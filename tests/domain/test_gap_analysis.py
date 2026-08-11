import pytest

from app.domain.errors import LLMOutputError
from app.domain.gap_analysis import analyse_gaps
from app.domain.types import Gap
from app.llm.client import StubLLMClient
from app.prompts.registry import PromptFile


PROMPT = PromptFile(
    kind="gap_rubric",
    call_type="demo",
    mode="descriptiononly",
    label="v1",
    content="Rubric themes for demo calls.",
    content_hash="feedface",
)


async def test_analyse_gaps_returns_empty_list_when_no_gaps_found():
    llm = StubLLMClient(responses={"gap_analysis": '{"gaps": []}'})

    result = await analyse_gaps(llm_client=llm, transcript="...", prompt=PROMPT)

    assert result.value == []
    assert result.prompt_content_hash == "feedface"


async def test_analyse_gaps_parses_moment_level_gap_with_dialogue_and_timestamp():
    llm = StubLLMClient(
        responses={
            "gap_analysis": (
                '{"gaps": [{"theme": "Seller-Dominated Call", '
                '"evidence_type": "dialogue", '
                '"evidence": "rep talked for 10 minutes straight", '
                '"timestamp": "00:12:34", "confidence": "high"}]}'
            )
        }
    )

    result = await analyse_gaps(llm_client=llm, transcript="...", prompt=PROMPT)

    assert result.value == [
        Gap(
            theme="Seller-Dominated Call",
            evidence_type="dialogue",
            evidence="rep talked for 10 minutes straight",
            timestamp="00:12:34",
            confidence="high",
        )
    ]


async def test_analyse_gaps_parses_whole_call_pattern_gap_with_explanation():
    llm = StubLLMClient(
        responses={
            "gap_analysis": (
                '{"gaps": [{"theme": "Wrong People on the Call", '
                '"evidence_type": "explanation", '
                '"evidence": "Only the IC attended; no economic buyer present.", '
                '"timestamp": null, "confidence": "medium"}]}'
            )
        }
    )

    result = await analyse_gaps(llm_client=llm, transcript="...", prompt=PROMPT)

    assert result.value == [
        Gap(
            theme="Wrong People on the Call",
            evidence_type="explanation",
            evidence="Only the IC attended; no economic buyer present.",
            timestamp=None,
            confidence="medium",
        )
    ]


async def test_analyse_gaps_rejects_gap_with_missing_evidence():
    llm = StubLLMClient(
        responses={
            "gap_analysis": (
                '{"gaps": [{"theme": "Some Theme", "evidence_type": "explanation", '
                '"evidence": null, "timestamp": null, "confidence": "low"}]}'
            )
        }
    )

    with pytest.raises(LLMOutputError):
        await analyse_gaps(llm_client=llm, transcript="...", prompt=PROMPT)


async def test_analyse_gaps_rejects_dialogue_evidence_with_no_timestamp():
    llm = StubLLMClient(
        responses={
            "gap_analysis": (
                '{"gaps": [{"theme": "Some Theme", "evidence_type": "dialogue", '
                '"evidence": "rep said X", "timestamp": null, "confidence": "low"}]}'
            )
        }
    )

    with pytest.raises(LLMOutputError):
        await analyse_gaps(llm_client=llm, transcript="...", prompt=PROMPT)


async def test_analyse_gaps_rejects_explanation_evidence_with_a_timestamp():
    llm = StubLLMClient(
        responses={
            "gap_analysis": (
                '{"gaps": [{"theme": "Some Theme", "evidence_type": "explanation", '
                '"evidence": "whole-call pattern", "timestamp": "00:01:00", "confidence": "low"}]}'
            )
        }
    )

    with pytest.raises(LLMOutputError):
        await analyse_gaps(llm_client=llm, transcript="...", prompt=PROMPT)


async def test_analyse_gaps_rejects_invalid_evidence_type():
    llm = StubLLMClient(
        responses={
            "gap_analysis": (
                '{"gaps": [{"theme": "Some Theme", "evidence_type": "vibes", '
                '"evidence": "rep said X", "timestamp": null, "confidence": "low"}]}'
            )
        }
    )

    with pytest.raises(LLMOutputError):
        await analyse_gaps(llm_client=llm, transcript="...", prompt=PROMPT)


async def test_analyse_gaps_rejects_invalid_confidence():
    llm = StubLLMClient(
        responses={
            "gap_analysis": (
                '{"gaps": [{"theme": "Some Theme", "evidence_type": "dialogue", '
                '"evidence": "rep said X", "timestamp": "00:01:00", "confidence": "extreme"}]}'
            )
        }
    )

    with pytest.raises(LLMOutputError):
        await analyse_gaps(llm_client=llm, transcript="...", prompt=PROMPT)


async def test_analyse_gaps_raises_on_malformed_json():
    llm = StubLLMClient(responses={"gap_analysis": "not json"})

    with pytest.raises(LLMOutputError):
        await analyse_gaps(llm_client=llm, transcript="...", prompt=PROMPT)


async def test_analyse_gaps_raises_when_gaps_key_missing():
    llm = StubLLMClient(responses={"gap_analysis": "{}"})

    with pytest.raises(LLMOutputError):
        await analyse_gaps(llm_client=llm, transcript="...", prompt=PROMPT)
