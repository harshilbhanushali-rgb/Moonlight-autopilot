import pytest

from app.domain.call_type import classify_call_type
from app.domain.errors import LLMOutputError
from app.domain.types import CallType
from app.llm.client import LLMMessage, StubLLMClient
from app.prompts.registry import PromptFile


PROMPT = PromptFile(
    kind="call_type",
    label="v1",
    content="Classify the call type.",
    content_hash="deadbeef",
)


async def test_classify_call_type_returns_matching_enum_value():
    llm = StubLLMClient(responses={"call_type": '{"call_type": "Demo"}'})

    result = await classify_call_type(llm_client=llm, transcript="...", prompt=PROMPT)

    assert result.value == CallType.DEMO
    assert result.prompt_content_hash == "deadbeef"


async def test_classify_call_type_sends_transcript_and_prompt_content_to_llm():
    llm = StubLLMClient(responses={"call_type": '{"call_type": "Demo"}'})

    await classify_call_type(llm_client=llm, transcript="rep: hello there", prompt=PROMPT)

    sent = llm.calls[0].messages
    joined = " ".join(m.content for m in sent)
    assert "rep: hello there" in joined
    assert "Classify the call type." in joined


async def test_classify_call_type_raises_llm_output_error_on_unknown_value():
    llm = StubLLMClient(responses={"call_type": '{"call_type": "Not A Real Type"}'})

    with pytest.raises(LLMOutputError):
        await classify_call_type(llm_client=llm, transcript="...", prompt=PROMPT)


async def test_classify_call_type_raises_llm_output_error_on_malformed_json():
    llm = StubLLMClient(responses={"call_type": "not json"})

    with pytest.raises(LLMOutputError):
        await classify_call_type(llm_client=llm, transcript="...", prompt=PROMPT)


async def test_classify_call_type_raises_llm_output_error_when_key_missing():
    llm = StubLLMClient(responses={"call_type": '{"wrong_key": "Demo"}'})

    with pytest.raises(LLMOutputError):
        await classify_call_type(llm_client=llm, transcript="...", prompt=PROMPT)
