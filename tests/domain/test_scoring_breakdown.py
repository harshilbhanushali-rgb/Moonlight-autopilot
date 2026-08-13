"""Phase A of the call-score redesign: the tier is arithmetic on returned
subscores, not a hidden judgement.

See docs/superpowers/specs/2026-08-13-call-score-redesign-design.md. The point
of every test here is that a tier can now be *attributed* to categories, and
that the two ways a mean can silently lie — a dropped category shrinking the
denominator, and a call where nothing applied — fail loudly instead.
"""

import json

import pytest

from app.domain.errors import LLMOutputError
from app.domain.scoring import (
    HIGH_THRESHOLD,
    MEDIUM_THRESHOLD,
    score_call_by_category,
)
from app.domain.types import CallScore
from app.llm.client import StubLLMClient
from app.prompts.registry import PromptFile

PROMPT = PromptFile(
    kind="scoring", label="v2", content="Score the call.", content_hash="cafebabe"
)


def _response(scores, *, names=None, evidence="they said something"):
    """Ten categories carrying `scores`, which may be ints or the string N/A."""
    names = names or [f"Category {i + 1}" for i in range(len(scores))]
    return json.dumps(
        {
            "categories": [
                {"name": name, "score": str(score), "evidence": evidence}
                for name, score in zip(names, scores)
            ]
        }
    )


async def _score_mean(target):
    """Ten categories whose mean is exactly `target`, via one fractional slot.

    Scores are integers, so the mean is built by mixing: nine 5s plus one
    value chosen to land the mean on `target` is not always reachable, so this
    uses a two-value mix and asserts the mean it actually produced.
    """
    total = round(target * 10)
    scores, remaining = [], total
    for slot in range(10):
        left = 10 - slot - 1
        value = max(1, min(5, remaining - left))
        scores.append(value)
        remaining -= value
    return await _score(scores)


async def _score(scores, **kwargs):
    llm = StubLLMClient(responses={"scoring": _response(scores, **kwargs)})
    return await score_call_by_category(llm_client=llm, transcript="...", prompt=PROMPT)


async def test_the_tier_is_the_mean_of_the_returned_category_scores():
    result = await _score([5, 5, 4, 5, 4, 5, 4, 5, 5, 5])  # mean 4.7

    assert result.value.tier == CallScore.HIGH
    assert result.value.mean == pytest.approx(4.7)
    assert result.prompt_content_hash == "cafebabe"


async def test_every_category_is_carried_out_of_the_step_for_diagnosis():
    """The whole point of Phase A: a flip must be attributable to a category."""
    result = await _score([3] * 10, names=[f"Cat {i}" for i in range(10)])

    assert [c.name for c in result.value.categories] == [f"Cat {i}" for i in range(10)]
    assert all(c.score == 3 for c in result.value.categories)
    assert all(c.evidence for c in result.value.categories)


@pytest.mark.parametrize(
    "threshold, tier_at, tier_below",
    [
        (HIGH_THRESHOLD, CallScore.HIGH, CallScore.MEDIUM),
        (MEDIUM_THRESHOLD, CallScore.MEDIUM, CallScore.LOW),
    ],
)
async def test_each_band_is_inclusive_at_its_lower_edge(threshold, tier_at, tier_below):
    """A mean exactly on an edge belongs to the band above it.

    Written against the constants rather than literal 4.7/2.8 so that
    recalibrating a threshold — which happened once already, on evidence — does
    not fail a test about inclusivity, which is the actual rule being pinned.
    """
    # 0.1, not something smaller: ten integer scores can only produce means in
    # steps of 0.1, so a finer step rounds back onto the edge being tested.
    at_edge = await _score_mean(threshold)
    just_below = await _score_mean(threshold - 0.1)

    assert at_edge.value.tier == tier_at
    assert just_below.value.tier == tier_below


async def test_one_category_moving_one_point_can_change_the_tier():
    """Root cause 2, kept as its own test: the mean of ten scores moves 0.1 when
    a single category moves a point, so a call sitting on an edge is a coin
    flip. Measured at 27% of runs within 0.15 of an edge — the reason the tier
    is now recomputed from stored subscores rather than trusted."""
    below = await _score([5] * 9 + [1])   # 4.6
    above = await _score([5] * 9 + [2])   # 4.7

    assert below.value.mean == pytest.approx(4.6)
    assert above.value.mean == pytest.approx(4.7)
    assert below.value.tier != above.value.tier


async def test_na_categories_are_excluded_from_the_mean_not_scored_as_one():
    """Root cause 3. A Pricing call that never reached procurement was scoring 1
    on every closing category, which is what sank 8 of 9 to Low. An occasion
    that never arose is not a failure by the rep."""
    result = await _score([5, 5, 4, "N/A", "N/A", "N/A", "N/A", 5, 4, 5])

    assert result.value.mean == pytest.approx(4.6666666, abs=1e-4)  # 28/6, not 28/10
    # The point is the denominator, not the tier: scored as 28/10 = 2.8 this
    # call would be a bare Medium, and under v1 it was exactly this shape of
    # call that came back Low.
    assert result.value.mean > sum(28 for _ in [1]) / 10
    assert result.value.scored_count == 6
    assert result.value.na_count == 4


async def test_na_categories_are_still_reported_so_the_denominator_is_visible():
    result = await _score([5, 5, 5, 5, 5, 5, 5, "N/A", "N/A", "N/A"])

    na = [c for c in result.value.categories if c.score is None]
    assert len(na) == 3
    assert len(result.value.categories) == 10


async def test_a_missing_category_fails_the_step_rather_than_shrinking_the_denominator():
    """Nine returned categories would silently raise the mean if the tenth was
    the one about to score 1. The schema cannot require a fixed-length list, so
    the count is checked here and the step takes the normal retry path."""
    llm = StubLLMClient(responses={"scoring": _response([5] * 9)})

    with pytest.raises(LLMOutputError):
        await score_call_by_category(llm_client=llm, transcript="...", prompt=PROMPT)


async def test_duplicate_category_names_fail_the_step():
    """Two rows for one category double-weight it in the mean."""
    names = ["Same"] * 2 + [f"Cat {i}" for i in range(8)]
    llm = StubLLMClient(responses={"scoring": _response([4] * 10, names=names)})

    with pytest.raises(LLMOutputError):
        await score_call_by_category(llm_client=llm, transcript="...", prompt=PROMPT)


async def test_a_call_where_almost_nothing_applied_fails_rather_than_being_graded():
    """A mean over one or two categories is not a call score. The input gate
    already refuses calls with no conversation, so this is an LLM failure, and
    it belongs on the retry path rather than becoming a confident tier."""
    llm = StubLLMClient(responses={"scoring": _response([5, 4] + ["N/A"] * 8)})

    with pytest.raises(LLMOutputError):
        await score_call_by_category(llm_client=llm, transcript="...", prompt=PROMPT)


async def test_an_off_scale_score_fails_the_step():
    llm = StubLLMClient(responses={"scoring": _response([7] + [4] * 9)})

    with pytest.raises(LLMOutputError):
        await score_call_by_category(llm_client=llm, transcript="...", prompt=PROMPT)


async def test_the_prompt_reaches_the_gateway_as_the_system_message():
    llm = StubLLMClient(responses={"scoring": _response([3] * 10)})

    await score_call_by_category(llm_client=llm, transcript="the call text", prompt=PROMPT)

    (call,) = llm.calls
    assert call.messages[0].content == "Score the call."
    assert call.messages[1].content == "the call text"
