"""One-off repair: reverts `analysis` rows that an integration test wrote with
StubLLMClient's canned answers back to `pending`.

**What happened.** `process_batch` claims any claimable row — that is its job —
and `tests/integration/test_batch_processor.py` calls it with `limit=100`
against the real Neon database. On 2026-08-13 five real calls were sitting in
`pending`, so an integration run analysed them with fixture constants and marked
them `processed`: `call_type=Demo`, `call_score=High`, `card_type=Coaching`, no
gaps, and a breakdown of ten categories literally named `Category 1..10`.
Nothing errored. The rows would have been read as genuine analysis.

The recurrence is prevented by `tests/integration/conftest.py`, which snapshots
and restores every claimable row around each integration test. This script
cleans up the rows written before that existed.

**The fingerprint is the category names.** All six real rubrics use the business
team's wording, so no genuine breakdown contains a category called
`Category 7`. A row is only touched when *every* category matches that pattern —
matching on `Demo`/`High` would risk reverting real analysis that happens to
agree with the fixture.

Rows are reset to `pending` rather than deleted so the analyser picks them up on
its next pass and scores them properly.

    uv run python -m scripts.revert_stub_written_rows            # dry run
    uv run python -m scripts.revert_stub_written_rows --apply
"""

from __future__ import annotations

import argparse
import re

from sqlalchemy import select, update

from app.db.models import STATUS_PENDING, Analysis
from app.db.session import SessionLocal

_STUB_CATEGORY_NAME = re.compile(r"Category \d+")

# Everything a pass writes. Reset together, so a reverted row is indistinguishable
# from one the fetcher has just seeded.
_CLEARED = {
    "call_type": None,
    "call_score": None,
    "call_score_categories": None,
    "risk_gap_analysis": None,
    "card_type": None,
    "status": STATUS_PENDING,
    "call_type_status": STATUS_PENDING,
    "scoring_status": STATUS_PENDING,
    "gap_status": STATUS_PENDING,
    "card_type_status": STATUS_PENDING,
    "call_type_error": None,
    "scoring_error": None,
    "gap_error": None,
    "card_type_error": None,
    "retry_count": 0,
    "dead_letter_at": None,
    "call_type_retry_count": 0,
    "scoring_retry_count": 0,
    "gap_retry_count": 0,
    "card_type_retry_count": 0,
    "call_type_prompt_version_id": None,
    "scoring_prompt_version_id": None,
    "gap_rubric_version_id": None,
    "card_type_prompt_version_id": None,
    "gap_verification_dialogue_version_id": None,
    "gap_verification_explanation_version_id": None,
}


def is_stub_written(categories) -> bool:
    return bool(categories) and all(
        _STUB_CATEGORY_NAME.fullmatch(item.get("name") or "") for item in categories
    )


def find_stub_rows(session):
    rows = session.execute(
        select(
            Analysis.id,
            Analysis.avoma_recording_id,
            Analysis.call_type,
            Analysis.call_score,
            Analysis.call_score_categories,
        ).where(Analysis.call_score_categories.is_not(None))
    ).all()
    return [row for row in rows if is_stub_written(row.call_score_categories)]


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--apply", action="store_true", help="write the changes; omit for a dry run"
    )
    args = parser.parse_args()

    with SessionLocal() as session:
        rows = find_stub_rows(session)
        if not rows:
            print("no stub-written rows found")
            return

        print(f"{len(rows)} stub-written row(s):")
        for row in rows:
            names = ", ".join(c["name"] for c in row.call_score_categories[:3])
            print(
                f"  id={row.id:<6}{row.avoma_recording_id[:12]}  "
                f"{row.call_type}/{row.call_score}  categories: {names}, ..."
            )

        if not args.apply:
            print("\ndry run — re-run with --apply to reset these rows to pending")
            return

        session.execute(
            update(Analysis).where(Analysis.id.in_([r.id for r in rows])).values(**_CLEARED)
        )
        session.commit()
        print(f"\nreset {len(rows)} row(s) to pending")


if __name__ == "__main__":
    main()
