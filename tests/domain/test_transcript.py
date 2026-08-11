import pytest
from pydantic import ValidationError

from app.domain.transcript import Transcript, TranscriptTurn


def test_renders_each_turn_with_an_mm_ss_prefix():
    transcript = Transcript(
        turns=[
            TranscriptTurn(speaker="Rep Name", text="Hi there", start_s=0.1),
            TranscriptTurn(speaker="Prospect", text="Hello", start_s=69.1),
        ]
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
    assert TranscriptTurn(speaker="s", text="t", start_s=start_s).timestamp == expected


def test_rejects_a_turn_with_no_start_time():
    # The gap step is asked to cite mm:ss for dialogue evidence. A turn with no
    # start time can't support that, and silently allowing it is what let the
    # model invent timestamps.
    with pytest.raises(ValidationError):
        TranscriptTurn(speaker="Rep", text="Hi")


def test_rejects_a_negative_start_time():
    with pytest.raises(ValidationError):
        TranscriptTurn(speaker="Rep", text="Hi", start_s=-1)


def test_rejects_unknown_fields_so_contract_drift_is_caught():
    with pytest.raises(ValidationError):
        TranscriptTurn(speaker="Rep", text="Hi", start_s=0, timestamps=[0.1, 0.2])


def test_rejects_a_stored_transcript_that_predates_timestamps():
    # The exact shape written before turn timestamps were carried through.
    legacy = {"turns": [{"speaker": "Rep", "text": "Hi there"}]}

    with pytest.raises(ValidationError):
        Transcript.model_validate(legacy)
