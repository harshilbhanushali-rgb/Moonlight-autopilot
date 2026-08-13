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


@dataclass(frozen=True)
class ScoredCategory:
    """One rubric category's outcome on one call.

    `score is None` means N/A — the call never created an occasion for the
    behaviour, which is different from doing it badly and is excluded from the
    mean. `evidence` is the transcript words the model says decided the score;
    it is carried for the moderator and is deliberately NOT verified against the
    transcript the way gap citations are (app/domain/citation.py) — that check
    exists because a fabricated gap quote reaches a moderator as an uncheckable
    card, whereas a subscore is only ever read alongside the tier it explains.
    """

    name: str
    score: int | None
    evidence: str


@dataclass(frozen=True)
class ScoreBreakdown:
    """A call score and the arithmetic that produced it.

    Before this, the scoring step returned only the tier, so a score that
    flipped between two identical runs could not be attributed to anything. The
    mean is computed in code from the model's own category scores — arithmetic
    on an LLM judgement, not a re-derivation from the transcript, so the
    "no deterministic checks" boundary (which is about gap detection) is intact.
    """

    tier: CallScore
    categories: list[ScoredCategory]
    mean: float

    @property
    def scored_count(self) -> int:
        return sum(1 for c in self.categories if c.score is not None)

    @property
    def na_count(self) -> int:
        return sum(1 for c in self.categories if c.score is None)


class ExclusionReason(str, Enum):
    """Why the input gate refused to analyse a call.

    Stored on `call_storage.excluded_reason`, so these strings are a persisted
    contract — renaming one orphans the rows already carrying it.

    NO_CONVERSATION is reported in preference to NO_CLIENT_SPEECH when a call
    fails both: "there was nothing said" is more specific and more actionable
    than "the client said nothing".
    """

    NO_CONVERSATION = "no_conversation"
    NO_CLIENT_SPEECH = "no_client_speech"


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
