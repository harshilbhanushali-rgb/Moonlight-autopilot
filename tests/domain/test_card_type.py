import pytest

from app.domain.card_type import classify_card_type
from app.domain.errors import LLMOutputError
from app.domain.types import CardType, CardTypeContext, Gap
from app.llm.client import StubLLMClient
from app.prompts.registry import PromptFile


PROMPT = PromptFile(
    kind="card_type", label="v1", content="Classify Coaching vs Risk.", content_hash="c0ffee"
)


async def test_classify_card_type_with_full_batch_context_includes_all_sections_in_prompt():
    llm = StubLLMClient(responses={"card_type": '{"card_type": "Risk"}'})
    context = CardTypeContext(
        transcript="rep: we can't meet that deadline",
        call_metadata={"call_type": "Pricing/Negotiation"},
        existing_score="Low",
        comment_text="Rep conceded pricing without escalating.",
    )

    result = await classify_card_type(llm_client=llm, context=context, prompt=PROMPT)

    assert result.value == CardType.RISK
    sent = " ".join(m.content for m in llm.calls[0].messages)
    assert "rep: we can't meet that deadline" in sent
    assert "Rep conceded pricing without escalating." in sent
    assert "Low" in sent


async def test_classify_card_type_with_comment_only_context_used_by_manual_autofill():
    llm = StubLLMClient(responses={"card_type": '{"card_type": "Coaching"}'})
    context = CardTypeContext(comment_text="Rep should have asked more discovery questions.")

    result = await classify_card_type(llm_client=llm, context=context, prompt=PROMPT)

    assert result.value == CardType.COACHING
    sent = " ".join(m.content for m in llm.calls[0].messages)
    assert "Rep should have asked more discovery questions." in sent


async def test_the_gaps_found_for_this_call_are_shown_to_the_card_type_classifier():
    """Card Type says whether the card is about the deal (Risk) or the rep
    (Coaching) — the gaps are the strongest evidence of which, so withholding
    them produced Risk cards whose every gap was a coaching observation."""
    llm = StubLLMClient(responses={"card_type": '{"card_type": "Risk"}'})
    context = CardTypeContext(
        transcript="rep: hi",
        gaps=[
            Gap(
                theme="Budget Frozen Client-Side",
                evidence_type="dialogue",
                evidence="any new vendor isn't gonna happen right now",
                timestamp="59:12",
                confidence="high",
            )
        ],
    )

    await classify_card_type(llm_client=llm, context=context, prompt=PROMPT)

    sent = " ".join(m.content for m in llm.calls[0].messages)
    assert "Budget Frozen Client-Side" in sent
    assert "any new vendor isn't gonna happen right now" in sent


async def test_a_call_with_no_gaps_is_described_as_such_rather_than_omitting_the_section():
    """An empty gap list is a finding ("nothing was flagged"), not missing
    input — leaving the section out entirely is indistinguishable from the
    gap step having failed, which is a different situation."""
    llm = StubLLMClient(responses={"card_type": '{"card_type": "Coaching"}'})
    context = CardTypeContext(transcript="rep: hi", gaps=[])

    await classify_card_type(llm_client=llm, context=context, prompt=PROMPT)

    sent = " ".join(m.content for m in llm.calls[0].messages)
    assert "No gaps" in sent


async def test_gaps_are_omitted_entirely_when_the_gap_step_did_not_produce_any():
    """None means the gap step failed or was skipped. That must not be
    presented to the classifier as "this call is clean"."""
    llm = StubLLMClient(responses={"card_type": '{"card_type": "Coaching"}'})
    context = CardTypeContext(transcript="rep: hi", gaps=None)

    await classify_card_type(llm_client=llm, context=context, prompt=PROMPT)

    sent = " ".join(m.content for m in llm.calls[0].messages)
    assert "No gaps" not in sent
    assert "Gaps" not in sent


async def test_classify_card_type_raises_value_error_when_context_has_no_content():
    llm = StubLLMClient(responses={"card_type": '{"card_type": "Coaching"}'})
    context = CardTypeContext()

    with pytest.raises(ValueError):
        await classify_card_type(llm_client=llm, context=context, prompt=PROMPT)


async def test_classify_card_type_raises_llm_output_error_on_unknown_value():
    llm = StubLLMClient(responses={"card_type": '{"card_type": "Neutral"}'})
    context = CardTypeContext(comment_text="some comment")

    with pytest.raises(LLMOutputError):
        await classify_card_type(llm_client=llm, context=context, prompt=PROMPT)
