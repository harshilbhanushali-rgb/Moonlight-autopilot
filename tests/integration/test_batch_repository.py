"""Stale-claim reclamation in the AI Analyser's row claiming.

claim_rows sets `processing` and commits immediately so the row lock isn't
held across slow LLM calls. That means a run that dies mid-batch leaves up to
`batch_size` rows in `processing` forever — invisible to the retry and
dead-letter logic, because claim_rows only ever looked at pending/failed.
These tests pin the reclamation window, and the `updated_at` refresh the
window depends on.
"""

import uuid
from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import select

from app.db.models import (
    STATUS_FAILED,
    STATUS_PENDING,
    STATUS_PROCESSED,
    STATUS_PROCESSING,
    Analysis,
)
from app.db.session import SessionLocal
from app.services.batch.repository import claim_rows, release_claims

pytestmark = pytest.mark.integration


@pytest.fixture
def seeded_ids():
    created: list[str] = []

    def seed(*, status: str, age: timedelta) -> str:
        recording_id = f"test-claim-{uuid.uuid4()}"
        claimed_at = datetime.now(timezone.utc) - age
        with SessionLocal() as session:
            session.add(
                Analysis(
                    avoma_recording_id=recording_id,
                    status=status,
                    call_type_status=STATUS_PENDING,
                    scoring_status=STATUS_PENDING,
                    gap_status=STATUS_PENDING,
                    card_type_status=STATUS_PENDING,
                    retry_count=0,
                    created_at=claimed_at,
                    updated_at=claimed_at,
                )
            )
            session.commit()
        created.append(recording_id)
        return recording_id

    yield seed

    with SessionLocal() as session:
        session.query(Analysis).filter(Analysis.avoma_recording_id.in_(created)).delete(
            synchronize_session=False
        )
        session.commit()


def claimed_ids(stale_claim_minutes: int) -> set[str]:
    with SessionLocal() as session:
        rows = claim_rows(session, 500, stale_claim_minutes=stale_claim_minutes)
        return {row.avoma_recording_id for row in rows}


def test_a_stale_processing_row_is_reclaimed_but_a_fresh_one_is_left_alone(seeded_ids):
    stale = seeded_ids(status=STATUS_PROCESSING, age=timedelta(minutes=60))
    fresh = seeded_ids(status=STATUS_PROCESSING, age=timedelta(seconds=0))

    ids = claimed_ids(stale_claim_minutes=30)

    assert stale in ids
    assert fresh not in ids


def test_pending_and_failed_rows_are_still_claimed_regardless_of_age(seeded_ids):
    pending = seeded_ids(status=STATUS_PENDING, age=timedelta(seconds=0))
    failed = seeded_ids(status=STATUS_FAILED, age=timedelta(seconds=0))

    ids = claimed_ids(stale_claim_minutes=30)

    assert {pending, failed} <= ids


def test_a_processed_row_is_never_claimed(seeded_ids):
    processed = seeded_ids(status=STATUS_PROCESSED, age=timedelta(days=30))

    ids = claimed_ids(stale_claim_minutes=30)

    assert processed not in ids


def status_of(recording_id: str) -> str:
    with SessionLocal() as session:
        return session.execute(
            select(Analysis.status).where(Analysis.avoma_recording_id == recording_id)
        ).scalar_one()


def test_release_claims_hands_back_a_claimed_row_immediately(seeded_ids):
    """When the circuit breaker stops a batch, the rows it never attempted must
    not wait out the staleness window before anyone can retry them."""
    row_id = seeded_ids(status=STATUS_PROCESSING, age=timedelta(seconds=0))

    # Fresh claim, so reclamation would not touch it.
    assert row_id not in claimed_ids(stale_claim_minutes=30)

    with SessionLocal() as session:
        assert release_claims(session, [row_id]) == 1

    assert status_of(row_id) == STATUS_PENDING
    assert row_id in claimed_ids(stale_claim_minutes=30)


def test_release_claims_only_touches_rows_still_processing(seeded_ids):
    """A row this run already finished must keep its outcome — releasing is for
    rows that were claimed and never attempted."""
    processed = seeded_ids(status=STATUS_PROCESSED, age=timedelta(seconds=0))
    failed = seeded_ids(status=STATUS_FAILED, age=timedelta(seconds=0))
    processing = seeded_ids(status=STATUS_PROCESSING, age=timedelta(seconds=0))

    with SessionLocal() as session:
        assert release_claims(session, [processed, failed, processing]) == 1

    assert status_of(processed) == STATUS_PROCESSED
    assert status_of(failed) == STATUS_FAILED
    assert status_of(processing) == STATUS_PENDING


def test_release_claims_with_no_ids_is_a_no_op():
    with SessionLocal() as session:
        assert release_claims(session, []) == 0


def test_claiming_refreshes_updated_at_so_the_claim_window_restarts(seeded_ids):
    """The whole design rests on claim_rows' bulk UPDATE bumping updated_at
    via `onupdate=func.now()` — without that there'd be no claim timestamp
    and a just-claimed old row would look instantly stale."""
    stale = seeded_ids(status=STATUS_PROCESSING, age=timedelta(minutes=60))

    assert stale in claimed_ids(stale_claim_minutes=30)

    with SessionLocal() as session:
        row = session.execute(
            select(Analysis).where(Analysis.avoma_recording_id == stale)
        ).scalar_one()
        assert row.status == STATUS_PROCESSING
        assert datetime.now(timezone.utc) - row.updated_at < timedelta(minutes=5)

    assert stale not in claimed_ids(stale_claim_minutes=30)
