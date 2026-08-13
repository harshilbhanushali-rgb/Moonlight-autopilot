import pytest
from pydantic import ValidationError

from app.domain.transcript import Transcript, TranscriptSpeaker, TranscriptTurn


def _speaker(id_=0, *, name="Rep Name", email="rep@joveo.com", is_rep=True):
    return TranscriptSpeaker(id=id_, name=name, email=email, is_rep=is_rep)


def test_renders_each_turn_with_an_mm_ss_prefix():
    # Also the regression guard for "adding speaker identity must not change the
    # text the LLM sees" — speakers and speaker_id are present here and neither
    # appears in the output.
    transcript = Transcript(
        speakers=[
            _speaker(0),
            _speaker(1, name="Prospect", email="buyer@acme.com", is_rep=False),
        ],
        turns=[
            TranscriptTurn(speaker="Rep Name", speaker_id=0, text="Hi there", start_s=0.1),
            TranscriptTurn(speaker="Prospect", speaker_id=1, text="Hello", start_s=69.1),
        ],
    )

    assert transcript.render_for_prompt() == (
        "[00:00] Rep Name: Hi there\n[01:09] Prospect: Hello"
    )


@pytest.mark.parametrize(
    ("start_s", "expected"),
    [
        (0.0, "00:00"),
        (42.575, "00:42"),
        (69.1, "01:09"),
        (489.4, "08:09"),
        (3671.0, "61:11"),
    ],
)
def test_formats_timestamps_as_mm_ss(start_s, expected):
    assert (
        TranscriptTurn(speaker="s", speaker_id=0, text="t", start_s=start_s).timestamp == expected
    )


def test_rejects_a_turn_with_no_start_time():
    # The gap step is asked to cite mm:ss for dialogue evidence. A turn with no
    # start time can't support that, and silently allowing it is what let the
    # model invent timestamps.
    with pytest.raises(ValidationError):
        TranscriptTurn(speaker="Rep", speaker_id=0, text="Hi")


def test_rejects_a_negative_start_time():
    with pytest.raises(ValidationError):
        TranscriptTurn(speaker="Rep", speaker_id=0, text="Hi", start_s=-1)


def test_rejects_unknown_fields_so_contract_drift_is_caught():
    with pytest.raises(ValidationError):
        TranscriptTurn(
            speaker="Rep", speaker_id=0, text="Hi", start_s=0, timestamps=[0.1, 0.2]
        )


def test_rejects_a_stored_transcript_that_predates_timestamps():
    # The exact shape written before turn timestamps were carried through.
    legacy = {"turns": [{"speaker": "Rep", "text": "Hi there"}]}

    with pytest.raises(ValidationError):
        Transcript.model_validate(legacy)


def test_rejects_a_turn_with_no_speaker_id():
    # Without it there is no way to ask whether a given person actually spoke —
    # the display label is not an identity (two "Marie"s, or a renamed speaker).
    with pytest.raises(ValidationError):
        TranscriptTurn(speaker="Rep", text="Hi", start_s=0.0)


def test_rejects_a_transcript_with_no_speakers_list():
    # The shape stored before speaker identity was carried. These rows must be
    # re-fetched rather than read with a default, so that a transcript whose
    # participants are unknown fails loudly instead of silently skipping the
    # client-speech rule.
    legacy = {"turns": [{"speaker": "Rep", "speaker_id": 0, "text": "Hi", "start_s": 0.0}]}

    with pytest.raises(ValidationError):
        Transcript.model_validate(legacy)


def test_carries_speaker_identity_through_a_round_trip():
    transcript = Transcript(
        speakers=[
            _speaker(0),
            _speaker(1, name="Buyer", email="buyer@acme.com", is_rep=False),
        ],
        turns=[TranscriptTurn(speaker="Rep Name", speaker_id=0, text="Hi", start_s=0.0)],
    )

    restored = Transcript.model_validate(transcript.model_dump())

    assert [(s.id, s.email, s.is_rep) for s in restored.speakers] == [
        (0, "rep@joveo.com", True),
        (1, "buyer@acme.com", False),
    ]
    assert restored.turns[0].speaker_id == 0


def test_a_speaker_may_have_no_name_or_email_but_the_keys_are_required():
    # Avoma does not always resolve a diarized speaker to a person. A missing
    # value is legitimate; a missing *key* means the writer changed shape.
    assert TranscriptSpeaker(id=3, name=None, email=None, is_rep=False).email is None

    with pytest.raises(ValidationError):
        TranscriptSpeaker.model_validate({"id": 3, "is_rep": False})


def test_an_empty_speakers_list_is_valid_only_with_no_turns():
    # Not a shape the fetcher writes (it raises instead), but the model itself
    # should not forbid it — emptiness is the gate's business, not the contract's.
    assert Transcript(speakers=[], turns=[]).render_for_prompt() == ""
