"""The input gate's two rules.

Both are measured over 51 real calls before being written — see
`problems-and-fixes.md` 8.17. The shapes exercised here are the real ones:
the 30-word call, the client no-show, and the NHS demo where half the words
came from speaker ids Avoma never resolved.
"""

from app.core.input_gate_config import InputGateConfig
from app.domain.input_gate import evaluate_input_gate
from app.domain.transcript import Transcript, TranscriptSpeaker, TranscriptTurn
from app.domain.types import ExclusionReason

REP = TranscriptSpeaker(id=0, name="Rep", email="rep@joveo.com", is_rep=True)
OTHER_REP = TranscriptSpeaker(id=1, name="Rep Two", email="rep2@joveo.com", is_rep=True)
CLIENT = TranscriptSpeaker(id=2, name="Buyer", email="buyer@acme.com", is_rep=False)

ENABLED = InputGateConfig(enabled=True, min_words=300, require_client_speech=True)


def turn(speaker: TranscriptSpeaker, words: int, *, start_s: float = 0.0) -> TranscriptTurn:
    return TranscriptTurn(
        speaker=speaker.name or "?",
        speaker_id=speaker.id,
        text=" ".join(["word"] * words),
        start_s=start_s,
    )


def unlinked_turn(speaker_id: int, words: int) -> TranscriptTurn:
    """Speech whose speaker_id has no entry in `speakers` — real on 3 of 51 calls."""
    return TranscriptTurn(
        speaker=f"speaker-{speaker_id}",
        speaker_id=speaker_id,
        text=" ".join(["word"] * words),
        start_s=1.0,
    )


def transcript(*turns, speakers=(REP, CLIENT)) -> Transcript:
    return Transcript(speakers=list(speakers), turns=list(turns))


# --- R1: no assessable conversation ---------------------------------------


def test_a_transcript_below_the_word_floor_is_rejected():
    verdict = evaluate_input_gate(transcript(turn(CLIENT, 299)), ENABLED)

    assert verdict.accepted is False
    assert verdict.reason is ExclusionReason.NO_CONVERSATION
    assert "299" in verdict.detail


def test_the_word_floor_is_a_pass_at_exactly_the_threshold():
    verdict = evaluate_input_gate(transcript(turn(CLIENT, 300)), ENABLED)

    assert verdict.accepted is True
    assert verdict.reason is None


def test_the_thirty_word_call_is_rejected():
    # 35f28528: one 30-word turn across a 1156-second meeting, by a Joveo rep.
    # Fails both rules; reports the more specific one.
    verdict = evaluate_input_gate(transcript(turn(REP, 30), speakers=(REP,)), ENABLED)

    assert verdict.reason is ExclusionReason.NO_CONVERSATION


def test_an_empty_transcript_is_rejected_rather_than_crashing():
    verdict = evaluate_input_gate(transcript(speakers=(REP,)), ENABLED)

    assert verdict.accepted is False
    assert verdict.reason is ExclusionReason.NO_CONVERSATION


# --- R2: no client speech -------------------------------------------------


def test_a_call_where_a_non_rep_spoke_is_accepted():
    verdict = evaluate_input_gate(transcript(turn(REP, 200), turn(CLIENT, 200)), ENABLED)

    assert verdict.accepted is True
    assert verdict.client_speech_skipped is False


def test_a_client_who_was_invited_but_never_spoke_is_not_client_speech():
    # The whole reason R2 checks attributed turns rather than the speakers list:
    # a speakers entry only records who Avoma associated with the meeting.
    verdict = evaluate_input_gate(transcript(turn(REP, 400)), ENABLED)

    assert verdict.accepted is False
    assert verdict.reason is ExclusionReason.NO_CLIENT_SPEECH


def test_a_call_where_only_reps_spoke_is_rejected():
    # 4ac4eea2: the client never joined and two Joveo employees talked to each
    # other for 20 minutes about the deck they meant to show her.
    verdict = evaluate_input_gate(
        transcript(turn(REP, 400), turn(OTHER_REP, 400), speakers=(REP, OTHER_REP)), ENABLED
    )

    assert verdict.accepted is False
    assert verdict.reason is ExclusionReason.NO_CLIENT_SPEECH
    assert "no non-rep" in verdict.detail


# --- R2 abstains rather than guessing ------------------------------------


def test_r2_abstains_when_only_reps_are_linked_and_some_speech_is_unlinked():
    # e8bfc3fb (NHS demo): 2,502 of 5,314 words came from speaker ids absent
    # from Avoma's speakers list. The client did speak. This is R2's only
    # measured false positive, and abstaining is what removes it.
    verdict = evaluate_input_gate(
        transcript(turn(REP, 400), unlinked_turn(9, 400), speakers=(REP,)), ENABLED
    )

    assert verdict.accepted is True
    assert verdict.client_speech_skipped is True


def test_all_speech_unlinked_abstains():
    verdict = evaluate_input_gate(
        transcript(unlinked_turn(7, 200), unlinked_turn(8, 200), speakers=(REP,)), ENABLED
    )

    assert verdict.accepted is True
    assert verdict.client_speech_skipped is True


def test_a_non_rep_who_spoke_settles_it_even_when_other_speech_is_unlinked():
    # R2 has its answer, so unresolved ids are irrelevant — abstaining here
    # would throw away a definite result.
    verdict = evaluate_input_gate(
        transcript(turn(CLIENT, 200), unlinked_turn(9, 200)), ENABLED
    )

    assert verdict.accepted is True
    assert verdict.client_speech_skipped is False


def test_a_speaker_with_no_email_still_counts_as_client_speech_if_avoma_says_non_rep():
    # is_rep is Avoma's own flag and does not depend on an email being resolved.
    anonymous = TranscriptSpeaker(id=3, name="Guest", email=None, is_rep=False)
    verdict = evaluate_input_gate(
        transcript(turn(REP, 200), turn(anonymous, 200), speakers=(REP, anonymous)), ENABLED
    )

    assert verdict.accepted is True


# --- rule ordering and switches ------------------------------------------


def test_the_word_floor_is_reported_when_a_call_fails_both_rules():
    verdict = evaluate_input_gate(transcript(turn(REP, 10), speakers=(REP,)), ENABLED)

    assert verdict.reason is ExclusionReason.NO_CONVERSATION


def test_turning_off_the_client_rule_leaves_the_word_floor_working():
    config = InputGateConfig(enabled=True, min_words=300, require_client_speech=False)

    rep_only = evaluate_input_gate(transcript(turn(REP, 400), speakers=(REP,)), config)
    too_short = evaluate_input_gate(transcript(turn(CLIENT, 10)), config)

    assert rep_only.accepted is True
    assert too_short.reason is ExclusionReason.NO_CONVERSATION


def test_a_disabled_gate_accepts_everything():
    config = InputGateConfig(enabled=False, min_words=300, require_client_speech=True)

    verdict = evaluate_input_gate(transcript(turn(REP, 10), speakers=(REP,)), config)

    assert verdict.accepted is True
    assert verdict.reason is None


def test_the_detail_line_records_the_counts_a_reviewer_needs():
    verdict = evaluate_input_gate(
        transcript(turn(REP, 400), unlinked_turn(9, 50), speakers=(REP,)), ENABLED
    )

    # Enough to reconstruct the decision without re-reading the transcript.
    assert "400" in verdict.detail
    assert "50" in verdict.detail
