from dataclasses import dataclass
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


T = TypeVar("T")


@dataclass(frozen=True)
class ClassificationResult(Generic[T]):
    value: T
    prompt_content_hash: str
    raw_response: str


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
