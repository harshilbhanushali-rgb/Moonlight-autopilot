import logging
from datetime import datetime, timedelta, timezone

from sqlalchemy import and_, func, or_, select, text, update
from sqlalchemy.orm import Session

from app.core.analyser_config import DEFAULT_STALE_CLAIM_MINUTES
from app.db.models import STATUS_FAILED, STATUS_PENDING, STATUS_PROCESSING, Analysis, CallStorage
from app.domain.transcript import Transcript
from app.prompts.registry import PromptFile
from app.services.batch.orchestrator import AnalysisRecord
from app.services.prompt_versions import resolve_prompt_version_id

logger = logging.getLogger(__name__)


def seed_missing_analysis_rows(session: Session) -> int:
    """Inserts a pending `analysis` row for every `call_storage` row that
    doesn't have one yet. call_storage itself is never written to here —
    it's the (deferred) Call Fetcher's table; this only reads it."""
    result = session.execute(
        text(
            """
            INSERT INTO analysis (
                avoma_recording_id, call_type_status, scoring_status,
                gap_status, card_type_status, status, retry_count
            )
            SELECT cs.avoma_recording_id, 'pending', 'pending', 'pending', 'pending', 'pending', 0
            FROM call_storage cs
            WHERE NOT EXISTS (
                SELECT 1 FROM analysis a WHERE a.avoma_recording_id = cs.avoma_recording_id
            )
            ON CONFLICT (avoma_recording_id) DO NOTHING
            """
        )
    )
    session.commit()
    return result.rowcount


def claim_rows(
    session: Session,
    limit: int,
    *,
    stale_claim_minutes: int = DEFAULT_STALE_CLAIM_MINUTES,
) -> list[Analysis]:
    """Atomically claims up to `limit` retryable analysis rows via
    SELECT ... FOR UPDATE SKIP LOCKED, so concurrent/overlapping batch runs
    never double-process the same row. Commits immediately after claiming so
    the lock isn't held across the (potentially slow) LLM calls that follow.

    Retryable means pending, failed, or *stale-processing*. That last case
    exists because committing the claim immediately is exactly what makes a
    crashed run leave rows in `processing` with nobody holding them: the row
    is then invisible to the retry/dead-letter logic and would never be
    picked up again. `analysis.updated_at` carries `onupdate=func.now()` and
    the claim below is an `update()`, so the claim itself timestamps the row —
    a claim older than `stale_claim_minutes` is assumed dead.

    The cutoff is evaluated with the database's clock (`now()`), matching the
    clock that wrote `updated_at`, so a skewed app-server clock can't make
    live claims look stale.
    """
    reclaim_cutoff = func.now() - timedelta(minutes=stale_claim_minutes)
    ids = (
        session.execute(
            select(Analysis.id)
            .where(
                or_(
                    Analysis.status.in_([STATUS_PENDING, STATUS_FAILED]),
                    and_(
                        Analysis.status == STATUS_PROCESSING,
                        Analysis.updated_at < reclaim_cutoff,
                    ),
                )
            )
            .order_by(Analysis.id)
            .limit(limit)
            .with_for_update(skip_locked=True)
        )
        .scalars()
        .all()
    )
    if not ids:
        return []

    reclaimed = (
        session.execute(
            select(Analysis.avoma_recording_id).where(
                Analysis.id.in_(ids), Analysis.status == STATUS_PROCESSING
            )
        )
        .scalars()
        .all()
    )
    if reclaimed:
        logger.warning(
            "Reclaiming %d analysis row(s) whose claim is older than %d minutes "
            "(a previous run almost certainly died mid-batch): %s",
            len(reclaimed),
            stale_claim_minutes,
            ", ".join(reclaimed),
        )

    session.execute(update(Analysis).where(Analysis.id.in_(ids)).values(status=STATUS_PROCESSING))
    session.commit()

    return session.execute(select(Analysis).where(Analysis.id.in_(ids))).scalars().all()


def release_claims(session: Session, avoma_recording_ids: list[str]) -> int:
    """Hands back rows this run claimed but never attempted.

    Used when the circuit breaker stops a batch early. Without it those rows sit
    in `processing` until `stale_claim_minutes` elapses — correct eventually,
    but stale reclamation exists for runs that *died*, and a breaker trip is a
    controlled stop that should clean up after itself. Releasing also means an
    operator can re-run immediately once the gateway is back, instead of
    waiting out the staleness window.

    Released to `pending`: the per-step statuses, errors and `retry_count` are
    untouched (these rows were never attempted), and `claim_rows` treats
    `pending` and `failed` identically for eligibility, so nothing is lost by
    not reconstructing the previous row-level status.
    """
    if not avoma_recording_ids:
        return 0

    result = session.execute(
        update(Analysis)
        .where(
            Analysis.avoma_recording_id.in_(avoma_recording_ids),
            Analysis.status == STATUS_PROCESSING,
        )
        .values(status=STATUS_PENDING)
    )
    session.commit()
    return result.rowcount


def get_call_storage_map(session: Session, avoma_recording_ids: list[str]) -> dict[str, CallStorage]:
    rows = (
        session.execute(
            select(CallStorage).where(CallStorage.avoma_recording_id.in_(avoma_recording_ids))
        )
        .scalars()
        .all()
    )
    return {row.avoma_recording_id: row for row in rows}


def render_transcript_text(transcript: dict) -> str:
    """Validates call_storage.transcript against its contract and flattens it
    to the text the LLM sees.

    Validated rather than read with `.get()` defaults: a transcript missing
    turn timestamps used to render as timestamp-free text, which left the gap
    step guessing `mm:ss` values. A row that doesn't satisfy the contract now
    fails the step visibly instead.
    """
    return Transcript.model_validate(transcript).render_for_prompt()


def analysis_row_to_record_dict(row: Analysis) -> dict:
    """Shapes a DB row into the plain dict app.services.batch.orchestrator's
    advance_analysis expects as its `record` argument."""
    return {
        "avoma_recording_id": row.avoma_recording_id,
        "call_type": row.call_type,
        "call_score": row.call_score,
        "risk_gap_analysis": row.risk_gap_analysis,
        "card_type": row.card_type,
        "call_type_status": row.call_type_status,
        "scoring_status": row.scoring_status,
        "gap_status": row.gap_status,
        "card_type_status": row.card_type_status,
        "call_type_error": row.call_type_error,
        "scoring_error": row.scoring_error,
        "gap_error": row.gap_error,
        "card_type_error": row.card_type_error,
        "retry_count": row.retry_count,
        "call_type_retry_count": row.call_type_retry_count,
        "scoring_retry_count": row.scoring_retry_count,
        "gap_retry_count": row.gap_retry_count,
        "card_type_retry_count": row.card_type_retry_count,
        "dead_letter_at": row.dead_letter_at,
    }


def _serialize_gaps(gaps) -> list[dict] | None:
    if gaps is None:
        return None
    return [
        {
            "theme": g.theme,
            "evidence_type": g.evidence_type,
            "evidence": g.evidence,
            "timestamp": g.timestamp,
            "confidence": g.confidence,
        }
        for g in gaps
    ]


_PROMPT_VERSION_COLUMNS = (
    ("call_type_prompt_version_id", "call_type_prompt_hash"),
    ("scoring_prompt_version_id", "scoring_prompt_hash"),
    ("gap_rubric_version_id", "gap_rubric_hash"),
    ("card_type_prompt_version_id", "card_type_prompt_hash"),
)


def _prompt_version_values(
    session: Session, record: AnalysisRecord, prompts_by_hash: dict[str, PromptFile]
) -> dict[str, int]:
    """Turns the record's per-step content hashes into prompt_versions ids.

    Only steps that succeeded on this pass carry a hash, so only those get a
    column here. Every other step is *omitted from the UPDATE entirely* rather
    than set to NULL — because persist_analysis_result upserts in place, a
    partial re-run (a step already `processed`, or call_type unknown so
    scoring/gap were skipped) would otherwise wipe the version id an earlier
    pass recorded correctly.
    """
    values: dict[str, int] = {}
    for column, hash_attr in _PROMPT_VERSION_COLUMNS:
        content_hash = getattr(record, hash_attr)
        if content_hash is None:
            continue
        prompt = prompts_by_hash.get(content_hash)
        if prompt is None:
            # The prompts the record's steps used and the prompts offered for
            # its call_type have diverged. Not a case to paper over with a
            # missing id: it means the caller passed a lookup that doesn't
            # describe this run.
            raise KeyError(
                f"no PromptFile supplied for {hash_attr}={content_hash!r} "
                f"(call {record.avoma_recording_id})"
            )
        values[column] = resolve_prompt_version_id(session, prompt)
    return values


def persist_analysis_result(
    session: Session,
    record: AnalysisRecord,
    *,
    prompts_by_hash: dict[str, PromptFile] | None = None,
) -> None:
    dead_letter_at = record.dead_letter_at
    if dead_letter_at is True:
        dead_letter_at = datetime.now(timezone.utc)

    prompt_version_values = _prompt_version_values(session, record, prompts_by_hash or {})

    session.execute(
        update(Analysis)
        .where(Analysis.avoma_recording_id == record.avoma_recording_id)
        .values(
            **prompt_version_values,
            call_type=record.call_type.value if record.call_type else None,
            call_score=record.call_score.value if record.call_score else None,
            risk_gap_analysis=_serialize_gaps(record.risk_gap_analysis),
            card_type=record.card_type.value if record.card_type else None,
            call_type_status=record.call_type_status,
            scoring_status=record.scoring_status,
            gap_status=record.gap_status,
            card_type_status=record.card_type_status,
            call_type_error=record.call_type_error,
            scoring_error=record.scoring_error,
            gap_error=record.gap_error,
            card_type_error=record.card_type_error,
            status=record.overall_status,
            retry_count=record.retry_count,
            call_type_retry_count=record.call_type_retry_count,
            scoring_retry_count=record.scoring_retry_count,
            gap_retry_count=record.gap_retry_count,
            card_type_retry_count=record.card_type_retry_count,
            dead_letter_at=dead_letter_at,
        )
    )
    session.commit()
