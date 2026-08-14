"""The neutral-framing arm of the verifier experiment.

Production asks "does this quote support this claim?", which presupposes the
claim and measured 71% approval. This arm asks "does this call exhibit theme
X?" with no claim and no pre-selected quote. The tests here pin the two
properties that make the comparison meaningful: the framing genuinely withholds
the claim, and a gap is only ever dropped on an affirmative "absent".
"""

import json

import pytest

from app.domain.errors import LLMOutputError
from app.domain.transcript import Transcript, TranscriptSpeaker, TranscriptTurn
from app.domain.types import Gap
from eval.neutral_framing import (
    NEUTRAL_DIALOGUE_PROMPT,
    NEUTRAL_EXPLANATION_PROMPT,
    Presence,
    dialogue_body,
    explanation_body,
    is_kept,
    judge_presence,
)

TRANSCRIPT = Transcript(
    speakers=[
        TranscriptSpeaker(id=0, name="Scott", email="scott@joveo.com", is_rep=True),
        TranscriptSpeaker(id=1, name="Keisha", email="keisha@acme.com", is_rep=False),
    ],
    turns=[
        TranscriptTurn(
            speaker="Scott",
            speaker_id=0,
            text="I saw you're using Symphony on your career site.",
            start_s=83,
        ),
        TranscriptTurn(
            speaker="Keisha",
            speaker_id=1,
            text="Yes, we are auditing our stack right now.",
            start_s=120,
        ),
        TranscriptTurn(
            speaker="Scott", speaker_id=0, text="Makes sense, plenty of teams are.", start_s=158
        ),
        # Far enough from the quote to fall outside a 1-turn window.
        TranscriptTurn(
            speaker="Keisha",
            speaker_id=1,
            text="Tenet has been really successful with us.",
            start_s=400,
        ),
    ],
)

DIALOGUE_GAP = Gap(
    theme="No Pre-Call Research",
    evidence_type="dialogue",
    evidence="I saw you're using Symphony on your career site.",
    timestamp="01:23",
    confidence="high",
)
EXPLANATION_GAP = Gap(
    theme="Success Metrics Not Agreed at Start",
    evidence_type="explanation",
    evidence="No KPIs were agreed anywhere on the call.",
    timestamp=None,
    confidence="high",
)


class StubClient:
    def __init__(self, raw):
        self._raw = raw
        self.calls = []

    async def complete_structured(self, *, messages, response_model, response_key=None, **_):
        from app.llm.client import RecordedCall, StructuredLLMResponse, StructuredOutputError

        self.calls.append(RecordedCall(messages=messages, response_key=response_key))
        try:
            return StructuredLLMResponse(
                parsed=response_model.model_validate_json(self._raw), content=self._raw
            )
        except Exception as exc:
            raise StructuredOutputError(str(exc), raw_content=self._raw) from exc


def test_the_dialogue_body_withholds_the_claim_and_the_pre_selected_quote():
    """The entire point of the reframe. If the body still handed over the gap's
    own evidence, the model would be answering the leading question again with a
    different wrapper, and the comparison would measure nothing."""
    body = dialogue_body(TRANSCRIPT, [(0, DIALOGUE_GAP)], window_turns=1)

    assert "CLAIM" not in body
    assert "QUOTE" not in body
    # The theme still has to be named — the question is "does the call exhibit
    # THIS theme", not "find any problem".
    assert "No Pre-Call Research" in body


def test_the_dialogue_body_shows_the_same_window_production_judges():
    """Context is held constant across the two arms so framing is the only
    variable. The window is still located by the gap's timestamp."""
    body = dialogue_body(TRANSCRIPT, [(0, DIALOGUE_GAP)], window_turns=1)

    assert "auditing our stack" in body
    assert "Symphony on your career site" in body
    # Outside the window, exactly as in production.
    assert "Tenet" not in body


def test_the_explanation_body_carries_the_whole_transcript():
    """An absence claim cannot be assessed from an excerpt — the same reason
    production hands explanation gaps the full transcript."""
    body = explanation_body(TRANSCRIPT, [(0, EXPLANATION_GAP)])

    assert "Tenet" in body
    assert "Symphony" in body
    assert "Success Metrics Not Agreed at Start" in body


def test_findings_are_labelled_by_index_so_verdicts_map_back():
    body = dialogue_body(TRANSCRIPT, [(3, DIALOGUE_GAP)], window_turns=1)
    assert "3" in body


def test_only_an_affirmative_absence_drops_a_gap():
    """`cannot_tell` must keep. A ±3-turn excerpt genuinely cannot settle a
    whole-call theme, and a reframed verifier that dropped on "I can't tell"
    would delete gaps for a structural reason rather than a substantive one."""
    assert is_kept(Presence.PRESENT) is True
    assert is_kept(Presence.CANNOT_TELL) is True
    assert is_kept(Presence.ABSENT) is False


@pytest.mark.asyncio
async def test_judge_presence_returns_a_verdict_per_finding():
    raw = json.dumps(
        {"findings": [{"index": 0, "presence": "present", "reason": "no stack question asked", "evidence_quote": "none"}]}
    )
    client = StubClient(raw)

    judged = await judge_presence(
        client,
        prompt_text=NEUTRAL_DIALOGUE_PROMPT,
        body="whatever",
        response_key="neutral_dialogue",
        batch=[(0, DIALOGUE_GAP)],
    )

    assert judged[0].presence is Presence.PRESENT


@pytest.mark.asyncio
async def test_a_response_that_does_not_answer_the_batch_is_rejected():
    """Same strictness as production: a missing verdict would let an unjudged
    gap through as if it had been assessed, and an unexpected index means the
    response is not describing the batch we sent."""
    raw = json.dumps(
        {"findings": [{"index": 7, "presence": "absent", "reason": "r", "evidence_quote": "none"}]}
    )
    client = StubClient(raw)

    with pytest.raises(LLMOutputError):
        await judge_presence(
            client,
            prompt_text=NEUTRAL_EXPLANATION_PROMPT,
            body="whatever",
            response_key="neutral_explanation",
            batch=[(0, EXPLANATION_GAP)],
        )
