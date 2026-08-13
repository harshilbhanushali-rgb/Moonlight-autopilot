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
from app.domain.scoring import score_call_by_category
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
    "scores, mean, expected",
    [
        ([5, 5, 4, 4, 4, 4, 4, 4, 4, 4], 4.2, CallScore.HIGH),    # exactly on the High edge
        ([5, 4, 4, 4, 4, 4, 4, 4, 4, 4], 4.1, CallScore.MEDIUM),  # one point below it
        ([3, 3, 3, 3, 3, 3, 3, 3, 2, 2], 2.8, CallScore.MEDIUM),  # exactly on the Medium edge
        ([3, 3, 3, 3, 3, 3, 3, 2, 2, 2], 2.7, CallScore.LOW),     # one point below it
        ([1] * 10, 1.0, CallScore.LOW),
    ],
)
async def test_each_band_is_inclusive_at_its_lower_edge(scores, mean, expected):
    """Root cause 2 in miniature: adjacent rows here differ by ONE category
    moving one point, and that is enough to change the tier. The arithmetic is
    now in code so at least it is the same arithmetic every night."""
    result = await _score(scores)

    assert result.value.mean == pytest.approx(mean)
    assert result.value.tier == expected


async def test_na_categories_are_excluded_from_the_mean_not_scored_as_one():
    """Root cause 3. A Pricing call that never reached procurement was scoring 1
    on every closing category, which is what sank 8 of 9 to Low. An occasion
    that never arose is not a failure by the rep."""
    result = await _score([5, 5, 4, "N/A", "N/A", "N/A", "N/A", 5, 4, 5])

    assert result.value.mean == pytest.approx(4.6666666, abs=1e-4)  # 28/6, not 28/10
    assert result.value.tier == CallScore.HIGH
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
