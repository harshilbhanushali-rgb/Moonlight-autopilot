from app.domain.classification import run_enum_classification_step
from app.domain.errors import LLMOutputError
from app.domain.response_models import CallScoreBreakdownResponse, CallScoreResponse
from app.domain.types import CallScore, ClassificationResult, ScoreBreakdown, ScoredCategory
from app.llm.client import LLMMessage, StructuredOutputError
from app.prompts.registry import PromptFile

STEP = "scoring"

# Band thresholds on the mean of the scored categories. These are the business
# team's own numbers from the v1 prompts, kept deliberately: setting them from
# 46 calls of our own output would be overfitting, and what the mean
# distribution actually does is part of the Phase A result rather than an input
# to it. Recalibration is a separate decision with its own A/B.
HIGH_THRESHOLD = 4.2
MEDIUM_THRESHOLD = 2.8

# The rubric names ten categories and the response schema cannot require a
# fixed-length list. Nine returned rows would silently raise the mean if the
# missing one was about to score 1, so the count is checked here instead.
EXPECTED_CATEGORY_COUNT = 10

# A mean over one or two categories is not a call score. Calls with no
# conversation are already refused by the input gate, so a response this sparse
# is an LLM failure and belongs on the retry path.
MIN_SCORED_CATEGORIES = 3

_NOT_APPLICABLE = "N/A"


async def score_call(
    *, llm_client, transcript: str, prompt: PromptFile
) -> ClassificationResult[CallScore]:
    """The v1 contract: the model returns the tier and nothing else.

    Kept so `app/services/eval/call_score_ab.py` can run the v1 prompts as the
    control arm. Production uses `score_call_by_category`.
    """
    return await run_enum_classification_step(
        llm_client=llm_client,
        step=STEP,
        field_name="call_score",
        response_model=CallScoreResponse,
        input_text=transcript,
        prompt=prompt,
    )


def _tier_for(mean: float) -> CallScore:
    if mean >= HIGH_THRESHOLD:
        return CallScore.HIGH
    if mean >= MEDIUM_THRESHOLD:
        return CallScore.MEDIUM
    return CallScore.LOW


def build_breakdown(items, *, expected_category_count: int) -> ScoreBreakdown:
    """Turns the model's category rows into a tier, or refuses to.

    Every rejection here means the mean would otherwise have been computed over
    a denominator that doesn't describe the rubric — which is exactly the way a
    score can look confident while being arithmetic on the wrong set.
    """
    if len(items) != expected_category_count:
        raise ValueError(
            f"expected {expected_category_count} categories, got {len(items)}"
        )

    names = [item.name for item in items]
    if len(set(names)) != len(names):
        raise ValueError("duplicate category names would double-weight the mean")

    categories = [
        ScoredCategory(
            name=item.name,
            score=None if item.score == _NOT_APPLICABLE else int(item.score),
            evidence=item.evidence,
        )
        for item in items
    ]

    scored = [c.score for c in categories if c.score is not None]
    if len(scored) < MIN_SCORED_CATEGORIES:
        raise ValueError(
            f"only {len(scored)} of {len(categories)} categories applied to this "
            "call; too few to score it"
        )

    mean = sum(scored) / len(scored)
    return ScoreBreakdown(tier=_tier_for(mean), categories=categories, mean=mean)


async def score_call_by_category(
    *,
    llm_client,
    transcript: str,
    prompt: PromptFile,
    expected_category_count: int = EXPECTED_CATEGORY_COUNT,
) -> ClassificationResult[ScoreBreakdown]:
    """Phase A: the model scores each rubric category, the tier is arithmetic.

    See docs/superpowers/specs/2026-08-13-call-score-redesign-design.md. The v1
    prompt asked the model to average ten numbers internally and return only the
    tier, which made a flipped score unattributable and left the arithmetic
    unaudited.
    """
    try:
        response = await llm_client.complete_structured(
            messages=[
                LLMMessage(role="system", content=prompt.content),
                LLMMessage(role="user", content=transcript),
            ],
            response_model=CallScoreBreakdownResponse,
            response_key=STEP,
        )
    except StructuredOutputError as exc:
        raise LLMOutputError(STEP, exc.raw_content, exc.detail) from exc

    try:
        breakdown = build_breakdown(
            response.parsed.categories, expected_category_count=expected_category_count
        )
    except ValueError as exc:
        raise LLMOutputError(STEP, response.content, str(exc)) from exc

    return ClassificationResult(
        value=breakdown,
        prompt_content_hash=prompt.content_hash,
        raw_response=response.content,
    )
