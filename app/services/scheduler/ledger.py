from datetime import date, datetime, timezone

from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session

from app.db.models import ScheduledRun

STATUS_RUNNING = "running"
STATUS_COMPLETED = "completed"
STATUS_FAILED = "failed"


def claim_run(session: Session, job_name: str, run_date: date) -> int | None:
    """Attempts to claim job_name/run_date via INSERT ... ON CONFLICT DO
    NOTHING. Returns the new row's id if this call won the claim, or None if
    another instance already claimed it (racing replicas hitting the unique
    (job_name, run_date) constraint at the same scheduled time)."""
    result = session.execute(
        insert(ScheduledRun)
        .values(job_name=job_name, run_date=run_date, status=STATUS_RUNNING)
        .on_conflict_do_nothing(index_elements=["job_name", "run_date"])
        .returning(ScheduledRun.id)
    )
    session.commit()
    row = result.first()
    return row[0] if row else None


def mark_completed(session: Session, run_id: int) -> None:
    run = session.get(ScheduledRun, run_id)
    run.status = STATUS_COMPLETED
    run.finished_at = datetime.now(timezone.utc)
    session.commit()


def mark_failed(session: Session, run_id: int, error: str) -> None:
    run = session.get(ScheduledRun, run_id)
    run.status = STATUS_FAILED
    run.finished_at = datetime.now(timezone.utc)
    run.error = error
    session.commit()
