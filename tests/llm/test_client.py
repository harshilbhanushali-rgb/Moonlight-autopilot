import pytest
from pydantic import BaseModel

from app.domain.types import CallType
from app.llm.client import LLMMessage, StructuredOutputError, StubLLMClient


class CallTypeResponse(BaseModel):
    call_type: CallType


async def test_stub_client_validates_canned_response_into_the_model():
    stub = StubLLMClient(responses={"call_type": '{"call_type": "Demo"}'})

    result = await stub.complete_structured(
        messages=[LLMMessage(role="user", content="hi")],
        response_model=CallTypeResponse,
        response_key="call_type",
    )

    assert result.parsed.call_type is CallType.DEMO
    assert result.content == '{"call_type": "Demo"}'


async def test_stub_client_raises_when_no_response_configured_for_key():
    stub = StubLLMClient(responses={})

    with pytest.raises(KeyError):
        await stub.complete_structured(
            messages=[LLMMessage(role="user", content="hi")],
            response_model=CallTypeResponse,
            response_key="missing",
        )


async def test_stub_client_rejects_a_canned_response_that_does_not_match_the_schema():
    # A fixture that the real gateway could never produce shouldn't quietly
    # pass here either, or tests would assert on impossible responses.
    stub = StubLLMClient(responses={"call_type": '{"call_type": "Brainstorm"}'})

    with pytest.raises(StructuredOutputError):
        await stub.complete_structured(
            messages=[LLMMessage(role="user", content="hi")],
            response_model=CallTypeResponse,
            response_key="call_type",
        )


async def test_stub_client_records_calls_for_assertions():
    stub = StubLLMClient(responses={"call_type": '{"call_type": "Demo"}'})

    await stub.complete_structured(
        messages=[LLMMessage(role="system", content="sys")],
        response_model=CallTypeResponse,
        response_key="call_type",
    )

    assert len(stub.calls) == 1
    assert stub.calls[0].messages[0].role == "system"
    assert stub.calls[0].response_key == "call_type"
