import textwrap
from pathlib import Path

from app.core.scheduler_config import load_scheduler_config


def test_load_scheduler_config_reads_scheduler_section(tmp_path: Path):
    config_path = tmp_path / "config.yaml"
    config_path.write_text(
        textwrap.dedent(
            """
            scheduler:
              hour: 3
              minute: 15
              timezone: America/New_York
            """
        ),
        encoding="utf-8",
    )

    config = load_scheduler_config(config_path)

    assert config.hour == 3
    assert config.minute == 15
    assert config.timezone == "America/New_York"
