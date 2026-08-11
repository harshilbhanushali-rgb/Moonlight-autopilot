from apscheduler.triggers.cron import CronTrigger

from app.core.scheduler_config import SchedulerConfig
from app.services.scheduler.scheduler import build_scheduler


def test_build_scheduler_adds_single_cron_job(monkeypatch):
    monkeypatch.setattr(
        "app.services.scheduler.scheduler.load_scheduler_config",
        lambda: SchedulerConfig(hour=2, minute=30, timezone="UTC"),
    )

    scheduler = build_scheduler()

    jobs = scheduler.get_jobs()
    assert len(jobs) == 1
    trigger = jobs[0].trigger
    assert isinstance(trigger, CronTrigger)
    fields = {f.name: str(f) for f in trigger.fields}
    assert fields["hour"] == "2"
    assert fields["minute"] == "30"
    assert str(trigger.timezone) == "UTC"
