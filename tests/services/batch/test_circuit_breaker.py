import httpx
import pytest
from openai import APIConnectionError, APITimeoutError

from app.core.analyser_config import DEFAULT_CIRCUIT_BREAKER_CONSECUTIVE_FAILURES
from app.domain.errors import LLMOutputError
from app.services.batch.circuit_breaker import CircuitBreaker

_REQUEST = httpx.Request("POST", "http://gateway.internal/v1/chat/completions")


def transport_error():
    return APITimeoutError(request=_REQUEST)


def test_starts_closed():
    breaker = CircuitBreaker(threshold=3)

    assert breaker.is_open is False
    assert breaker.consecutive_failures == 0


def test_opens_once_the_threshold_is_reached():
    breaker = CircuitBreaker(threshold=3)

    for _ in range(2):
        breaker.record_gateway_unreachable(transport_error())
    assert breaker.is_open is False

    breaker.record_gateway_unreachable(transport_error())
    assert breaker.is_open is True


def test_a_reachable_gateway_resets_the_count():
    breaker = CircuitBreaker(threshold=3)

    breaker.record_gateway_unreachable(transport_error())
    breaker.record_gateway_unreachable(transport_error())
    breaker.record_gateway_reachable()

    assert breaker.consecutive_failures == 0
    assert breaker.last_error is None

    # Two more must no longer be enough to trip it.
    breaker.record_gateway_unreachable(transport_error())
    breaker.record_gateway_unreachable(transport_error())
    assert breaker.is_open is False


def test_only_consecutive_failures_count():
    # A gateway that fails intermittently but keeps answering is not an outage.
    breaker = CircuitBreaker(threshold=3)

    for _ in range(10):
        breaker.record_gateway_unreachable(transport_error())
        breaker.record_gateway_unreachable(transport_error())
        breaker.record_gateway_reachable()

    assert breaker.is_open is False


def test_records_the_last_error_for_the_operator():
    breaker = CircuitBreaker(threshold=1)

    breaker.record_gateway_unreachable(APIConnectionError(request=_REQUEST))

    assert breaker.is_open is True
    assert "APIConnectionError" in breaker.last_error


def test_a_threshold_of_zero_disables_the_breaker():
    breaker = CircuitBreaker(threshold=0)

    for _ in range(100):
        breaker.record_gateway_unreachable(transport_error())

    assert breaker.enabled is False
    assert breaker.is_open is False


@pytest.mark.parametrize("threshold", [-1, 0])
def test_non_positive_thresholds_are_disabled_not_instantly_open(threshold):
    breaker = CircuitBreaker(threshold=threshold)

    assert breaker.is_open is False


def test_default_threshold_spans_more_than_one_row():
    # A row has 4 steps, so a threshold of <=4 could be reached by a single
    # pathological transcript timing out on all of its own steps. The default
    # must require a second row to be involved.
    assert DEFAULT_CIRCUIT_BREAKER_CONSECUTIVE_FAILURES > 4


def test_llm_output_error_is_not_a_transport_failure():
    # Documents the intended call-site contract: a rejected *answer* is
    # evidence the gateway is alive, so callers route it to reachable().
    breaker = CircuitBreaker(threshold=2)

    breaker.record_gateway_unreachable(transport_error())
    breaker.record_gateway_reachable()  # what _run_step does for LLMOutputError
    breaker.record_gateway_unreachable(LLMOutputError("scoring", "raw", "bad"))

    assert breaker.is_open is False
