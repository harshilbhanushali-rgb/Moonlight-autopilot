from datetime import date

import pytest

from app.db.models import ScheduledRun
from app.db.session import SessionLocal
from app.services.scheduler import ledger

pytestmark = pytest.mark.integration


@pytest.fixture
def job_name():
    name = "test_job"
    yield name
    with SessionLocal() as session:
        session.query(ScheduledRun).filter_by(job_name=name).delete()
        session.commit()


def test_claim_run_wins_once_then_returns_none_for_same_day(job_name):
    run_date = date(2026, 1, 1)

    with SessionLocal() as session:
        first = ledger.claim_run(session, job_name, run_date)
    with SessionLocal() as session:
        second = ledger.claim_run(session, job_name, run_date)

    assert first is not None
    assert second is None


def test_claim_run_allows_a_new_claim_on_a_different_day(job_name):
    with SessionLocal() as session:
        day_one = ledger.claim_run(session, job_name, date(2026, 1, 1))
    with SessionLocal() as session:
        day_two = ledger.claim_run(session, job_name, date(2026, 1, 2))

    assert day_one is not None
    assert day_two is not None
    assert day_one != day_two


def test_mark_completed_sets_status_and_finished_at(job_name):
    with SessionLocal() as session:
        run_id = ledger.claim_run(session, job_name, date(2026, 1, 1))

    with SessionLocal() as session:
        ledger.mark_completed(session, run_id)

    with SessionLocal() as session:
        run = session.get(ScheduledRun, run_id)
        assert run.status == ledger.STATUS_COMPLETED
        assert run.finished_at is not None


def test_mark_failed_sets_status_and_error(job_name):
    with SessionLocal() as session:
        run_id = ledger.claim_run(session, job_name, date(2026, 1, 1))

    with SessionLocal() as session:
        ledger.mark_failed(session, run_id, "boom")

    with SessionLocal() as session:
        run = session.get(ScheduledRun, run_id)
        assert run.status == ledger.STATUS_FAILED
        assert run.error == "boom"
        assert run.finished_at is not None
