from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.cron import CronTrigger

from app.core.scheduler_config import load_scheduler_config
from app.services.scheduler.pipeline import run_daily_pipeline


def build_scheduler() -> BackgroundScheduler:
    """A thread-based scheduler: the fetcher/analyser pipeline is
    synchronous, blocking I/O, so it must not run on FastAPI's event loop
    thread — BackgroundScheduler runs jobs in its own worker thread."""
    config = load_scheduler_config()
    scheduler = BackgroundScheduler()
    scheduler.add_job(
        run_daily_pipeline,
        trigger=CronTrigger(hour=config.hour, minute=config.minute, timezone=config.timezone),
        id="daily_pipeline",
    )
    return scheduler
