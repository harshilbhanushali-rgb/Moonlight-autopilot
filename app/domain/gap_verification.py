"""Second-pass entailment check: does a gap's evidence bear out its claim?

`citation.py` proves a quote was said. This proves the quote *means* what the
gap says it means — a failure that citation checking structurally cannot see,
because the quotes involved are all genuine speech.

Measured over 46 real calls, roughly two thirds of gaps did not survive manual
review, and the dominant pattern was evidence that disproves its own claim:
"no pre-call research" cited to the rep naming the client's stack, "no
committed timeline" cited to the rep committing to one. Nothing in the pipeline
asked whether the quote supported the theme, so nothing caught it.

Two kinds of gap need two different questions, and the split is load-bearing:

- `dialogue` gaps get the quote plus a few turns either side. The narrow window
  is the point — several errors were only visible because a colleague resolved
  the issue in the very next turn, which a whole-transcript pass overlooked.
- `explanation` gaps get the entire transcript, because they are almost always
  claims that something never happened, and an absence cannot be disproved from
  an excerpt. This is where the worst error found in the audit lived: a claim
  that no customer stories were given, on a call that named two with figures.

This is still purely LLM reasoning about the transcript — no rule-based check
re-derives a gap. It only ever removes gaps; it never invents one.
"""

import json
from dataclasses import dataclass, field

from app.domain.errors import LLMOutputError
from app.domain.response_models import GapVerificationResponse
from app.domain.transcript import Transcript
from app.domain.types import Gap, Verdict
from app.llm.client import LLMMessage, StructuredOutputError
from app.prompts.registry import PromptFile

STEP = "gap_verification"

DEFAULT_BATCH_SIZE = 5
DEFAULT_WINDOW_TURNS = 3


@dataclass(frozen=True)
class GapJudgement:
    """One gap's verdict plus the model's reasoning for it.

    The reasoning is kept rather than discarded because a rejected gap is the
    rubric's bug report: "this theme fired on its own counter-example" is only
    actionable if you can see why. Dropping rejects silently also makes a call
    with no gaps indistinguishable from a call whose gaps were all killed.
    """

    gap: Gap
    verdict: Verdict
    reason: str
    evidence_quote: str


@dataclass(frozen=True)
class VerificationOutcome:
    # Every gap judged, in the order it was given. `kept` and `rejected` are
    # views over this so nothing is thrown away at the boundary.
    judgements: list[GapJudgement] = field(default_factory=list)
    dialogue_prompt_hash: str | None = None
    explanation_prompt_hash: str | None = None

    @property
    def kept(self) -> list[Gap]:
        return [j.gap for j in self.judgements if j.verdict is Verdict.SUPPORTED]

    @property
    def rejected(self) -> list[GapJudgement]:
        return [j for j in self.judgements if j.verdict is not Verdict.SUPPORTED]

    @property
    def rejected_by_verdict(self) -> dict[str, int]:
        counts: dict[str, int] = {}
        for judgement in self.rejected:
            counts[judgement.verdict.value] = counts.get(judgement.verdict.value, 0) + 1
        return counts


def _window(transcript: Transcript, gap: Gap, window_turns: int) -> str:
    """The turns around the quote. Located by timestamp, which is exact because
    `citation.verify_citations` has already re-anchored it to the turn the
    quote starts in — so this must run after citation validation, not before.
    """
    anchor = next(
        (i for i, turn in enumerate(transcript.turns) if turn.timestamp == gap.timestamp),
        None,
    )
    if anchor is None:
        # Should be unreachable post-citation-validation; degrade to the whole
        # transcript rather than judging a claim with no context at all.
        turns = transcript.turns
    else:
        low = max(0, anchor - window_turns)
        turns = transcript.turns[low : anchor + window_turns + 1]
    return "\n".join(f"[{t.timestamp}] {t.speaker}: {t.text}" for t in turns)


def _dialogue_batch_text(transcript, batch, window_turns) -> str:
    blocks = []
    for index, gap in batch:
        blocks.append(
            f"--- FINDING {index} ---\n"
            f"CLAIM: {gap.theme}\n"
            f"QUOTE: {gap.evidence}\n"
            f"CONTEXT:\n{_window(transcript, gap, window_turns)}"
        )
    return "\n\n".join(blocks)


def _explanation_batch_text(transcript, batch) -> str:
    findings = "\n\n".join(
        f"--- FINDING {index} ---\nCLAIM: {gap.theme}\nDETAIL: {gap.evidence}"
        for index, gap in batch
    )
    return f"FULL TRANSCRIPT:\n{transcript.render_for_prompt()}\n\n{findings}"


async def _judge(llm_client, *, prompt: PromptFile, body: str, response_key: str, batch):
    """One gateway call for one batch. Returns {gap index -> Verdict}."""
    try:
        response = await llm_client.complete_structured(
            messages=[
                LLMMessage(role="system", content=prompt.content),
                LLMMessage(role="user", content=body),
            ],
            response_model=GapVerificationResponse,
            response_key=response_key,
        )
    except StructuredOutputError as exc:
        raise LLMOutputError(STEP, exc.raw_content, exc.detail) from exc

    returned = {item.index: item for item in response.parsed.verdicts}
    asked = {index for index, _ in batch}
    # Strict both ways. A missing verdict would let an unjudged gap through as
    # if it had been verified; an unexpected index means the response is not
    # describing the batch we sent, so its other verdicts cannot be trusted
    # to belong to the gaps we think they do.
    if returned.keys() != asked:
        raise LLMOutputError(
            STEP,
            response.content,
            f"verification returned verdicts for {sorted(returned)} "
            f"but was asked about {sorted(asked)}",
        )
    return returned


async def verify_gap_claims(
    *,
    llm_client,
    gaps: list[Gap],
    transcript: Transcript,
    dialogue_prompt: PromptFile,
    explanation_prompt: PromptFile,
    batch_size: int = DEFAULT_BATCH_SIZE,
    window_turns: int = DEFAULT_WINDOW_TURNS,
) -> VerificationOutcome:
    """Returns only the gaps whose evidence was judged to support them.

    Gaps keep their original index while batched so a reordered response still
    maps back to the right gap — matching on list position would silently drop
    the wrong finding.
    """
    if not gaps:
        return VerificationOutcome()

    indexed = list(enumerate(gaps))
    dialogue = [(i, g) for i, g in indexed if g.evidence_type == "dialogue"]
    explanation = [(i, g) for i, g in indexed if g.evidence_type != "dialogue"]

    judged: dict[int, object] = {}
    for kind, items, prompt, body_for in (
        ("dialogue", dialogue, dialogue_prompt,
         lambda b: _dialogue_batch_text(transcript, b, window_turns)),
        ("explanation", explanation, explanation_prompt,
         lambda b: _explanation_batch_text(transcript, b)),
    ):
        for start in range(0, len(items), batch_size):
            batch = items[start : start + batch_size]
            judged.update(
                await _judge(
                    llm_client,
                    prompt=prompt,
                    body=body_for(batch),
                    response_key=f"{STEP}_{kind}",
                    batch=batch,
                )
            )

    return VerificationOutcome(
        # Original gap order, not the dialogue-then-explanation order the
        # batching imposed — rejects are read alongside the call's gap list.
        judgements=[
            GapJudgement(
                gap=gap,
                verdict=judged[index].verdict,
                reason=judged[index].reason,
                evidence_quote=judged[index].evidence_quote,
            )
            for index, gap in indexed
        ],
        dialogue_prompt_hash=dialogue_prompt.content_hash if dialogue else None,
        explanation_prompt_hash=explanation_prompt.content_hash if explanation else None,
    )
