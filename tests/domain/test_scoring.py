import pytest

from app.domain.errors import LLMOutputError
from app.domain.scoring import score_call
from app.domain.types import CallScore
from app.llm.client import StubLLMClient
from app.prompts.registry import PromptFile


PROMPT = PromptFile(
    kind="scoring", label="v1", content="Score the call.", content_hash="cafebabe"
)


async def test_score_call_returns_matching_enum_value():
    llm = StubLLMClient(responses={"scoring": '{"call_score": "High"}'})

    result = await score_call(llm_client=llm, transcript="...", prompt=PROMPT)

    assert result.value == CallScore.HIGH
    assert result.prompt_content_hash == "cafebabe"


async def test_score_call_raises_llm_output_error_on_unknown_value():
    llm = StubLLMClient(responses={"scoring": '{"call_score": "Excellent"}'})

    with pytest.raises(LLMOutputError):
        await score_call(llm_client=llm, transcript="...", prompt=PROMPT)


async def test_score_call_raises_llm_output_error_when_key_missing():
    llm = StubLLMClient(responses={"scoring": '{"wrong_key": "High"}'})

    with pytest.raises(LLMOutputError):
        await score_call(llm_client=llm, transcript="...", prompt=PROMPT)
