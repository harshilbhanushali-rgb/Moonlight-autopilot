import logging
from datetime import date

from app.db.session import get_session
from app.services.batch import run as batch_run
from app.services.fetcher import run as fetcher_run
from app.services.scheduler import ledger

logger = logging.getLogger(__name__)

JOB_NAME = "daily_pipeline"


def run_daily_pipeline(today: date | None = None) -> None:
    """Runs the fetcher then the analyser for today, claimed via the
    scheduled_run ledger so only one replica executes per day. If the
    fetcher step fails, the analyser step is skipped for this cycle —
    both are retried together on tomorrow's run."""
    run_date = today or date.today()

    session = get_session()
    try:
        run_id = ledger.claim_run(session, JOB_NAME, run_date)
    finally:
        session.close()

    if run_id is None:
        logger.info("Daily pipeline for %s already claimed by another instance, skipping.", run_date)
        return

    try:
        fetcher_run.main(argv=[])
    except Exception as exc:
        logger.exception("Fetcher step failed for %s; skipping analyser this cycle.", run_date)
        _mark_failed(run_id, str(exc))
        return

    try:
        batch_run.main()
    except Exception as exc:
        logger.exception("Analyser step failed for %s.", run_date)
        _mark_failed(run_id, str(exc))
        return

    session = get_session()
    try:
        ledger.mark_completed(session, run_id)
    finally:
        session.close()


def _mark_failed(run_id: int, error: str) -> None:
    session = get_session()
    try:
        ledger.mark_failed(session, run_id, error)
    finally:
        session.close()
