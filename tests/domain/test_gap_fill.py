import pytest

from app.domain.errors import LLMOutputError
from app.domain.gap_fill import fill_gap_field
from app.domain.types import CardTypeContext
from app.llm.client import StubLLMClient
from app.prompts.registry import PromptFile


PROMPT = PromptFile(
    kind="gap_fill", label="v1", content="Summarize the gap from the comment.", content_hash="ab12"
)


async def test_fill_gap_field_returns_text_from_comment_only_context():
    llm = StubLLMClient(responses={"gap_fill": '{"gap": "Rep skipped discovery questions."}'})
    context = CardTypeContext(comment_text="Rep jumped straight to pricing.")

    result = await fill_gap_field(llm_client=llm, context=context, prompt=PROMPT)

    assert result.value == "Rep skipped discovery questions."
    assert result.prompt_content_hash == "ab12"


async def test_fill_gap_field_raises_value_error_when_context_has_no_content():
    llm = StubLLMClient(responses={"gap_fill": '{"gap": "x"}'})

    with pytest.raises(ValueError):
        await fill_gap_field(llm_client=llm, context=CardTypeContext(), prompt=PROMPT)


async def test_fill_gap_field_raises_llm_output_error_on_empty_gap_text():
    llm = StubLLMClient(responses={"gap_fill": '{"gap": ""}'})
    context = CardTypeContext(comment_text="some comment")

    with pytest.raises(LLMOutputError):
        await fill_gap_field(llm_client=llm, context=context, prompt=PROMPT)


async def test_fill_gap_field_raises_llm_output_error_when_key_missing():
    llm = StubLLMClient(responses={"gap_fill": "{}"})
    context = CardTypeContext(comment_text="some comment")

    with pytest.raises(LLMOutputError):
        await fill_gap_field(llm_client=llm, context=context, prompt=PROMPT)
