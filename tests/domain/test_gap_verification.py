"""Entailment checking: does the evidence actually support the claim?

Citation validation (app/domain/citation.py) proves a quote was said. This
proves the quote means what the gap says it means — the failure mode that
citation checking cannot see, because the quotes in question are all real.
"""

import json

import pytest

from app.domain.errors import LLMOutputError
from app.domain.gap_verification import verify_gap_claims
from app.domain.transcript import Transcript, TranscriptSpeaker, TranscriptTurn
from app.domain.types import Gap, Verdict
from app.prompts.registry import PromptFile


DIALOGUE_PROMPT = PromptFile(
    kind="gap_verification", mode="dialogue", label="v1",
    content="Does the quote support the claim?", content_hash="dial-hash",
)
EXPLANATION_PROMPT = PromptFile(
    kind="gap_verification", mode="explanation", label="v1",
    content="Is this absence claim contradicted anywhere?", content_hash="expl-hash",
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
        TranscriptTurn(
            speaker="Keisha", speaker_id=1, text="It is overdue honestly.", start_s=180
        ),
        # Far enough from the quote above to fall outside a 3-turn window.
        TranscriptTurn(
            speaker="Scott",
            speaker_id=0,
            text="Tenet has been really successful with us.",
            start_s=400,
        ),
        TranscriptTurn(speaker="Keisha", speaker_id=1, text="Good to know.", start_s=430),
    ],
)


class SequencedLLMClient:
    """Like StubLLMClient but serves a different canned response per call for
    the same key, so a batched request that spills into two calls can be given
    two distinct responses. Validates through the real response model, so a bad
    fixture fails exactly as a bad gateway response would."""

    def __init__(self, responses):
        self._queues = {k: list(v) if isinstance(v, list) else [v] for k, v in responses.items()}
        self.calls = []

    async def complete_structured(self, *, messages, response_model, response_key=None, **_):
        from app.llm.client import RecordedCall, StructuredLLMResponse, StructuredOutputError

        self.calls.append(RecordedCall(messages=messages, response_key=response_key))
        queue = self._queues.get(response_key) or []
        if not queue:
            raise AssertionError(f"no canned response left for {response_key!r}")
        raw = queue.pop(0)
        try:
            return StructuredLLMResponse(parsed=response_model.model_validate_json(raw), content=raw)
        except Exception as exc:
            raise StructuredOutputError(str(exc), raw_content=raw) from exc


def dialogue_gap(theme, evidence, timestamp):
    return Gap(
        theme=theme, evidence_type="dialogue", evidence=evidence,
        timestamp=timestamp, confidence="high",
    )


def explanation_gap(theme, evidence):
    return Gap(
        theme=theme, evidence_type="explanation", evidence=evidence,
        timestamp=None, confidence="high",
    )


def verdicts(*pairs):
    return json.dumps(
        {
            "verdicts": [
                {"index": i, "verdict": v, "reason": f"reason-{i}", "evidence_quote": f"q-{i}"}
                for i, v in pairs
            ]
        }
    )


async def run(gaps, *, dialogue=None, explanation=None, batch_size=5):
    llm = SequencedLLMClient(
        {
            "gap_verification_dialogue": dialogue,
            "gap_verification_explanation": explanation,
        }
    )
    outcome = await verify_gap_claims(
        llm_client=llm,
        gaps=gaps,
        transcript=TRANSCRIPT,
        dialogue_prompt=DIALOGUE_PROMPT,
        explanation_prompt=EXPLANATION_PROMPT,
        batch_size=batch_size,
    )
    return outcome, llm


async def test_a_gap_whose_quote_supports_it_is_kept():
    gap = dialogue_gap("No Pre-Call Research", "I saw you're using Symphony", "01:23")

    outcome, _ = await run([gap], dialogue=verdicts((0, "supported")))

    assert outcome.kept == [gap]


async def test_a_gap_whose_quote_contradicts_it_is_dropped():
    """The real id=57 case: the cited proof of "no pre-call research" is the
    rep naming the client's stack."""
    gap = dialogue_gap("No Pre-Call Research", "I saw you're using Symphony", "01:23")

    outcome, _ = await run([gap], dialogue=verdicts((0, "contradicted")))

    assert outcome.kept == []


async def test_a_gap_whose_quote_is_unrelated_is_dropped():
    gap = dialogue_gap("No Competitive Framing", "Good to know.", "03:00")

    outcome, _ = await run([gap], dialogue=verdicts((0, "irrelevant")))

    assert outcome.kept == []


async def test_only_the_window_around_the_quote_is_sent_not_the_whole_transcript():
    """The window is what makes this work: measured cases were rejected by a
    rebuttal one or two turns later that a whole-transcript pass missed."""
    gap = dialogue_gap("Some Theme", "I saw you're using Symphony", "01:23")

    _, llm = await run([gap], dialogue=verdicts((0, "supported")))

    sent = " ".join(m.content for m in llm.calls[0].messages)
    assert "Symphony" in sent
    assert "Tenet has been really successful" not in sent


async def test_an_explanation_claim_is_checked_against_the_entire_transcript():
    """An absence claim cannot be disproved from a window — the counterexample
    could be anywhere. The real id=274 case claimed no customer stories were
    given while Uber and Banfield were both named."""
    gap = explanation_gap("No Case Study Evidence", "No customer stories were given.")

    _, llm = await run([gap], explanation=verdicts((0, "contradicted")))

    sent = " ".join(m.content for m in llm.calls[0].messages)
    assert "Tenet has been really successful" in sent
    assert "Good to know" in sent


async def test_dialogue_and_explanation_gaps_are_asked_different_questions():
    gaps = [
        dialogue_gap("D", "I saw you're using Symphony", "01:23"),
        explanation_gap("E", "Nothing was agreed."),
    ]

    outcome, llm = await run(
        gaps, dialogue=verdicts((0, "supported")), explanation=verdicts((1, "supported"))
    )

    used = {c.response_key for c in llm.calls}
    assert used == {"gap_verification_dialogue", "gap_verification_explanation"}
    assert len(outcome.kept) == 2


async def test_up_to_batch_size_gaps_are_verified_in_a_single_call():
    gaps = [dialogue_gap(f"T{i}", "I saw you're using Symphony", "01:23") for i in range(5)]

    _, llm = await run(gaps, dialogue=verdicts(*[(i, "supported") for i in range(5)]))

    assert len(llm.calls) == 1


async def test_gaps_beyond_batch_size_spill_into_a_second_call():
    gaps = [dialogue_gap(f"T{i}", "I saw you're using Symphony", "01:23") for i in range(3)]

    _, llm = await run(
        gaps,
        dialogue=[verdicts((0, "supported"), (1, "supported")), verdicts((2, "supported"))],
        batch_size=2,
    )

    assert len(llm.calls) == 2


async def test_verdicts_are_matched_to_gaps_by_index_not_by_position():
    """A batched response that comes back reordered must still drop the right
    gap — matching by list position would silently drop the wrong one."""
    gaps = [
        dialogue_gap("KEEP", "I saw you're using Symphony", "01:23"),
        dialogue_gap("DROP", "Good to know.", "03:00"),
    ]

    outcome, _ = await run(gaps, dialogue=verdicts((1, "contradicted"), (0, "supported")))

    assert [g.theme for g in outcome.kept] == ["KEEP"]


async def test_a_response_missing_a_verdict_fails_the_step():
    """Silently keeping an unjudged gap would let a malformed response pass
    unverified output off as verified."""
    gaps = [
        dialogue_gap("A", "I saw you're using Symphony", "01:23"),
        dialogue_gap("B", "Good to know.", "03:00"),
    ]

    with pytest.raises(LLMOutputError):
        await run(gaps, dialogue=verdicts((0, "supported")))


async def test_a_verdict_for_a_gap_that_was_not_asked_about_fails_the_step():
    gap = dialogue_gap("A", "I saw you're using Symphony", "01:23")

    with pytest.raises(LLMOutputError):
        await run([gap], dialogue=verdicts((0, "supported"), (7, "supported")))


async def test_no_gaps_means_no_llm_calls_at_all():
    outcome, llm = await run([])

    assert outcome.kept == []
    assert llm.calls == []


async def test_a_rejected_gap_keeps_its_verdict_and_the_model_s_reasoning():
    """Rejects are the rubric's bug report — "this theme fired on its own
    counter-example" is only actionable if the reasoning survives. Dropping
    them silently also makes a zero-gap call indistinguishable from a call
    whose gaps were all killed."""
    gap = dialogue_gap("No Pre-Call Research", "I saw you're using Symphony", "01:23")

    outcome, _ = await run([gap], dialogue=verdicts((0, "contradicted")))

    assert outcome.kept == []
    (rejected,) = outcome.rejected
    assert rejected.gap is gap
    assert rejected.verdict is Verdict.CONTRADICTED
    assert rejected.reason == "reason-0"
    assert rejected.evidence_quote == "q-0"


async def test_kept_gaps_carry_their_reasoning_too():
    gap = dialogue_gap("A", "I saw you're using Symphony", "01:23")

    outcome, _ = await run([gap], dialogue=verdicts((0, "supported")))

    (judgement,) = outcome.judgements
    assert judgement.verdict is Verdict.SUPPORTED
    assert judgement.reason == "reason-0"


async def test_judgements_are_returned_in_the_original_gap_order():
    """Rejects are reported per call, so their order has to match the gap list
    a human would be reading alongside them."""
    gaps = [
        dialogue_gap("FIRST", "I saw you're using Symphony", "01:23"),
        explanation_gap("SECOND", "Nothing agreed."),
        dialogue_gap("THIRD", "Good to know.", "07:10"),
    ]

    outcome, _ = await run(
        gaps,
        dialogue=verdicts((0, "supported"), (2, "contradicted")),
        explanation=verdicts((1, "supported")),
    )

    assert [j.gap.theme for j in outcome.judgements] == ["FIRST", "SECOND", "THIRD"]


async def test_the_prompt_hash_is_reported_only_for_the_kind_of_gap_present():
    """Provenance must not claim the explanation verifier ran when this call
    had nothing for it to judge."""
    gap = dialogue_gap("A", "I saw you're using Symphony", "01:23")

    outcome, _ = await run([gap], dialogue=verdicts((0, "supported")))

    assert outcome.dialogue_prompt_hash == "dial-hash"
    assert outcome.explanation_prompt_hash is None
