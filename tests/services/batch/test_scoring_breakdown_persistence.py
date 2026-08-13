"""The subscores have to survive the trip out of the domain and into the row.

Phase A's whole value is that a tier can be attributed to categories after the
fact, which is worth nothing if the categories are dropped between
`advance_analysis` and the UPDATE — the same way the prompt-hash chain was
silently broken for a long time by a missing field on AnalysisRecord.
"""

import json

import pytest

from app.db.models import STATUS_PENDING, STATUS_PROCESSED
from app.domain.types import CallScore, CallType
from app.llm.client import StubLLMClient
from app.prompts.registry import PromptFile
from app.services.batch.orchestrator import StepPrompts, advance_analysis


def _prompt(label):
    return PromptFile(kind=label, label="v2", content=f"{label} prompt", content_hash=label)


CATEGORIES = [
    {"name": f"Category {i}", "score": "4", "evidence": "they said something"}
    for i in range(1, 10)
] + [{"name": "Category 10", "score": "N/A", "evidence": "none"}]


def _prompts():
    return StepPrompts(
        call_type=_prompt("call_type"),
        scoring_for=lambda _: _prompt("scoring"),
        card_type=_prompt("card_type"),
        gap_rubric_for=lambda _: _prompt("gap_rubric"),
    )


def _record(**overrides):
    base = {
        "avoma_recording_id": "rec-1",
        "call_type": None,
        "call_score": None,
        "risk_gap_analysis": None,
        "card_type": None,
        "call_type_status": STATUS_PENDING,
        "scoring_status": STATUS_PENDING,
        "gap_status": STATUS_PENDING,
        "card_type_status": STATUS_PENDING,
        "call_type_error": None,
        "scoring_error": None,
        "gap_error": None,
        "card_type_error": None,
        "retry_count": 0,
        "dead_letter_at": None,
        "call_type_retry_count": 0,
        "scoring_retry_count": 0,
        "gap_retry_count": 0,
        "card_type_retry_count": 0,
    }
    base.update(overrides)
    return base


def _llm(**overrides):
    responses = {
        "call_type": '{"call_type": "Demo"}',
        "scoring": json.dumps({"categories": CATEGORIES}),
        "gap_analysis": '{"gaps": []}',
        "card_type": '{"card_type": "Coaching"}',
    }
    responses.update(overrides)
    return StubLLMClient(responses=responses)


@pytest.fixture
def transcript():
    from app.domain.transcript import Transcript, TranscriptSpeaker, TranscriptTurn

    return Transcript(
        speakers=[
            TranscriptSpeaker(id=0, name="Rep", email="r@joveo.com", is_rep=True),
            TranscriptSpeaker(id=1, name="Client", email="c@acme.com", is_rep=False),
        ],
        turns=[
            TranscriptTurn(speaker="Rep", speaker_id=0, text="Hello there.", start_s=0.0),
            TranscriptTurn(speaker="Client", speaker_id=1, text="Hi, good to talk.", start_s=4.0),
        ],
    )


async def test_a_successful_scoring_step_carries_both_the_tier_and_the_categories(transcript):
    result = await advance_analysis(
        llm_client=_llm(),
        transcript=transcript,
        call_metadata={},
        prompts=_prompts(),
        record=_record(),
    )

    assert result.call_score is CallScore.MEDIUM  # mean 4.0 over the nine scored
    assert result.scoring_status == STATUS_PROCESSED
    assert result.call_score_categories is not None
    assert len(result.call_score_categories) == 10
    assert result.call_score_categories[-1]["score"] is None  # the N/A one


async def test_a_failed_scoring_step_leaves_the_categories_alone(transcript):
    """None means "this pass has nothing to say", so persistence must not
    overwrite subscores an earlier pass recorded correctly — the same rule the
    prompt-hash columns follow."""
    result = await advance_analysis(
        llm_client=_llm(scoring='{"categories": []}'),
        transcript=transcript,
        call_metadata={},
        prompts=_prompts(),
        record=_record(),
    )

    assert result.scoring_status != STATUS_PROCESSED
    assert result.call_score_categories is None


async def test_a_skipped_scoring_step_leaves_the_categories_alone(transcript):
    """call_type failed, so no scoring prompt could be selected and scoring
    never ran."""
    result = await advance_analysis(
        llm_client=_llm(call_type='{"call_type": "Nonsense"}'),
        transcript=transcript,
        call_metadata={},
        prompts=_prompts(),
        record=_record(),
    )

    assert result.call_score_categories is None


async def test_an_already_processed_scoring_step_does_not_resurface_categories(transcript):
    result = await advance_analysis(
        llm_client=_llm(),
        transcript=transcript,
        call_metadata={},
        prompts=_prompts(),
        record=_record(
            call_type=CallType.DEMO.value,
            call_score=CallScore.HIGH.value,
            call_type_status=STATUS_PROCESSED,
            scoring_status=STATUS_PROCESSED,
        ),
    )

    assert result.call_score is CallScore.HIGH
    assert result.call_score_categories is None


async def test_the_card_type_step_still_sees_the_tier_not_the_breakdown(transcript):
    """CardTypeContext.existing_score is a string the card_type prompt renders;
    handing it a ScoreBreakdown would put a dataclass repr into the prompt."""
    llm = _llm()

    await advance_analysis(
        llm_client=llm,
        transcript=transcript,
        call_metadata={},
        prompts=_prompts(),
        record=_record(),
    )

    card_type_call = [c for c in llm.calls if c.response_key == "card_type"][0]
    assert "Medium" in card_type_call.messages[1].content
    assert "ScoreBreakdown" not in card_type_call.messages[1].content
