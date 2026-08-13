from dataclasses import dataclass
from pathlib import Path

import yaml

_DEFAULT_CONFIG_PATH = Path(__file__).resolve().parent.parent.parent / "config.yaml"

# How long a row may sit in `processing` before its claim is assumed dead and
# the row becomes claimable again. See config.yaml for how this number is
# derived; it must comfortably exceed a whole batch's worst-case duration,
# because every row in a batch shares one claim timestamp.
DEFAULT_STALE_CLAIM_MINUTES = 360

# Consecutive gateway-unreachable failures before the analyser abandons a batch.
#
# A single row can contribute at most 4 step failures (one per analyser step),
# so this is deliberately ABOVE 4: tripping must require failures spanning at
# least two different rows. One pathologically large transcript timing out on
# all four of its own steps is a per-call problem, not an outage, and must not
# halt the batch. 6 is the smallest value that guarantees a second row is
# involved. 0 (or less) disables the breaker.
DEFAULT_CIRCUIT_BREAKER_CONSECUTIVE_FAILURES = 6

# How many calls the analyser has under analysis at once. Each one still runs
# its four steps sequentially, so this is also the ceiling on concurrent
# requests to the LLM gateway. Kept modest by default because the gateway's
# rate limits are not documented anywhere we control — raise it deliberately
# while watching for HTTP 429s, not speculatively.
DEFAULT_MAX_CONCURRENT_CALLS = 4

# How many gaps are judged in one entailment-verification request. The observed
# maximum on a single call is 5 gaps (mean 1.9), so 5 means one verification
# request per kind of gap for essentially every call. Raising it changes
# nothing in practice; lowering it costs extra round trips. Must be >= 1.
DEFAULT_VERIFICATION_BATCH_SIZE = 5


@dataclass(frozen=True)
class AnalyserConfig:
    gap_rubric_mode: str
    batch_size: int
    max_retries: int
    # Which app/prompts/scoring/<call_type>/<label>.txt the analyser runs.
    #
    # Named rather than resolved by PromptRegistry.latest(), which takes the
    # highest version label: dropping a candidate `v3.txt` in for an A/B
    # otherwise repoints production at an unvalidated prompt with no diff to
    # the analyser and nothing to notice. That is not hypothetical — it
    # happened while A/B-ing Phase B of the call-score redesign, and CLAUDE.md
    # already records the same hazard for the gap-verification prompts.
    #
    # No default, deliberately. A missing value means the config has not
    # decided which prompt scores every call, and that should stop the run
    # rather than quietly pick the newest file on disk.
    scoring_prompt_version: str = ""
    stale_claim_minutes: int = DEFAULT_STALE_CLAIM_MINUTES
    circuit_breaker_consecutive_failures: int = DEFAULT_CIRCUIT_BREAKER_CONSECUTIVE_FAILURES
    max_concurrent_calls: int = DEFAULT_MAX_CONCURRENT_CALLS
    verification_batch_size: int = DEFAULT_VERIFICATION_BATCH_SIZE


def load_analyser_config(path: Path | None = None) -> AnalyserConfig:
    path = path or _DEFAULT_CONFIG_PATH
    raw = yaml.safe_load(path.read_text(encoding="utf-8"))
    section = raw["analyser"]

    breaker_threshold = section.get(
        "circuit_breaker_consecutive_failures", DEFAULT_CIRCUIT_BREAKER_CONSECUTIVE_FAILURES
    )
    # Rejected at load time rather than mid-batch: a value in 1..4 is reachable
    # by one oversized transcript failing all of its own steps, so it would
    # halt healthy batches on a per-call problem. 0 disables the breaker.
    if breaker_threshold != 0 and breaker_threshold <= 4:
        raise ValueError(
            "analyser.circuit_breaker_consecutive_failures must be 0 (disabled) or "
            f"greater than 4 (one row's step count), got {breaker_threshold!r}"
        )

    max_concurrent_calls = section.get("max_concurrent_calls", DEFAULT_MAX_CONCURRENT_CALLS)
    # Rejected at load time for the same reason as the breaker threshold: 0 or a
    # negative value builds an asyncio.Semaphore that no coroutine can ever
    # acquire, so the batch would hang silently instead of failing.
    if max_concurrent_calls < 1:
        raise ValueError(
            "analyser.max_concurrent_calls must be at least 1, "
            f"got {max_concurrent_calls!r}"
        )

    verification_batch_size = section.get(
        "verification_batch_size", DEFAULT_VERIFICATION_BATCH_SIZE
    )
    # 0 would batch nothing and silently skip every gap's verification, which
    # looks identical to verification being wired up and working.
    if verification_batch_size < 1:
        raise ValueError(
            "analyser.verification_batch_size must be at least 1, "
            f"got {verification_batch_size!r}"
        )

    scoring_prompt_version = (section.get("scoring_prompt_version") or "").strip()
    if not scoring_prompt_version:
        raise ValueError(
            "analyser.scoring_prompt_version must name the scoring prompt label to "
            "run (e.g. 'v2'). It has no default on purpose: falling back to the "
            "highest label on disk means adding a candidate prompt for an A/B "
            "silently repoints production at it."
        )

    return AnalyserConfig(
        scoring_prompt_version=scoring_prompt_version,
        gap_rubric_mode=section["gap_rubric_mode"],
        batch_size=section["batch_size"],
        max_retries=section["max_retries"],
        stale_claim_minutes=section.get("stale_claim_minutes", DEFAULT_STALE_CLAIM_MINUTES),
        circuit_breaker_consecutive_failures=breaker_threshold,
        max_concurrent_calls=max_concurrent_calls,
        verification_batch_size=verification_batch_size,
    )
