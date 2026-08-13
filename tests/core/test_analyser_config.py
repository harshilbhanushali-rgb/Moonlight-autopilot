import pytest

from app.core.analyser_config import (
    DEFAULT_CIRCUIT_BREAKER_CONSECUTIVE_FAILURES,
    DEFAULT_MAX_CONCURRENT_CALLS,
    DEFAULT_STALE_CLAIM_MINUTES,
    AnalyserConfig,
    load_analyser_config,
)


def test_loads_analyser_config_from_yaml(tmp_path):
    path = tmp_path / "config.yaml"
    path.write_text(
        """
analyser:
  gap_rubric_mode: fewshot
  scoring_prompt_version: v2
  batch_size: 25
  max_retries: 4
  stale_claim_minutes: 90
""",
        encoding="utf-8",
    )

    config = load_analyser_config(path)

    assert config == AnalyserConfig(
        gap_rubric_mode="fewshot",
        scoring_prompt_version="v2",
        batch_size=25,
        max_retries=4,
        stale_claim_minutes=90,
    )


def test_stale_claim_minutes_falls_back_to_the_default_when_absent(tmp_path):
    path = tmp_path / "config.yaml"
    path.write_text(
        """
analyser:
  gap_rubric_mode: fewshot
  scoring_prompt_version: v2
  batch_size: 25
  max_retries: 4
""",
        encoding="utf-8",
    )

    config = load_analyser_config(path)

    assert config.stale_claim_minutes == DEFAULT_STALE_CLAIM_MINUTES


def _write(tmp_path, extra: str = ""):
    path = tmp_path / "config.yaml"
    path.write_text(
        f"""
analyser:
  gap_rubric_mode: fewshot
  scoring_prompt_version: v2
  batch_size: 25
  max_retries: 4
{extra}""",
        encoding="utf-8",
    )
    return path


def test_circuit_breaker_threshold_falls_back_to_the_default_when_absent(tmp_path):
    config = load_analyser_config(_write(tmp_path))

    assert (
        config.circuit_breaker_consecutive_failures
        == DEFAULT_CIRCUIT_BREAKER_CONSECUTIVE_FAILURES
    )


def test_circuit_breaker_threshold_is_read_from_yaml(tmp_path):
    path = _write(tmp_path, "  circuit_breaker_consecutive_failures: 9\n")

    assert load_analyser_config(path).circuit_breaker_consecutive_failures == 9


def test_a_circuit_breaker_threshold_of_zero_is_allowed_as_the_off_switch(tmp_path):
    path = _write(tmp_path, "  circuit_breaker_consecutive_failures: 0\n")

    assert load_analyser_config(path).circuit_breaker_consecutive_failures == 0


@pytest.mark.parametrize("bad", [1, 2, 3, 4, -1])
def test_rejects_a_circuit_breaker_threshold_that_one_row_could_reach(tmp_path, bad):
    # A row has 4 steps, so 1..4 could be tripped by a single oversized
    # transcript failing all of its own steps — a per-call problem, not an
    # outage. Rejected at load time rather than surfacing mid-batch.
    path = _write(tmp_path, f"  circuit_breaker_consecutive_failures: {bad}\n")

    with pytest.raises(ValueError, match="circuit_breaker_consecutive_failures"):
        load_analyser_config(path)


def test_the_default_threshold_cannot_be_reached_by_one_row():
    assert DEFAULT_CIRCUIT_BREAKER_CONSECUTIVE_FAILURES > 4


def test_max_concurrent_calls_falls_back_to_the_default_when_absent(tmp_path):
    config = load_analyser_config(_write(tmp_path))

    assert config.max_concurrent_calls == DEFAULT_MAX_CONCURRENT_CALLS


def test_max_concurrent_calls_is_read_from_yaml(tmp_path):
    path = _write(tmp_path, "  max_concurrent_calls: 8\n")

    assert load_analyser_config(path).max_concurrent_calls == 8


def test_one_is_a_valid_max_concurrent_calls_meaning_fully_sequential(tmp_path):
    path = _write(tmp_path, "  max_concurrent_calls: 1\n")

    assert load_analyser_config(path).max_concurrent_calls == 1


@pytest.mark.parametrize("bad", [0, -1])
def test_rejects_a_max_concurrent_calls_that_would_hang_the_batch(tmp_path, bad):
    # 0 or negative builds an asyncio.Semaphore no coroutine can ever acquire,
    # so the batch would hang silently forever instead of failing. Caught at
    # load time, like the breaker threshold.
    path = _write(tmp_path, f"  max_concurrent_calls: {bad}\n")

    with pytest.raises(ValueError, match="max_concurrent_calls"):
        load_analyser_config(path)


def test_verification_batch_size_defaults_when_absent(tmp_path):
    path = _write(tmp_path, "")

    assert load_analyser_config(path).verification_batch_size == 5


@pytest.mark.parametrize("bad", [0, -1])
def test_rejects_a_verification_batch_size_that_would_skip_verification(tmp_path, bad):
    # 0 batches nothing, so every gap would go unverified while the pipeline
    # still looked wired up — the failure mode this whole check exists to stop.
    path = _write(tmp_path, f"  verification_batch_size: {bad}\n")

    with pytest.raises(ValueError, match="verification_batch_size"):
        load_analyser_config(path)


def test_the_checked_in_config_is_loadable():
    config = load_analyser_config()

    assert config.stale_claim_minutes > 0
    assert config.circuit_breaker_consecutive_failures > 4
    assert config.max_concurrent_calls >= 1
    assert config.verification_batch_size >= 1
