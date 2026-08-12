"""Per-step LLM response contracts.

Each model is handed to the gateway as `response_format`, which constrains
generation to its JSON schema. A fenced response, a missing field, or an
off-enum value (`"very high"`, a bare number) becomes structurally impossible
rather than something we detect after the fact — which is what previously made
whole batches fail on `gemini-2.5-flash` wrapping its JSON in ```json fences.

The enums come from app.domain.types so the allowed values are declared exactly
once and flow through to the schema; the gateway resolves the $defs/$ref that
Pydantic generates for them, and hands back real enum members.

One rule these schemas deliberately cannot express: the gap timestamp
invariant (`dialogue` requires a timestamp, `explanation` must not have one).
That is a conditional constraint, unsupported by the schema subset the gateway
accepts — app.domain.gap_analysis still enforces it in code.
"""

from typing import Literal

from pydantic import BaseModel

from app.domain.types import CallScore, CallType, CardType, Verdict


class CallTypeResponse(BaseModel):
    call_type: CallType


class CallScoreResponse(BaseModel):
    call_score: CallScore


class CardTypeResponse(BaseModel):
    card_type: CardType


class GapItem(BaseModel):
    theme: str
    evidence_type: Literal["dialogue", "explanation"]
    evidence: str
    timestamp: str | None
    confidence: Literal["high", "medium", "low"]


class GapAnalysisResponse(BaseModel):
    gaps: list[GapItem]


class GapVerdictItem(BaseModel):
    """`index` is the gap's position in the batch as presented in the prompt.

    Carried explicitly rather than relying on response order: a reordered list
    would otherwise drop the wrong gap silently. app.domain.gap_verification
    checks the returned indices are exactly the ones asked about.
    """

    index: int
    verdict: Verdict
    reason: str
    # The words from the transcript that carry the verdict — what supports the
    # claim, or what disproves it. Required because a verifier that only has to
    # emit a label defaults to agreeing: measured over 86 real gaps it returned
    # "supported" 71% of the time, including cases where the rebuttal sat one
    # turn away. Having to produce the actual words makes that harder to fake.
    # "none" is the expected value for an `irrelevant` verdict.
    evidence_quote: str


class GapVerificationResponse(BaseModel):
    verdicts: list[GapVerdictItem]


class GapFillResponse(BaseModel):
    gap: str
