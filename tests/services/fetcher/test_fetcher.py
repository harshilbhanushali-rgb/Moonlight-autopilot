"""The fetcher's input-gate wiring.

The load-bearing assertion here is that an excluded call is still WRITTEN. Not
storing it would make `filter_new_calls` see it as new on every subsequent run,
so it would be re-fetched from Avoma forever and no record of the rejection
would exist anywhere.
"""

from datetime import datetime, timezone

import pytest

from app.avoma.client import AvomaSpeaker, AvomaTranscript, AvomaTranscriptTurn
from app.core.input_gate_config import InputGateConfig
from app.db.client_models import MoonlightCall
from app.services.fetcher.fetcher import FetchOutcome, fetch_and_store_call

GATE_ON = InputGateConfig(enabled=True, min_words=300, require_client_speech=True)

REP = AvomaSpeaker(id=0, email="rep@joveo.com", name="Rep", is_rep=True)
CLIENT = AvomaSpeaker(id=1, email="buyer@acme.com", name="Buyer", is_rep=False)


class FakeSession:
    """Just enough Session for fetch_and_store_call: add/commit/get."""

    def __init__(self):
        self.added = []
        self.commits = 0

    def add(self, obj):
        self.added.append(obj)

    def commit(self):
        self.commits += 1

    def get(self, _model, _pk):
        return None


class FakeAvoma:
    def __init__(self, transcript):
        self._transcript = transcript

    def get_transcript_by_meeting_uuid(self, _uuid):
        return self._transcript


def avoma_transcript(*turn_specs, speakers=(REP, CLIENT)):
    return AvomaTranscript(
        meeting_uuid="m1",
        uuid="t1",
        speakers=list(speakers),
        turns=[
            AvomaTranscriptTurn(
                speaker_id=speaker_id, text=" ".join(["word"] * words), timestamps=[float(i + 1)]
            )
            for i, (speaker_id, words) in enumerate(turn_specs)
        ],
        transcription_vtt_url=None,
    )


def make_call():
    return MoonlightCall(
        id=1,
        avoma_meeting_uuid="m1",
        transcription_uuid="t1",
        account_id=None,
        crm_deal_id=None,
        deal_stage=None,
        title="Acme <> Joveo",
        started_at=datetime(2026, 8, 1, tzinfo=timezone.utc),
        duration_s=1800,
        organizer_email="rep@joveo.com",
        avoma_type_label=None,
        created_at=datetime(2026, 8, 1, tzinfo=timezone.utc),
    )


def store(transcript, config=GATE_ON):
    our_session = FakeSession()
    outcome = fetch_and_store_call(
        client_session=FakeSession(),
        our_session=our_session,
        avoma_client=FakeAvoma(transcript),
        call=make_call(),
        input_gate_config=config,
    )
    return outcome, our_session


def test_an_analysable_call_is_stored_with_no_exclusion():
    outcome, session = store(avoma_transcript((0, 200), (1, 200)))

    assert outcome is FetchOutcome.STORED
    row = session.added[0]
    assert row.excluded_reason is None
    assert row.excluded_detail is None


def test_an_excluded_call_is_still_written_and_carries_its_reason():
    outcome, session = store(avoma_transcript((0, 10), speakers=(REP,)))

    assert outcome is FetchOutcome.EXCLUDED
    assert session.commits == 1
    row = session.added[0]
    assert row.avoma_recording_id == "m1"
    assert row.excluded_reason == "no_conversation"
    assert "10 words" in row.excluded_detail
    # The transcript is kept, so the gate can be re-run with different settings
    # without going back to Avoma.
    assert row.transcript["turns"]


def test_a_rep_only_call_is_excluded_for_no_client_speech():
    outcome, session = store(avoma_transcript((0, 400), speakers=(REP,)))

    assert outcome is FetchOutcome.EXCLUDED
    assert session.added[0].excluded_reason == "no_client_speech"


def test_no_transcript_yet_writes_nothing():
    our_session = FakeSession()
    outcome = fetch_and_store_call(
        client_session=FakeSession(),
        our_session=our_session,
        avoma_client=FakeAvoma(None),
        call=make_call(),
        input_gate_config=GATE_ON,
    )

    assert outcome is FetchOutcome.NO_TRANSCRIPT
    assert our_session.added == []


def test_a_disabled_gate_stores_everything_unmarked():
    outcome, session = store(
        avoma_transcript((0, 5), speakers=(REP,)),
        config=InputGateConfig(enabled=False),
    )

    assert outcome is FetchOutcome.STORED
    assert session.added[0].excluded_reason is None


def test_the_gate_defaults_to_off_when_no_config_is_passed():
    # A caller that forgets to pass config must not silently start excluding.
    our_session = FakeSession()
    outcome = fetch_and_store_call(
        client_session=FakeSession(),
        our_session=our_session,
        avoma_client=FakeAvoma(avoma_transcript((0, 5), speakers=(REP,))),
        call=make_call(),
    )

    assert outcome is FetchOutcome.STORED
    assert our_session.added[0].excluded_reason is None


def test_an_abstained_client_check_is_not_an_exclusion():
    # Speech from an unresolved speaker id, no linked non-rep: the call is
    # analysed and nothing is marked.
    outcome, session = store(avoma_transcript((0, 200), (9, 200), speakers=(REP,)))

    assert outcome is FetchOutcome.STORED
    assert session.added[0].excluded_reason is None


def test_a_transcript_with_no_speakers_raises_rather_than_being_stored():
    from app.services.fetcher.transform import TranscriptShapeError

    with pytest.raises(TranscriptShapeError):
        store(avoma_transcript((0, 400), speakers=()))
