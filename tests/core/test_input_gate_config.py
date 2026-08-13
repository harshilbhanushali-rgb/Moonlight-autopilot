import pytest

from app.core.input_gate_config import load_input_gate_config


def write_config(tmp_path, body: str):
    path = tmp_path / "config.yaml"
    path.write_text(body, encoding="utf-8")
    return path


def test_loads_the_input_gate_section(tmp_path):
    path = write_config(
        tmp_path,
        "input_gate:\n  enabled: true\n  min_words: 300\n  require_client_speech: true\n",
    )

    config = load_input_gate_config(path)

    assert (config.enabled, config.min_words, config.require_client_speech) == (True, 300, True)


def test_a_missing_section_leaves_the_gate_off(tmp_path):
    # An absent section must not silently start excluding calls.
    path = write_config(tmp_path, "analyser:\n  batch_size: 10\n")

    config = load_input_gate_config(path)

    assert config.enabled is False


def test_a_negative_word_floor_is_rejected_at_load(tmp_path):
    # Same reasoning as the analyser's validated tunables: a bad value must fail
    # at startup rather than mid-run.
    path = write_config(tmp_path, "input_gate:\n  enabled: true\n  min_words: -1\n")

    with pytest.raises(ValueError, match="min_words"):
        load_input_gate_config(path)
