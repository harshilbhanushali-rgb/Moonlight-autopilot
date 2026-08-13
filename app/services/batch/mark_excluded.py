"""Marks pre-existing `analysis` rows whose call the input gate now rejects.

One-off, not wired into the scheduler. Calls excluded from now on never get an
`analysis` row (`seed_missing_analysis_rows` skips them), but rows written before
the gate existed are already there — carrying a call type, a score and in one
case a Risk card for a recording with no conversation in it. This flips those to
`status = 'excluded'` so nothing downstream reads them as valid.

**It never touches the four output columns.** `call_type`, `call_score`,
`risk_gap_analysis` and `card_type` are the A/B baseline every rubric
measurement so far was computed against, and CLAUDE.md is explicit that they
must survive. Only `status` changes — it is not one of the compared outputs, and
keeping the outputs preserves the evidence of what the pipeline did wrong.

Targets are derived from `call_storage.excluded_reason` rather than a hardcoded
list, so this cannot drift from what the gate actually decided. Run the backfill
first with the gate enabled, so history carries its reasons.

    uv run python -m app.services.batch.mark_excluded --dry-run
    uv run python -m app.services.batch.mark_excluded
"""

import argparse
from dataclasses import dataclass

from sqlalchemy import select, update

from app.db.models import STATUS_EXCLUDED, Analysis, CallStorage
from app.db.session import get_session


@dataclass
class MarkSummary:
    already_marked: int = 0
    marked: int = 0


def mark_excluded_analyses(*, session, dry_run: bool = False) -> MarkSummary:
    rows = session.execute(
        select(
            Analysis.avoma_recording_id,
            Analysis.status,
            Analysis.call_type,
            Analysis.call_score,
            Analysis.card_type,
            CallStorage.excluded_reason,
        )
        .join(CallStorage, CallStorage.avoma_recording_id == Analysis.avoma_recording_id)
        .where(CallStorage.excluded_reason.isnot(None))
        .order_by(Analysis.id)
    ).all()

    summary = MarkSummary()
    to_mark = []
    for recording_id, status, call_type, call_score, card_type, reason in rows:
        if status == STATUS_EXCLUDED:
            summary.already_marked += 1
            continue
        to_mark.append(recording_id)
        print(
            f"  {recording_id[:8]}  {reason:<18} status {status!r} -> {STATUS_EXCLUDED!r}"
            f"  (keeping call_type={call_type!r} score={call_score!r} card={card_type!r})"
        )

    if to_mark and not dry_run:
        session.execute(
            update(Analysis)
            .where(Analysis.avoma_recording_id.in_(to_mark))
            .values(status=STATUS_EXCLUDED)
        )
        session.commit()

    summary.marked = len(to_mark)
    return summary


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(
        description="Mark analysis rows for calls the input gate excludes."
    )
    parser.add_argument("--dry-run", action="store_true", help="Report without writing.")
    args = parser.parse_args(argv)

    session = get_session()
    try:
        print("Analysis rows for excluded calls:")
        summary = mark_excluded_analyses(session=session, dry_run=args.dry_run)
    finally:
        session.close()

    verb = "would be marked" if args.dry_run else "marked"
    print(
        f"\n{summary.marked} {verb} excluded, "
        f"{summary.already_marked} already excluded. Output columns untouched."
    )


if __name__ == "__main__":
    main()
