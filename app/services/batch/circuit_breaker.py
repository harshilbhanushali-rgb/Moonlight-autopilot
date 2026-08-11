"""Stops the analyser early when the LLM gateway is unreachable.

Per-step and per-row failure isolation (see `orchestrator` and `processor`)
deliberately keep a batch going when individual calls fail. The cost is that a
*dead* gateway no longer aborts on the first row — the batch grinds through
every remaining row, spending `llm_gateway.timeout_seconds x (max_retries + 1)`
per step to learn the same thing each time. For the current settings that's
~2.7h of pure timeouts to produce a batch of rows that all failed identically.

This breaker is the missing "every step of every row is failing the same way,
stop" signal. It counts *consecutive transport* failures only, because that is
the sole evidence that the gateway itself is gone:

- An `openai.APIError` (timeout, connection failure, HTTP 5xx) means we could
  not get an answer out of the gateway. That counts.
- An `LLMOutputError` means the gateway *answered* and we rejected the content.
  That is a live gateway, so it RESETS the count — treating it as a failure
  would let a run of malformed responses halt a perfectly healthy batch.
"""

import logging

logger = logging.getLogger(__name__)

# The default threshold lives in app/core/analyser_config.py with the rest of the
# `analyser:` defaults, so this module imports nothing from the app and core
# stays a leaf. See that file for why it must exceed one row's 4 steps.


class GatewayUnavailableError(Exception):
    """The gateway looks down, so the batch stopped early rather than burning
    the remaining rows against it.

    Raised by `process_batch` *after* it has released the rows it claimed but
    never attempted, so nothing is left stranded in `processing`. It propagates
    out of `batch.run.main()` on purpose: the scheduler marks the day's
    `scheduled_run` row `failed`, which is the honest outcome — the run did not
    do its job.
    """


class CircuitBreaker:
    """Tracks consecutive gateway-unreachable failures within one batch run.

    One instance per `process_batch` call, never shared across runs. It needs no
    locking despite the concurrency there: the analyser's coroutines all run on
    a single event-loop thread, and every mutating method below is straight-line
    code with no `await` in it, so no increment can interleave with another.

    What concurrency *does* change is the meaning of "consecutive". With several
    calls in flight, the failures reaching this counter interleave across them,
    so the count reflects the order answers came back rather than one call's own
    run of bad luck. That is tolerable for the question being asked — "is
    everything failing the same way right now?" — but it means the threshold is
    a heuristic, not an exact count of anything, and a batch may attempt up to
    `max_concurrent_calls - 1` more rows after the count is reached, because
    rows already in flight are allowed to finish.
    """

    def __init__(self, threshold: int):
        self._threshold = threshold
        self._consecutive_failures = 0
        self._last_error: str | None = None
        self._gateway_proved_alive = False

    @property
    def enabled(self) -> bool:
        """A threshold of 0 (or less) disables the breaker entirely, restoring
        the grind-through-everything behaviour for anyone who wants it."""
        return self._threshold > 0

    @property
    def threshold(self) -> int:
        return self._threshold

    @property
    def consecutive_failures(self) -> int:
        return self._consecutive_failures

    @property
    def last_error(self) -> str | None:
        return self._last_error

    @property
    def is_open(self) -> bool:
        return self.enabled and self._consecutive_failures >= self._threshold

    @property
    def gateway_proved_alive(self) -> bool:
        """Has the gateway answered at least once during this run?

        Decides whether a transport failure spends a step's retry budget. If
        nothing has answered yet, the gateway is plausibly down and the step is
        an innocent bystander — charging it would dead-letter healthy calls
        after a few days of outage. If something HAS answered, then a failure
        specific to this step is the step's own problem and must be able to
        exhaust its budget, or a permanently broken step would retry silently
        forever.

        Latching (never reset to False) is deliberate: it asks "was the gateway
        up at any point in this batch", which is the outage-versus-not question.
        """
        return self._gateway_proved_alive

    def record_gateway_reachable(self) -> None:
        """The gateway answered — whatever we thought of the answer."""
        self._consecutive_failures = 0
        self._last_error = None
        self._gateway_proved_alive = True

    def record_gateway_unreachable(self, error: BaseException) -> None:
        self._consecutive_failures += 1
        self._last_error = f"{type(error).__name__}: {error}"
        if self.is_open:
            logger.error(
                "Circuit breaker OPEN after %d consecutive gateway failures "
                "(threshold %d). Last error: %s",
                self._consecutive_failures,
                self._threshold,
                self._last_error,
            )
