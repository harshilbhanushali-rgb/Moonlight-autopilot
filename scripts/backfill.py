"""Re-fetches stored transcripts so they satisfy the current contract.

Needed because `Transcript.speakers` and `TranscriptTurn.speaker_id` are
required: the input gate has to be able to ask whether anyone on the client's
side actually spoke, and a transcript that cannot answer that is not stored.
Rows written before those fields existed therefore fail validation and must be
re-fetched — they cannot be backfilled from `call_storage` alone.

**Keyed on `call_storage`, deliberately not `moonlight_calls`.** 19 of the 51
stored calls no longer appear in Koushik's table, but Avoma is unaffected by
that churn: a phase-0 probe confirmed all 51 transcripts still return. Iterating
the client table would silently abandon those 19 and, with them, the baseline
every rubric measurement so far was computed against.

Also re-evaluates the input gate, so history gets `excluded_reason` too rather
than only calls fetched from now on.

    uv run python -m scripts.backfill --dry-run
    uv run python -m scripts.backfill [--limit N]
"""

import argparse
import logging
from dataclasses import dataclass

from sqlalchemy import select, update

from app.avoma.client import AvomaClient
from app.core.config import settings
from app.core.input_gate_config import InputGateConfig, load_input_gate_config
from app.db.models import CallStorage
from app.db.session import get_session
from app.domain.input_gate import evaluate_input_gate
from app.domain.transcript import Transcript
from app.services.fetcher.transform import TranscriptShapeError, transcript_to_storage_shape

logger = logging.getLogger(__name__)


@dataclass
class BackfillSummary:
    considered: int = 0
    updated: int = 0
    excluded: int = 0
    missing_from_avoma: int = 0
    malformed: int = 0

    def __str__(self) -> str:
        return (
            f"{self.considered} considered, {self.updated} updated "
            f"({self.excluded} of them excluded by the input gate), "
            f"{self.missing_from_avoma} no longer in Avoma, "
            f"{self.malformed} unusable shape"
        )


def backfill_transcripts(
    *,
    our_session,
    avoma_client,
    limit: int | None = None,
    input_gate_config: InputGateConfig | None = None,
    dry_run: bool = False,
) -> BackfillSummary:
    config = input_gate_config or InputGateConfig()
    summary = BackfillSummary()

    recording_ids = (
        our_session.execute(select(CallStorage.avoma_recording_id).order_by(CallStorage.id))
        .scalars()
        .all()
    )
    if limit is not None:
        recording_ids = recording_ids[:limit]

    for recording_id in recording_ids:
        summary.considered += 1
        try:
            transcript = avoma_client.get_transcript_by_meeting_uuid(recording_id)
        except Exception:
            # A row that cannot be recovered is left exactly as it was rather
            # than overwritten with anything partial.
            logger.exception("backfill: Avoma request failed for %s", recording_id)
            summary.missing_from_avoma += 1
            continue

        if transcript is None:
            logger.warning("backfill: Avoma no longer returns a transcript for %s", recording_id)
            summary.missing_from_avoma += 1
            continue

        try:
            stored_transcript = transcript_to_storage_shape(transcript)
        except TranscriptShapeError:
            logger.exception("backfill: %s cannot be stored in the current shape", recording_id)
            summary.malformed += 1
            continue

        verdict = evaluate_input_gate(Transcript.model_validate(stored_transcript), config)
        if not verdict.accepted:
            summary.excluded += 1
            logger.info(
                "backfill: %s would be excluded — %s (%s)",
                recording_id,
                verdict.reason.value if verdict.reason else "unknown",
                verdict.detail,
            )

        if dry_run:
            summary.updated += 1
            continue

        our_session.execute(
            update(CallStorage)
            .where(CallStorage.avoma_recording_id == recording_id)
            .values(
                transcript=stored_transcript,
                excluded_reason=verdict.reason.value if verdict.reason else None,
                excluded_detail=verdict.detail if not verdict.accepted else None,
            )
        )
        # Committed per row so a failure part-way through keeps the rows already
        # done, rather than losing the whole run's work.
        our_session.commit()
        summary.updated += 1

    return summary


def main(argv: list[str] | None = None) -> None:
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    parser = argparse.ArgumentParser(
        description="Re-fetch stored transcripts so they carry speaker identity."
    )
    parser.add_argument("--limit", type=int, default=None, help="Process at most N rows.")
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Report what would change without writing anything.",
    )
    args = parser.parse_args(argv)

    avoma_client = AvomaClient(base_url=settings.avoma_base_url, api_key=settings.avoma_api_key)
    our_session = get_session()
    try:
        summary = backfill_transcripts(
            our_session=our_session,
            avoma_client=avoma_client,
            limit=args.limit,
            input_gate_config=load_input_gate_config(),
            dry_run=args.dry_run,
        )
    finally:
        our_session.close()

    prefix = "Backfill dry run" if args.dry_run else "Backfill complete"
    print(f"{prefix}: {summary}")


if __name__ == "__main__":
    main()
