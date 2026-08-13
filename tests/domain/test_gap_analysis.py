import pytest

from app.domain.errors import LLMOutputError
from app.domain.gap_analysis import analyse_gaps
from app.domain.transcript import Transcript, TranscriptSpeaker, TranscriptTurn
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

TRANSCRIPT = Transcript(
    speakers=[
        TranscriptSpeaker(id=0, name="Rep", email="rep@joveo.com", is_rep=True),
        TranscriptSpeaker(id=1, name="Buyer", email="buyer@acme.com", is_rep=False),
    ],
    turns=[
        TranscriptTurn(
            speaker="Rep",
            speaker_id=0,
            text="Let me walk you through the whole platform.",
            start_s=0,
        ),
        TranscriptTurn(speaker="Buyer", speaker_id=1, text="Sure, go ahead.", start_s=41),
        TranscriptTurn(
            speaker="Rep", speaker_id=0, text="rep talked for 10 minutes straight", start_s=754
        ),
    ],
)


async def test_analyse_gaps_returns_empty_list_when_no_gaps_found():
    llm = StubLLMClient(responses={"gap_analysis": '{"gaps": []}'})

    result = await analyse_gaps(llm_client=llm, transcript=TRANSCRIPT, prompt=PROMPT)

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

    result = await analyse_gaps(llm_client=llm, transcript=TRANSCRIPT, prompt=PROMPT)

    assert result.value == [
        Gap(
            theme="Seller-Dominated Call",
            evidence_type="dialogue",
            evidence="rep talked for 10 minutes straight",
            # Anchored to the turn the quote is actually in, not the 00:12:34
            # the model claimed — see app/domain/citation.py.
            timestamp="12:34",
            confidence="high",
        )
    ]


VERIFY_DIALOGUE = PromptFile(
    kind="gap_verification", mode="dialogue", label="v1", content="v-d", content_hash="vd"
)
VERIFY_EXPLANATION = PromptFile(
    kind="gap_verification", mode="explanation", label="v1", content="v-e", content_hash="ve"
)


async def test_a_gap_whose_evidence_is_judged_not_to_support_it_is_dropped():
    """The entailment check runs inside the gap step, so a caller cannot get an
    unverified gap list. See app/domain/gap_verification.py."""
    llm = StubLLMClient(
        responses={
            "gap_analysis": (
                '{"gaps": [{"theme": "Seller-Dominated Call", "evidence_type": "dialogue", '
                '"evidence": "rep talked for 10 minutes straight", '
                '"timestamp": "12:34", "confidence": "high"}]}'
            ),
            "gap_verification_dialogue": (
                '{"verdicts": [{"index": 0, "verdict": "contradicted", "reason": "r", "evidence_quote": "q"}]}'
            ),
        }
    )

    result = await analyse_gaps(
        llm_client=llm,
        transcript=TRANSCRIPT,
        prompt=PROMPT,
        verification_prompts=(VERIFY_DIALOGUE, VERIFY_EXPLANATION),
    )

    assert result.value == []


async def test_verification_is_skipped_entirely_when_no_prompts_are_supplied():
    """The eval harness compares rubric wording and must see raw gap output —
    filtering it would measure the verifier instead of the rubric."""
    llm = StubLLMClient(
        responses={
            "gap_analysis": (
                '{"gaps": [{"theme": "T", "evidence_type": "dialogue", '
                '"evidence": "rep talked for 10 minutes straight", '
                '"timestamp": "12:34", "confidence": "high"}]}'
            )
        }
    )

    result = await analyse_gaps(llm_client=llm, transcript=TRANSCRIPT, prompt=PROMPT)

    assert len(result.value) == 1
    assert [c.response_key for c in llm.calls] == ["gap_analysis"]


async def test_a_gap_quoting_text_that_is_not_in_the_transcript_fails_the_step():
    """The citation must be checkable by a moderator. Measured over 46 real
    calls, no quote was ever fabricated — but nothing enforced that."""
    llm = StubLLMClient(
        responses={
            "gap_analysis": (
                '{"gaps": [{"theme": "Invented", "evidence_type": "dialogue", '
                '"evidence": "we already signed the contract", '
                '"timestamp": "00:10", "confidence": "high"}]}'
            )
        }
    )

    with pytest.raises(LLMOutputError):
        await analyse_gaps(llm_client=llm, transcript=TRANSCRIPT, prompt=PROMPT)


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

    result = await analyse_gaps(llm_client=llm, transcript=TRANSCRIPT, prompt=PROMPT)

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
        await analyse_gaps(llm_client=llm, transcript=TRANSCRIPT, prompt=PROMPT)


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
        await analyse_gaps(llm_client=llm, transcript=TRANSCRIPT, prompt=PROMPT)


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
        await analyse_gaps(llm_client=llm, transcript=TRANSCRIPT, prompt=PROMPT)


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
        await analyse_gaps(llm_client=llm, transcript=TRANSCRIPT, prompt=PROMPT)


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
        await analyse_gaps(llm_client=llm, transcript=TRANSCRIPT, prompt=PROMPT)


async def test_analyse_gaps_raises_on_malformed_json():
    llm = StubLLMClient(responses={"gap_analysis": "not json"})

    with pytest.raises(LLMOutputError):
        await analyse_gaps(llm_client=llm, transcript=TRANSCRIPT, prompt=PROMPT)


async def test_analyse_gaps_raises_when_gaps_key_missing():
    llm = StubLLMClient(responses={"gap_analysis": "{}"})

    with pytest.raises(LLMOutputError):
        await analyse_gaps(llm_client=llm, transcript=TRANSCRIPT, prompt=PROMPT)
