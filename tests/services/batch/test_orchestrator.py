import httpx
import pytest
from openai import APIStatusError, APITimeoutError

from app.services.batch.orchestrator import StepPrompts, advance_analysis, fail_analysis
from app.domain.types import CallType
from app.db.models import (
    STATUS_FAILED,
    STATUS_FAILED_PERMANENT,
    STATUS_PENDING,
    STATUS_PROCESSED,
)
from app.domain.transcript import Transcript, TranscriptSpeaker, TranscriptTurn
from app.llm.client import StubLLMClient
from app.prompts.registry import PromptFile


# advance_analysis takes the Transcript, not rendered text, so the gap step can
# check its citations against the turns (app/domain/citation.py). Gap fixtures
# in these tests must therefore quote something that is actually in here.
TRANSCRIPT = Transcript(
    speakers=[
        TranscriptSpeaker(id=0, name="rep", email="rep@joveo.com", is_rep=True),
        TranscriptSpeaker(id=1, name="buyer", email="buyer@acme.com", is_rep=False),
    ],
    turns=[
        TranscriptTurn(speaker="rep", speaker_id=0, text="hi", start_s=0),
        TranscriptTurn(
            speaker="buyer",
            speaker_id=1,
            text="any new vendor is not gonna happen right now",
            start_s=3552,
        ),
    ],
)


def make_prompts(gap_prompt_by_call_type=None, scoring_prompt=None):
    gap_prompt_by_call_type = gap_prompt_by_call_type or {}
    scoring_prompt = scoring_prompt or PromptFile(
        kind="scoring", label="v1", content="sc", content_hash="h2"
    )
    return StepPrompts(
        call_type=PromptFile(kind="call_type", label="v1", content="ct", content_hash="h1"),
        scoring_for=lambda call_type: scoring_prompt,
        card_type=PromptFile(kind="card_type", label="v1", content="cd", content_hash="h3"),
        gap_rubric_for=lambda call_type: gap_prompt_by_call_type[call_type],
        gap_verification_dialogue=PromptFile(
            kind="gap_verification", mode="dialogue", label="v1", content="vd", content_hash="h5"
        ),
        gap_verification_explanation=PromptFile(
            kind="gap_verification", mode="explanation", label="v1", content="ve", content_hash="h6"
        ),
    )


def fresh_record(avoma_recording_id="rec-1"):
    return {
        "avoma_recording_id": avoma_recording_id,
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
        "call_type_retry_count": 0,
        "scoring_retry_count": 0,
        "gap_retry_count": 0,
        "card_type_retry_count": 0,
        "dead_letter_at": None,
    }


ALL_GOOD_RESPONSES = {
    "call_type": '{"call_type": "Demo"}',
    "scoring": '{"call_score": "High"}',
    "gap_analysis": '{"gaps": []}',
    "card_type": '{"card_type": "Coaching"}',
}


async def test_advance_analysis_marks_all_steps_processed_and_overall_status_processed():
    llm = StubLLMClient(responses=ALL_GOOD_RESPONSES)
    demo_gap_prompt = PromptFile(
        kind="gap_rubric", call_type="Demo", mode="descriptiononly", label="v1",
        content="rubric", content_hash="h4",
    )
    prompts = make_prompts({CallType.DEMO: demo_gap_prompt})

    result = await advance_analysis(
        llm_client=llm,
        transcript=TRANSCRIPT,
        call_metadata={},
        prompts=prompts,
        record=fresh_record(),
    )

    assert result.call_type_status == STATUS_PROCESSED
    assert result.scoring_status == STATUS_PROCESSED
    assert result.gap_status == STATUS_PROCESSED
    assert result.card_type_status == STATUS_PROCESSED
    assert result.overall_status == STATUS_PROCESSED
    assert result.call_type == CallType.DEMO


def card_type_prompt_text(llm):
    call = next(c for c in llm.calls if c.response_key == "card_type")
    return " ".join(m.content for m in call.messages)


async def test_the_batch_pipeline_verifies_every_gap_before_recording_it():
    """A gap the verifier rejects must not reach the analysis row. Wired here
    rather than left to callers so no batch path can skip it."""
    responses = dict(ALL_GOOD_RESPONSES)
    responses["gap_analysis"] = (
        '{"gaps": [{"theme": "No Pre-Call Research", "evidence_type": "dialogue", '
        '"evidence": "any new vendor is not gonna happen right now", '
        '"timestamp": "59:12", "confidence": "high"}]}'
    )
    responses["gap_verification_dialogue"] = (
        '{"verdicts": [{"index": 0, "verdict": "contradicted", "reason": "r", "evidence_quote": "q"}]}'
    )
    llm = StubLLMClient(responses=responses)
    demo_gap_prompt = PromptFile(
        kind="gap_rubric", call_type="Demo", mode="descriptiononly", label="v1",
        content="rubric", content_hash="h4",
    )

    result = await advance_analysis(
        llm_client=llm,
        transcript=TRANSCRIPT,
        call_metadata={},
        prompts=make_prompts({CallType.DEMO: demo_gap_prompt}),
        record=fresh_record(),
    )

    assert result.risk_gap_analysis == []
    assert result.gap_status == STATUS_PROCESSED


async def test_the_verification_prompt_that_filtered_a_gap_list_is_recorded():
    """The verification prompts change which gaps survive, so an edit to one
    must be as traceable as an edit to the rubric — same rule as the other
    four steps' provenance."""
    responses = dict(ALL_GOOD_RESPONSES)
    responses["gap_analysis"] = (
        '{"gaps": [{"theme": "T", "evidence_type": "dialogue", '
        '"evidence": "any new vendor is not gonna happen right now", '
        '"timestamp": "59:12", "confidence": "high"}]}'
    )
    responses["gap_verification_dialogue"] = (
        '{"verdicts": [{"index": 0, "verdict": "supported", "reason": "r", "evidence_quote": "q"}]}'
    )
    llm = StubLLMClient(responses=responses)
    demo_gap_prompt = PromptFile(
        kind="gap_rubric", call_type="Demo", mode="descriptiononly", label="v1",
        content="rubric", content_hash="h4",
    )

    result = await advance_analysis(
        llm_client=llm,
        transcript=TRANSCRIPT,
        call_metadata={},
        prompts=make_prompts({CallType.DEMO: demo_gap_prompt}),
        record=fresh_record(),
    )

    assert result.gap_verification_dialogue_hash == "h5"
    # No explanation gap on this call, so that verifier never ran and must not
    # claim provenance it did not produce.
    assert result.gap_verification_explanation_hash is None


async def test_the_gaps_this_run_found_are_passed_to_the_card_type_step():
    """card_type decides deal-risk vs rep-coaching, and the gaps are the best
    evidence of which. Without them the step produced Risk cards whose gaps
    were all coaching observations."""
    responses = dict(ALL_GOOD_RESPONSES)
    responses["gap_analysis"] = (
        '{"gaps": [{"theme": "Budget Frozen Client-Side", "evidence_type": "dialogue", '
        '"evidence": "any new vendor is not gonna happen right now", '
        '"timestamp": "59:12", "confidence": "high"}]}'
    )
    responses["gap_verification_dialogue"] = (
        '{"verdicts": [{"index": 0, "verdict": "supported", "reason": "r", "evidence_quote": "q"}]}'
    )
    llm = StubLLMClient(responses=responses)
    demo_gap_prompt = PromptFile(
        kind="gap_rubric", call_type="Demo", mode="descriptiononly", label="v1",
        content="rubric", content_hash="h4",
    )

    await advance_analysis(
        llm_client=llm,
        transcript=TRANSCRIPT,
        call_metadata={},
        prompts=make_prompts({CallType.DEMO: demo_gap_prompt}),
        record=fresh_record(),
    )

    assert "Budget Frozen Client-Side" in card_type_prompt_text(llm)


async def test_card_type_is_told_when_a_call_had_no_gaps_flagged():
    llm = StubLLMClient(responses=ALL_GOOD_RESPONSES)
    demo_gap_prompt = PromptFile(
        kind="gap_rubric", call_type="Demo", mode="descriptiononly", label="v1",
        content="rubric", content_hash="h4",
    )

    await advance_analysis(
        llm_client=llm,
        transcript=TRANSCRIPT,
        call_metadata={},
        prompts=make_prompts({CallType.DEMO: demo_gap_prompt}),
        record=fresh_record(),
    )

    assert "No gaps were flagged" in card_type_prompt_text(llm)


async def test_card_type_is_not_told_a_call_is_clean_when_the_gap_step_failed():
    """A failed gap step leaves nothing known about gaps. Rendering that as
    "no gaps" would let a gateway error read as a clean call."""
    responses = dict(ALL_GOOD_RESPONSES)
    responses["gap_analysis"] = "not json"
    llm = StubLLMClient(responses=responses)
    demo_gap_prompt = PromptFile(
        kind="gap_rubric", call_type="Demo", mode="descriptiononly", label="v1",
        content="rubric", content_hash="h4",
    )

    await advance_analysis(
        llm_client=llm,
        transcript=TRANSCRIPT,
        call_metadata={},
        prompts=make_prompts({CallType.DEMO: demo_gap_prompt}),
        record=fresh_record(),
    )

    assert "No gaps were flagged" not in card_type_prompt_text(llm)


async def test_advance_analysis_marks_failing_step_failed_but_still_runs_independent_steps():
    responses = dict(ALL_GOOD_RESPONSES)
    responses["scoring"] = "not json"
    llm = StubLLMClient(responses=responses)
    demo_gap_prompt = PromptFile(
        kind="gap_rubric", call_type="Demo", mode="descriptiononly", label="v1",
        content="rubric", content_hash="h4",
    )
    prompts = make_prompts({CallType.DEMO: demo_gap_prompt})

    result = await advance_analysis(
        llm_client=llm,
        transcript=TRANSCRIPT,
        call_metadata={},
        prompts=prompts,
        record=fresh_record(),
    )

    assert result.call_type_status == STATUS_PROCESSED
    assert result.scoring_status == STATUS_FAILED
    assert result.scoring_error is not None
    assert result.gap_status == STATUS_PROCESSED
    assert result.card_type_status == STATUS_PROCESSED
    assert result.overall_status == STATUS_FAILED


async def test_advance_analysis_does_not_recall_llm_for_already_processed_steps():
    llm = StubLLMClient(responses=ALL_GOOD_RESPONSES)
    demo_gap_prompt = PromptFile(
        kind="gap_rubric", call_type="Demo", mode="descriptiononly", label="v1",
        content="rubric", content_hash="h4",
    )
    prompts = make_prompts({CallType.DEMO: demo_gap_prompt})

    first = await advance_analysis(
        llm_client=llm, transcript=TRANSCRIPT, call_metadata={},
        prompts=prompts, record=fresh_record(),
    )
    calls_after_first_pass = len(llm.calls)

    await advance_analysis(
        llm_client=llm, transcript=TRANSCRIPT, call_metadata={},
        prompts=prompts, record=first.__dict__,
    )

    assert len(llm.calls) == calls_after_first_pass


async def test_scoring_and_gap_analysis_are_skipped_when_call_type_not_yet_resolved():
    responses = dict(ALL_GOOD_RESPONSES)
    responses["call_type"] = "not json"
    llm = StubLLMClient(responses=responses)
    prompts = make_prompts({})

    result = await advance_analysis(
        llm_client=llm, transcript=TRANSCRIPT, call_metadata={},
        prompts=prompts, record=fresh_record(),
    )

    assert result.call_type_status == STATUS_FAILED
    assert result.scoring_status == STATUS_PENDING
    assert result.gap_status == STATUS_PENDING
    assert result.overall_status == STATUS_FAILED


async def test_advance_analysis_escalates_to_failed_permanent_after_max_retries():
    responses = dict(ALL_GOOD_RESPONSES)
    responses["scoring"] = "not json"
    llm = StubLLMClient(responses=responses)
    demo_gap_prompt = PromptFile(
        kind="gap_rubric", call_type="Demo", mode="descriptiononly", label="v1",
        content="rubric", content_hash="h4",
    )
    prompts = make_prompts({CallType.DEMO: demo_gap_prompt})

    record = fresh_record()
    for _ in range(3):
        result = await advance_analysis(
            llm_client=llm, transcript=TRANSCRIPT, call_metadata={},
            prompts=prompts, record=record, max_retries=2,
        )
        record = result.__dict__

    assert result.scoring_status == STATUS_FAILED_PERMANENT
    assert result.dead_letter_at is not None
    assert result.overall_status == STATUS_FAILED_PERMANENT


async def test_advance_analysis_does_not_retry_dead_lettered_step():
    responses = dict(ALL_GOOD_RESPONSES)
    responses["scoring"] = "not json"
    llm = StubLLMClient(responses=responses)
    demo_gap_prompt = PromptFile(
        kind="gap_rubric", call_type="Demo", mode="descriptiononly", label="v1",
        content="rubric", content_hash="h4",
    )
    prompts = make_prompts({CallType.DEMO: demo_gap_prompt})

    record = fresh_record()
    for _ in range(3):
        result = await advance_analysis(
            llm_client=llm, transcript=TRANSCRIPT, call_metadata={},
            prompts=prompts, record=record, max_retries=2,
        )
        record = result.__dict__
    scoring_calls_before = sum(1 for c in llm.calls if c.response_key == "scoring")

    await advance_analysis(
        llm_client=llm, transcript=TRANSCRIPT, call_metadata={},
        prompts=prompts, record=record, max_retries=2,
    )
    scoring_calls_after = sum(1 for c in llm.calls if c.response_key == "scoring")

    assert scoring_calls_after == scoring_calls_before


async def test_every_successful_step_carries_the_content_hash_of_the_prompt_it_used():
    llm = StubLLMClient(responses=ALL_GOOD_RESPONSES)
    demo_gap_prompt = PromptFile(
        kind="gap_rubric", call_type="Demo", mode="descriptiononly", label="v1",
        content="rubric", content_hash="h4",
    )
    prompts = make_prompts({CallType.DEMO: demo_gap_prompt})

    result = await advance_analysis(
        llm_client=llm, transcript=TRANSCRIPT, call_metadata={},
        prompts=prompts, record=fresh_record(),
    )

    assert result.call_type_prompt_hash == "h1"
    assert result.scoring_prompt_hash == "h2"
    assert result.card_type_prompt_hash == "h3"
    assert result.gap_rubric_hash == "h4"


async def test_a_failed_step_records_no_prompt_hash_while_its_siblings_still_do():
    """Correctness rule (a): provenance is only ever attached to output that
    exists. A hash on a failed step would claim a lineage for nothing."""
    responses = dict(ALL_GOOD_RESPONSES)
    responses["scoring"] = "not json"
    llm = StubLLMClient(responses=responses)
    demo_gap_prompt = PromptFile(
        kind="gap_rubric", call_type="Demo", mode="descriptiononly", label="v1",
        content="rubric", content_hash="h4",
    )
    prompts = make_prompts({CallType.DEMO: demo_gap_prompt})

    result = await advance_analysis(
        llm_client=llm, transcript=TRANSCRIPT, call_metadata={},
        prompts=prompts, record=fresh_record(),
    )

    assert result.scoring_status == STATUS_FAILED
    assert result.scoring_prompt_hash is None
    assert result.call_type_prompt_hash == "h1"
    assert result.gap_rubric_hash == "h4"
    assert result.card_type_prompt_hash == "h3"


async def test_a_step_skipped_because_it_already_succeeded_reports_no_prompt_hash():
    """Correctness rule (b), orchestrator half: a step that didn't run this
    pass reports None, which is the signal the persistence layer uses to leave
    the already-recorded version id alone instead of nulling it."""
    llm = StubLLMClient(responses=ALL_GOOD_RESPONSES)
    demo_gap_prompt = PromptFile(
        kind="gap_rubric", call_type="Demo", mode="descriptiononly", label="v1",
        content="rubric", content_hash="h4",
    )
    prompts = make_prompts({CallType.DEMO: demo_gap_prompt})

    first = await advance_analysis(
        llm_client=llm, transcript=TRANSCRIPT, call_metadata={},
        prompts=prompts, record=fresh_record(),
    )
    second = await advance_analysis(
        llm_client=llm, transcript=TRANSCRIPT, call_metadata={},
        prompts=prompts, record=first.__dict__,
    )

    assert second.overall_status == STATUS_PROCESSED
    assert second.call_type_prompt_hash is None
    assert second.scoring_prompt_hash is None
    assert second.gap_rubric_hash is None
    assert second.card_type_prompt_hash is None


async def test_steps_skipped_because_call_type_is_unknown_report_no_prompt_hash():
    responses = dict(ALL_GOOD_RESPONSES)
    responses["call_type"] = "not json"
    llm = StubLLMClient(responses=responses)
    prompts = make_prompts({})

    result = await advance_analysis(
        llm_client=llm, transcript=TRANSCRIPT, call_metadata={},
        prompts=prompts, record=fresh_record(),
    )

    assert result.scoring_status == STATUS_PENDING
    assert result.scoring_prompt_hash is None
    assert result.gap_rubric_hash is None
    assert result.call_type_prompt_hash is None
    assert result.card_type_prompt_hash == "h3"


async def test_fail_analysis_reports_no_prompt_hashes_at_all():
    record = fresh_record()
    record["call_type_status"] = STATUS_PROCESSED
    record["call_type"] = "Demo"

    result = fail_analysis(record=record, error="transcript did not validate")

    assert result.call_type_prompt_hash is None
    assert result.scoring_prompt_hash is None
    assert result.gap_rubric_hash is None
    assert result.card_type_prompt_hash is None


async def test_by_content_hash_covers_the_prompts_the_run_can_use():
    demo_gap_prompt = PromptFile(
        kind="gap_rubric", call_type="Demo", mode="descriptiononly", label="v1",
        content="rubric", content_hash="h4",
    )
    prompts = make_prompts({CallType.DEMO: demo_gap_prompt})

    # h5/h6 are the two gap-verification prompts. They are not call-type-scoped,
    # so they are reachable even when call_type never resolved.
    assert set(prompts.by_content_hash(CallType.DEMO)) == {"h1", "h2", "h3", "h4", "h5", "h6"}
    assert set(prompts.by_content_hash(None)) == {"h1", "h3", "h5", "h6"}


class TransportFailureLLMClient:
    """Raises a gateway transport error for one step and behaves like the
    stub for the rest. These errors are not LLMOutputError, so they used to
    escape _run_step and abort the entire batch run."""

    def __init__(self, responses, failing_step, error):
        self._stub = StubLLMClient(responses=responses)
        self._failing_step = failing_step
        self._error = error

    @property
    def calls(self):
        return self._stub.calls

    async def complete_structured(self, **kwargs):
        if kwargs.get("response_key") == self._failing_step:
            raise self._error
        return await self._stub.complete_structured(**kwargs)


def _request():
    return httpx.Request("POST", "http://gateway.invalid/chat/completions")


TRANSPORT_ERRORS = [
    APITimeoutError(request=_request()),
    APIStatusError(
        "Internal Server Error",
        response=httpx.Response(500, request=_request()),
        body=None,
    ),
]


@pytest.mark.parametrize("error", TRANSPORT_ERRORS)
async def test_gateway_transport_error_fails_only_that_step(error):
    demo_gap_prompt = PromptFile(
        kind="gap_rubric", call_type="Demo", mode="descriptiononly", label="v1",
        content="rubric", content_hash="h4",
    )
    prompts = make_prompts({CallType.DEMO: demo_gap_prompt})
    llm = TransportFailureLLMClient(ALL_GOOD_RESPONSES, "scoring", error)

    result = await advance_analysis(
        llm_client=llm, transcript=TRANSCRIPT, call_metadata={},
        prompts=prompts, record=fresh_record(),
    )

    assert result.scoring_status == STATUS_FAILED
    assert result.scoring_error
    assert result.call_type_status == STATUS_PROCESSED
    assert result.gap_status == STATUS_PROCESSED
    assert result.card_type_status == STATUS_PROCESSED
    assert result.overall_status == STATUS_FAILED


async def test_gateway_transport_error_escalates_to_failed_permanent_after_max_retries():
    demo_gap_prompt = PromptFile(
        kind="gap_rubric", call_type="Demo", mode="descriptiononly", label="v1",
        content="rubric", content_hash="h4",
    )
    prompts = make_prompts({CallType.DEMO: demo_gap_prompt})
    llm = TransportFailureLLMClient(
        ALL_GOOD_RESPONSES, "scoring", APITimeoutError(request=_request())
    )

    record = fresh_record()
    for _ in range(3):
        result = await advance_analysis(
            llm_client=llm, transcript=TRANSCRIPT, call_metadata={},
            prompts=prompts, record=record, max_retries=2,
        )
        record = result.__dict__

    assert result.scoring_status == STATUS_FAILED_PERMANENT
    assert result.dead_letter_at is not None
