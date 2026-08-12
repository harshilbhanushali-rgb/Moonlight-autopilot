from dataclasses import dataclass, field
from enum import Enum
from typing import Generic, TypeVar


class CallType(str, Enum):
    DISCOVERY = "Discovery"
    DEMO = "Demo"
    FOLLOW_UP_DEMO = "Follow-up Demo"
    PRICING_NEGOTIATION = "Pricing/Negotiation"
    TECHNICAL_INTEGRATION = "Technical Integration"
    KICKOFF = "Kick-off"


class CallScore(str, Enum):
    HIGH = "High"
    MEDIUM = "Medium"
    LOW = "Low"


class CardType(str, Enum):
    COACHING = "Coaching"
    RISK = "Risk"


class Verdict(str, Enum):
    """Whether a gap's evidence actually bears out its claim.

    Only SUPPORTED survives. The other two are separate values rather than one
    "rejected" because they describe different failures and the distinction is
    what tells the rubric owners which themes to rewrite: CONTRADICTED means
    the evidence disproves the claim (the rubric theme is firing on its own
    counter-example), IRRELEVANT means the evidence simply doesn't bear on it
    (the theme is being matched on topic, not on substance).
    """

    SUPPORTED = "supported"
    CONTRADICTED = "contradicted"
    IRRELEVANT = "irrelevant"


T = TypeVar("T")


@dataclass(frozen=True)
class ClassificationResult(Generic[T]):
    value: T
    prompt_content_hash: str
    raw_response: str
    # Content hashes of any *additional* prompts a step used beyond its main
    # one, keyed by a provenance name. Only the gap step has these today: its
    # two verification prompts materially change which gaps survive, so an edit
    # to either must be as traceable as an edit to the rubric itself.
    extra_prompt_hashes: dict[str, str] = field(default_factory=dict)


@dataclass(frozen=True)
class Gap:
    theme: str
    evidence_type: str | None = None
    evidence: str | None = None
    timestamp: str | None = None
    confidence: str | None = None


@dataclass(frozen=True)
class CardTypeContext:
    """Optional-fields context for Card Type classification.

    Batch pipeline populates transcript/call_metadata/existing_score/comment_text
    fully; Manual Card Auto-Fill populates whatever it has, at minimum
    comment_text. Same classification function and machinery run either way —
    see app.core.card_type.classify_card_type.
    """

    comment_text: str | None = None
    transcript: str | None = None
    call_metadata: dict | None = None
    existing_score: str | None = None
    # The gaps found for this call, when the gap step ran. `None` (step failed
    # or was skipped) and `[]` (ran, found nothing) mean different things and
    # are rendered differently — see card_type.build_context_text.
    gaps: list[Gap] | None = None
