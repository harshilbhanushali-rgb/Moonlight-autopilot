"""Integration-test safety net for the shared `analysis` table.

`process_batch` claims **any** claimable row — that is its job — and the batch
tests call it with `limit=100` against the real Neon database. So a test that
seeds one call also analyses whatever real calls happen to be sitting in
`pending`, and writes the StubLLMClient's canned answers over them as if they
were genuine results.

That is not hypothetical. On 2026-08-13 an integration run stamped five real
calls with `call_type=Demo`, `call_score=High`, `card_type=Coaching`, no gaps,
and a ten-row breakdown of `Category 1..10 / "said so"` — every value a fixture
constant, none of it derived from the transcripts. Nothing failed; the rows
simply became `processed` and would have been read as real analysis.

This fixture takes a full snapshot of every claimable row before each
integration test and restores it verbatim afterwards. Restoring the whole row
rather than resetting to `pending` matters: a `failed` row can legitimately hold
partial output from an earlier pass, and blanking it would destroy real work
just as surely as the stub did.
"""

import pytest
from sqlalchemy import inspect, select, update

from app.db.models import (
    STATUS_FAILED,
    STATUS_PENDING,
    STATUS_PROCESSING,
    Analysis,
)
from app.db.session import SessionLocal

# The states claim_rows will pick up. `processing` is included because a claim
# older than analyser.stale_claim_minutes is reclaimable, and an integration run
# can outlive that window on a slow day.
CLAIMABLE = (STATUS_PENDING, STATUS_FAILED, STATUS_PROCESSING)


def _snapshot():
    columns = [c.key for c in inspect(Analysis).mapper.column_attrs]
    with SessionLocal() as session:
        rows = session.execute(
            select(Analysis).where(Analysis.status.in_(CLAIMABLE))
        ).scalars().all()
        return [{c: getattr(row, c) for c in columns} for row in rows]


@pytest.fixture(autouse=True)
def protect_claimable_analysis_rows():
    """Restores any real row an unbounded `process_batch` swept up."""
    before = _snapshot()
    yield
    if not before:
        return

    with SessionLocal() as session:
        for row in before:
            values = {k: v for k, v in row.items() if k != "id"}
            session.execute(update(Analysis).where(Analysis.id == row["id"]).values(**values))
        session.commit()
