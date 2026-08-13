"""The live scoring prompt version is named, not inferred from the filename.

`PromptRegistry.latest()` takes the highest version label, so adding an
experimental `v3.txt` under app/prompts/scoring/ silently repointed the
production analyser at an unvalidated prompt — which is exactly what happened
while A/B-ing Phase B, and exactly the hazard CLAUDE.md already records for the
gap-verification prompts. An A/B needs the candidate on disk; production must
not follow it there.
"""

import pytest

from app.core.analyser_config import load_analyser_config


def _write(tmp_path, body: str):
    path = tmp_path / "config.yaml"
    path.write_text(body, encoding="utf-8")
    return path


BASE = """
analyser:
  gap_rubric_mode: descriptiononly
  batch_size: 10
  max_retries: 3
"""


def test_the_scoring_version_can_be_pinned(tmp_path):
    config = load_analyser_config(_write(tmp_path, BASE + "  scoring_prompt_version: v2\n"))

    assert config.scoring_prompt_version == "v2"


def test_an_unpinned_config_is_rejected_rather_than_falling_back_to_latest(tmp_path):
    """Defaulting to "highest label wins" is what caused the problem. An absent
    setting is a config that has not decided, and it fails loudly at load."""
    with pytest.raises(ValueError, match="scoring_prompt_version"):
        load_analyser_config(_write(tmp_path, BASE))


def test_a_blank_scoring_version_is_rejected(tmp_path):
    with pytest.raises(ValueError, match="scoring_prompt_version"):
        load_analyser_config(_write(tmp_path, BASE + "  scoring_prompt_version: ''\n"))
