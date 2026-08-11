import json

import httpx
import openai
import pytest
from pydantic import BaseModel

from app.domain.types import CallType
from app.llm.client import (
    LLMMessage,
    OpenAICompatibleLLMClient,
    StructuredOutputError,
)


class CallTypeResponse(BaseModel):
    call_type: CallType


def make_client(handler, **overrides):
    transport = httpx.MockTransport(handler)
    kwargs = dict(
        base_url="http://gateway.internal/v1",
        api_key="secret-key",
        model="gemini-2.5-flash",
        timeout_seconds=5.0,
        max_retries=2,
        environment="test",
        trace_name="moonlight_autopilot",
        http_client=httpx.AsyncClient(transport=transport),
    )
    kwargs.update(overrides)
    return OpenAICompatibleLLMClient(**kwargs)


def ok_response(content='{"call_type": "Demo"}', finish_reason="stop", refusal=None):
    message = {"role": "assistant", "content": content}
    if refusal is not None:
        message["refusal"] = refusal
    return httpx.Response(
        200,
        json={"choices": [{"message": message, "finish_reason": finish_reason, "index": 0}]},
    )


async def call(client, **overrides):
    kwargs = dict(
        messages=[LLMMessage(role="user", content="What is the call type?")],
        response_model=CallTypeResponse,
    )
    kwargs.update(overrides)
    return await client.complete_structured(**kwargs)


async def test_returns_parsed_model_instance_not_raw_text():
    captured = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["headers"] = request.headers
        return ok_response()

    result = await call(make_client(handler))

    assert result.parsed.call_type is CallType.DEMO
    assert result.content == '{"call_type": "Demo"}'
    assert captured["headers"]["authorization"] == "Bearer secret-key"


async def test_constrains_generation_with_the_response_schema_and_zero_temperature():
    # The reason this client exists: an unconstrained call to this gateway
    # intermittently comes back ```json-fenced, which no strict parser accepts.
    captured_bodies = []

    def handler(request: httpx.Request) -> httpx.Response:
        captured_bodies.append(json.loads(request.read()))
        return ok_response()

    await call(make_client(handler, temperature=0.0))

    body = captured_bodies[0]
    assert body["temperature"] == 0.0
    response_format = body["response_format"]
    assert response_format["type"] == "json_schema"
    assert response_format["json_schema"]["strict"] is True
    schema = response_format["json_schema"]["schema"]
    assert schema["additionalProperties"] is False
    # The six Call Types reach the gateway as an enum constraint, so a value
    # outside app.domain.types.CallType cannot be generated in the first place.
    enum_values = schema["$defs"]["CallType"]["enum"]
    assert enum_values == [c.value for c in CallType]


async def test_passes_configured_temperature_through():
    captured_bodies = []

    def handler(request: httpx.Request) -> httpx.Response:
        captured_bodies.append(json.loads(request.read()))
        return ok_response()

    await call(make_client(handler, temperature=0.7))

    assert captured_bodies[0]["temperature"] == 0.7


async def test_sends_thinking_level_as_reasoning_effort():
    # Gemini's thinking level MUST go over the wire as `reasoning_effort`.
    # The native thinking_config/thinking_level shapes are silently dropped by
    # this gateway — they produce identical reasoning-token counts to sending
    # nothing, so a regression here would look wired up while doing nothing.
    captured_bodies = []

    def handler(request: httpx.Request) -> httpx.Response:
        captured_bodies.append(json.loads(request.read()))
        return ok_response()

    await call(make_client(handler, reasoning_effort="high"))

    body = captured_bodies[0]
    assert body["reasoning_effort"] == "high"
    assert "thinking_level" not in body
    assert "thinking_config" not in body


async def test_omits_reasoning_effort_when_not_configured():
    captured_bodies = []

    def handler(request: httpx.Request) -> httpx.Response:
        captured_bodies.append(json.loads(request.read()))
        return ok_response()

    await call(make_client(handler, reasoning_effort=None))

    assert "reasoning_effort" not in captured_bodies[0]


async def test_sends_trace_metadata_with_generation_name_defaulted_from_response_key():
    captured_bodies = []

    def handler(request: httpx.Request) -> httpx.Response:
        captured_bodies.append(json.loads(request.read()))
        return ok_response()

    await call(make_client(handler), response_key="call_type")

    metadata = captured_bodies[0]["metadata"]
    assert metadata["generation_name"] == "call_type"
    assert metadata["trace_name"] == "moonlight_autopilot"
    assert metadata["environment"] == "test"
    assert "trace_id" in metadata
    assert "generation_id" in metadata


async def test_retries_on_timeout_and_succeeds_on_a_later_attempt():
    attempts = {"count": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        attempts["count"] += 1
        if attempts["count"] < 3:
            raise httpx.ReadTimeout("simulated timeout")
        return ok_response()

    result = await call(make_client(handler, max_retries=3))

    assert result.parsed.call_type is CallType.DEMO
    assert attempts["count"] == 3


async def test_raises_api_timeout_error_after_exhausting_retries():
    attempts = {"count": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        attempts["count"] += 1
        raise httpx.ReadTimeout("simulated timeout")

    client = make_client(handler, max_retries=2)

    with pytest.raises(openai.APITimeoutError):
        await call(client)

    assert attempts["count"] == 3  # initial attempt + 2 retries


async def test_raises_api_status_error_on_non_2xx_response():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(500, json={"error": "boom"})

    client = make_client(handler, max_retries=0)

    with pytest.raises(openai.APIStatusError):
        await call(client)


async def test_raises_structured_output_error_when_truncated_by_token_limit():
    # A long transcript can exhaust the token budget mid-object. That must
    # surface as a failed step, not as a partial object or a crashed batch.
    attempts = {"count": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        attempts["count"] += 1
        return ok_response(content='{"call_ty', finish_reason="length")

    with pytest.raises(StructuredOutputError, match="truncated"):
        await call(make_client(handler, max_retries=2))

    # Not retried in-client: at temperature 0 the retry truncates identically.
    assert attempts["count"] == 1


async def test_raises_structured_output_error_on_refusal():
    def handler(request: httpx.Request) -> httpx.Response:
        return ok_response(content=None, refusal="I can't help with that.")

    with pytest.raises(StructuredOutputError, match="refused"):
        await call(make_client(handler))


async def test_retries_a_transient_connection_error_not_only_a_timeout():
    """A bare connection error used to fail the step on the first blip.

    It was observed hitting several concurrent requests at once and then
    succeeding immediately on a repeat, so it is transient. Left un-retried it
    also feeds the analyser's circuit breaker, which reads connection failures as
    "the gateway is down" — enough simultaneous blips would abandon a whole
    nightly batch over nothing.
    """
    attempts = {"count": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        attempts["count"] += 1
        if attempts["count"] < 3:
            raise httpx.ConnectError("simulated connection reset")
        return ok_response()

    result = await call(make_client(handler, max_retries=3))

    assert result.parsed.call_type is CallType.DEMO
    assert attempts["count"] == 3


async def test_an_http_500_is_still_not_retried_in_client():
    """APIStatusError means the gateway answered. It belongs on the per-step
    failure path where an operator sees it, not silently retried alongside
    transport blips."""
    attempts = {"count": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        attempts["count"] += 1
        return httpx.Response(500, json={"error": "boom"})

    with pytest.raises(openai.APIStatusError):
        await call(make_client(handler, max_retries=3))

    assert attempts["count"] == 1
