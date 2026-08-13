import uuid
from datetime import datetime, timezone

import pytest

from sqlalchemy import select

from app.avoma.client import AvomaSpeaker, AvomaTranscript, AvomaTranscriptTurn
from app.core.input_gate_config import InputGateConfig
from app.db.client_models import MoonlightCall
from app.db.client_session import ClientSessionLocal
from app.db.models import Analysis, CallStorage
from app.db.session import SessionLocal
from app.services.batch.repository import seed_missing_analysis_rows
from app.services.fetcher.fetcher import FetchOutcome, fetch_and_store_call
from app.services.fetcher.repository import filter_new_calls

pytestmark = pytest.mark.integration


class FakeAvomaClient:
    def __init__(self, transcripts_by_meeting_uuid: dict):
        self._transcripts = transcripts_by_meeting_uuid
        self.calls = []

    def get_transcript_by_meeting_uuid(self, meeting_uuid: str):
        self.calls.append(meeting_uuid)
        return self._transcripts.get(meeting_uuid)


def make_unpersisted_call(meeting_uuid: str) -> MoonlightCall:
    """A MoonlightCall instance kept out of any session — never written to
    the real client DB, just used to exercise our own code's handling of
    the shape it reads."""
    return MoonlightCall(
        id=-1,
        avoma_meeting_uuid=meeting_uuid,
        transcription_uuid=None,
        account_id=None,
        crm_deal_id="deal-x",
        deal_stage="34443613",
        title="Test Co <> Joveo",
        started_at=None,
        duration_s=600,
        organizer_email="rep@joveo.com",
        avoma_type_label="Discovery",
        created_at=None,
    )


@pytest.fixture
def recording_id():
    rid = f"test-{uuid.uuid4()}"
    yield rid
    with SessionLocal() as session:
        session.query(CallStorage).filter_by(avoma_recording_id=rid).delete()
        session.commit()


def test_filter_new_calls_excludes_ids_already_in_call_storage(recording_id):
    already_stored = make_unpersisted_call(recording_id)
    not_yet_stored = make_unpersisted_call(f"test-{uuid.uuid4()}")

    with SessionLocal() as session:
        session.add(
            CallStorage(
                avoma_recording_id=recording_id,
                client_record_id="crm-1",
                transcript={"turns": []},
                call_metadata={},
                fetched_at=datetime.now(timezone.utc),
            )
        )
        session.commit()

    with SessionLocal() as our_session:
        result = filter_new_calls(our_session, [already_stored, not_yet_stored])

    assert [c.avoma_meeting_uuid for c in result] == [not_yet_stored.avoma_meeting_uuid]


def test_fetch_and_store_call_writes_row_when_transcript_found(recording_id):
    call = make_unpersisted_call(recording_id)
    avoma = FakeAvomaClient(
        {
            recording_id: AvomaTranscript(
                meeting_uuid=recording_id,
                uuid="fake-transcription-uuid",
                speakers=[AvomaSpeaker(id=0, email="rep@joveo.com", name="Rep", is_rep=True)],
                turns=[AvomaTranscriptTurn(speaker_id=0, text="hello", timestamps=[0.1])],
                transcription_vtt_url=None,
            )
        }
    )

    with ClientSessionLocal() as client_session, SessionLocal() as our_session:
        stored = fetch_and_store_call(
            client_session=client_session,
            our_session=our_session,
            avoma_client=avoma,
            call=call,
        )

    assert stored is FetchOutcome.STORED
    with SessionLocal() as session:
        row = session.query(CallStorage).filter_by(avoma_recording_id=recording_id).one()
        assert row.client_record_id == "unknown"
        assert row.transcript == {
            "turns": [{"speaker": "Rep", "speaker_id": 0, "text": "hello", "start_s": 0.1}],
            "speakers": [{"id": 0, "name": "Rep", "email": "rep@joveo.com", "is_rep": True}],
        }
        assert row.call_metadata["title"] == "Test Co <> Joveo"
        assert row.excluded_reason is None


def test_fetch_and_store_call_returns_false_when_no_transcript_available(recording_id):
    call = make_unpersisted_call(recording_id)
    avoma = FakeAvomaClient({})

    with ClientSessionLocal() as client_session, SessionLocal() as our_session:
        stored = fetch_and_store_call(
            client_session=client_session,
            our_session=our_session,
            avoma_client=avoma,
            call=call,
        )

    assert stored is FetchOutcome.NO_TRANSCRIPT
    with SessionLocal() as session:
        assert session.query(CallStorage).filter_by(avoma_recording_id=recording_id).count() == 0


def test_an_excluded_call_is_stored_but_never_seeded_for_analysis(recording_id):
    """The two halves of the exclusion mechanism, end to end against real Neon:
    the row lands in call_storage carrying its reason, and the analyser's seed
    step refuses to create an analysis row for it — so it costs no LLM call and
    never appears in the table Koushik's side reads."""
    call = make_unpersisted_call(recording_id)
    avoma = FakeAvomaClient(
        {
            recording_id: AvomaTranscript(
                meeting_uuid=recording_id,
                uuid="fake-transcription-uuid",
                speakers=[AvomaSpeaker(id=0, email="rep@joveo.com", name="Rep", is_rep=True)],
                # 3 words, well under the floor, and rep-only.
                turns=[AvomaTranscriptTurn(speaker_id=0, text="hi there again", timestamps=[0.1])],
                transcription_vtt_url=None,
            )
        }
    )

    with ClientSessionLocal() as client_session, SessionLocal() as our_session:
        outcome = fetch_and_store_call(
            client_session=client_session,
            our_session=our_session,
            avoma_client=avoma,
            call=call,
            input_gate_config=InputGateConfig(
                enabled=True, min_words=300, require_client_speech=True
            ),
        )

    assert outcome is FetchOutcome.EXCLUDED
    with SessionLocal() as session:
        row = session.query(CallStorage).filter_by(avoma_recording_id=recording_id).one()
        assert row.excluded_reason == "no_conversation"
        assert "3 words" in row.excluded_detail

        # seed_missing_analysis_rows is table-wide, so it could legitimately
        # create rows for unrelated call_storage entries that happen to lack
        # one. Diff before/after and clean up whatever this call created, per
        # the project's rule that integration tests leave no residue.
        before = set(session.execute(select(Analysis.avoma_recording_id)).scalars().all())
        seed_missing_analysis_rows(session)
        after = set(session.execute(select(Analysis.avoma_recording_id)).scalars().all())
        try:
            assert recording_id not in after
        finally:
            created = after - before
            if created:
                session.query(Analysis).filter(
                    Analysis.avoma_recording_id.in_(created)
                ).delete(synchronize_session=False)
                session.commit()
