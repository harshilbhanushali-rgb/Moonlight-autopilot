"""Citation validation: does a gap's quote actually appear in the transcript,
and does its timestamp point at the moment it was said?

This checks the *citation*, never the judgement — same category as the
dialogue/explanation timestamp invariant in gap_analysis. Nothing here decides
whether a gap is real; it decides whether a moderator can find the quote.
"""

import pytest

from app.domain.citation import verify_citations
from app.domain.errors import LLMOutputError
from app.domain.transcript import Transcript, TranscriptTurn
from app.domain.types import Gap


TRANSCRIPT = Transcript(
    turns=[
        TranscriptTurn(speaker="Scott", text="So what does your current stack look like?", start_s=0),
        TranscriptTurn(speaker="Matt", text="We are coming off Radancy over the next year.", start_s=69),
        TranscriptTurn(speaker="Jill", text="I do have to run. I'm getting pinged.", start_s=124),
        TranscriptTurn(speaker="Matt", text="Okay. That'll work. Way over.", start_s=131),
    ]
)


def dialogue(evidence, timestamp, theme="Some Theme"):
    return Gap(
        theme=theme,
        evidence_type="dialogue",
        evidence=evidence,
        timestamp=timestamp,
        confidence="high",
    )


def test_a_quote_that_matches_its_turn_is_returned_unchanged():
    gap = dialogue("We are coming off Radancy over the next year.", "01:09")

    assert verify_citations([gap], TRANSCRIPT, raw_content="{}") == [gap]


def test_a_wrong_timestamp_is_corrected_to_the_turn_the_quote_was_said_in():
    """Measured: 8 of 46 citations were off by more than 5s and 4 pointed at a
    different speaker's turn, sending moderators to the wrong moment."""
    gap = dialogue("We are coming off Radancy over the next year.", "00:43")

    (fixed,) = verify_citations([gap], TRANSCRIPT, raw_content="{}")

    assert fixed.timestamp == "01:09"
    assert fixed.evidence == gap.evidence


def test_a_quote_starting_mid_turn_still_anchors_to_that_turn():
    gap = dialogue("coming off Radancy", "00:00")

    (fixed,) = verify_citations([gap], TRANSCRIPT, raw_content="{}")

    assert fixed.timestamp == "01:09"


def test_a_quote_elided_with_an_ellipsis_is_matched_segment_by_segment():
    gap = dialogue("So what does your current stack ... over the next year.", "00:00")

    (fixed,) = verify_citations([gap], TRANSCRIPT, raw_content="{}")

    assert fixed.timestamp == "00:00"


def test_a_speaker_label_the_model_prepended_is_ignored_when_matching():
    """Measured: 12 of 58 quotes failed a verbatim match purely because the
    model added `Speaker:` labels that are not in the transcript."""
    gap = dialogue('Matt: "We are coming off Radancy over the next year."', "01:09")

    (fixed,) = verify_citations([gap], TRANSCRIPT, raw_content="{}")

    assert fixed.timestamp == "01:09"


def test_a_composite_quote_anchors_to_the_turn_it_starts_in():
    """Measured: 10 quotes were stitched across turns, 5 across speakers. The
    words are real, so the citation is salvageable — it just has to point at
    the first of them, not at whichever turn the model picked."""
    gap = dialogue("I do have to run. I'm getting pinged. Okay. That'll work. Way over.", "02:11")

    (fixed,) = verify_citations([gap], TRANSCRIPT, raw_content="{}")

    assert fixed.timestamp == "02:04"


def test_punctuation_and_casing_differences_do_not_break_the_match():
    gap = dialogue("we are coming off radancy, over the next year", "01:09")

    (fixed,) = verify_citations([gap], TRANSCRIPT, raw_content="{}")

    assert fixed.timestamp == "01:09"


def test_a_quote_interleaving_two_speakers_with_inline_labels_is_accepted():
    """The model narrates composites as `A: "..." B: "..."`. Those labels are
    not in the transcript, but every word of actual speech is. Replayed over
    46 real calls this shape was the single biggest cause of rejection, and
    not one instance was fabricated."""
    gap = dialogue(
        'Jill: "I do have to run. I\'m getting pinged." Matt: "Okay. That\'ll work. Way over."',
        "02:11",
    )

    (fixed,) = verify_citations([gap], TRANSCRIPT, raw_content="{}")

    assert fixed.timestamp == "02:04"


def test_a_quote_with_a_trailing_ellipsis_is_accepted():
    gap = dialogue("We are coming off Radancy over the next year...", "01:09")

    (fixed,) = verify_citations([gap], TRANSCRIPT, raw_content="{}")

    assert fixed.timestamp == "01:09"


def test_a_quote_with_a_short_narrated_lead_in_still_anchors_to_the_real_speech():
    gap = dialogue('Matt says: "We are coming off Radancy over the next year."', "00:00")

    (fixed,) = verify_citations([gap], TRANSCRIPT, raw_content="{}")

    assert fixed.timestamp == "01:09"


def test_a_quote_that_is_mostly_narration_around_a_snippet_is_rejected():
    """Coverage, not mere presence: a card whose "quote" is largely the
    model's own prose is not a citation a moderator can check, even when a
    few real words are embedded in it."""
    gap = dialogue(
        "In my assessment the representative here clearly failed to establish "
        "any credibility whatsoever with the buyer, coming off Radancy, which "
        "I would characterise as a significant and material execution problem.",
        "00:00",
    )

    with pytest.raises(LLMOutputError):
        verify_citations([gap], TRANSCRIPT, raw_content="{}")


def test_a_quote_that_is_nowhere_in_the_transcript_fails_the_step():
    """The one thing a moderator cannot recover from: a quote with no source.
    Failing follows the normal retry path rather than shipping an
    uncheckable card."""
    gap = dialogue("We have already signed the contract.", "01:09")

    with pytest.raises(LLMOutputError):
        verify_citations([gap], TRANSCRIPT, raw_content="{}")


def test_explanation_gaps_pass_through_untouched():
    """A whole-call pattern quotes nothing, so there is no citation to check."""
    gap = Gap(
        theme="Success Metrics Not Agreed at Start",
        evidence_type="explanation",
        evidence="The call ended without agreeing how success would be measured.",
        timestamp=None,
        confidence="high",
    )

    assert verify_citations([gap], TRANSCRIPT, raw_content="{}") == [gap]


def test_every_gap_is_checked_not_just_the_first():
    good = dialogue("I do have to run", "02:04", theme="Time Management")
    bad = dialogue("We have already signed the contract.", "01:09", theme="Invented")

    with pytest.raises(LLMOutputError):
        verify_citations([good, bad], TRANSCRIPT, raw_content="{}")


def test_an_empty_gap_list_is_not_an_error():
    assert verify_citations([], TRANSCRIPT, raw_content="{}") == []
