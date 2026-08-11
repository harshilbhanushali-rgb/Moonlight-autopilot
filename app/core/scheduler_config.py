from dataclasses import dataclass
from pathlib import Path

import yaml

_DEFAULT_CONFIG_PATH = Path(__file__).resolve().parent.parent.parent / "config.yaml"


@dataclass(frozen=True)
class SchedulerConfig:
    hour: int
    minute: int
    timezone: str


def load_scheduler_config(path: Path | None = None) -> SchedulerConfig:
    path = path or _DEFAULT_CONFIG_PATH
    raw = yaml.safe_load(path.read_text(encoding="utf-8"))
    section = raw["scheduler"]
    return SchedulerConfig(
        hour=section["hour"],
        minute=section["minute"],
        timezone=section["timezone"],
    )
