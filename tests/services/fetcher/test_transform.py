from datetime import datetime, timezone

import pytest

from app.avoma.client import AvomaSpeaker, AvomaTranscript, AvomaTranscriptTurn
from app.db.client_models import MoonlightAccount, MoonlightCall
from app.services.fetcher.transform import (
    TranscriptShapeError,
    build_call_metadata,
    transcript_to_storage_shape,
)


def test_transcript_to_storage_shape_maps_speaker_id_to_name():
    transcript = AvomaTranscript(
        meeting_uuid="m1",
        uuid="t1",
        speakers=[
            AvomaSpeaker(id=0, email="rep@example.com", name="Rep Name", is_rep=True),
            AvomaSpeaker(id=1, email="prospect@example.com", name=None, is_rep=False),
        ],
        turns=[
            AvomaTranscriptTurn(speaker_id=0, text="Hi there", timestamps=[0.1]),
            AvomaTranscriptTurn(speaker_id=1, text="Hello", timestamps=[1.2]),
        ],
        transcription_vtt_url=None,
    )

    result = transcript_to_storage_shape(transcript)

    assert result == {
        "turns": [
            {"speaker": "Rep Name", "speaker_id": 0, "text": "Hi there", "start_s": 0.1},
            {
                "speaker": "prospect@example.com",
                "speaker_id": 1,
                "text": "Hello",
                "start_s": 1.2,
            },
        ],
        "speakers": [
            {"id": 0, "name": "Rep Name", "email": "rep@example.com", "is_rep": True},
            {"id": 1, "name": None, "email": "prospect@example.com", "is_rep": False},
        ],
    }


def test_transcript_to_storage_shape_carries_the_turn_start_time():
    # Avoma timestamps are per word; the turn starts at the earliest of them.
    # Dropping these is what left the gap step inventing mm:ss values.
    transcript = AvomaTranscript(
        meeting_uuid="m1",
        uuid="t1",
        speakers=[AvomaSpeaker(id=0, email=None, name="Rep", is_rep=True)],
        turns=[
            AvomaTranscriptTurn(
                speaker_id=0, text="a longer turn", timestamps=[42.575, 42.895, 43.375]
            )
        ],
        transcription_vtt_url=None,
    )

    result = transcript_to_storage_shape(transcript)

    assert result["turns"][0]["start_s"] == 42.575


def test_transcript_to_storage_shape_falls_back_to_placeholder_when_speaker_unmatched():
    # Real and not rare: on 3 of 51 measured calls some turns carry a speaker_id
    # absent from Avoma's speakers list — on one, half the words. The turn is kept
    # (it is real speech) and the id is preserved, which is what lets the input
    # gate abstain rather than mistake unattributable speech for rep-only speech.
    transcript = AvomaTranscript(
        meeting_uuid="m1",
        uuid="t1",
        speakers=[AvomaSpeaker(id=0, email="rep@joveo.com", name="Rep", is_rep=True)],
        turns=[
            AvomaTranscriptTurn(speaker_id=0, text="hello", timestamps=[1.0]),
            AvomaTranscriptTurn(speaker_id=5, text="hi", timestamps=[3.0]),
        ],
        transcription_vtt_url=None,
    )

    result = transcript_to_storage_shape(transcript)

    assert result["turns"][1] == {
        "speaker": "speaker-5",
        "speaker_id": 5,
        "text": "hi",
        "start_s": 3.0,
    }
    assert [s["id"] for s in result["speakers"]] == [0]


def test_transcript_to_storage_shape_rejects_a_transcript_with_no_speakers():
    # Without speakers there is no way to tell whether the client said anything,
    # so the call is skipped at the boundary rather than stored in a shape the
    # input gate cannot judge — same treatment as an unanchorable turn.
    transcript = AvomaTranscript(
        meeting_uuid="m1",
        uuid="t1",
        speakers=[],
        turns=[AvomaTranscriptTurn(speaker_id=0, text="hi", timestamps=[1.0])],
        transcription_vtt_url=None,
    )

    with pytest.raises(TranscriptShapeError, match="m1"):
        transcript_to_storage_shape(transcript)


def test_transcript_to_storage_shape_carries_is_rep_for_every_speaker():
    transcript = AvomaTranscript(
        meeting_uuid="m1",
        uuid="t1",
        speakers=[
            AvomaSpeaker(id=0, email="rep@joveo.com", name="Rep", is_rep=True),
            AvomaSpeaker(id=1, email="buyer@acme.com", name="Buyer", is_rep=False),
        ],
        turns=[AvomaTranscriptTurn(speaker_id=1, text="hi", timestamps=[1.0])],
        transcription_vtt_url=None,
    )

    result = transcript_to_storage_shape(transcript)

    assert {s["id"]: s["is_rep"] for s in result["speakers"]} == {0: True, 1: False}


def test_transcript_to_storage_shape_rejects_a_turn_with_no_timestamps():
    transcript = AvomaTranscript(
        meeting_uuid="m1",
        uuid="t1",
        speakers=[AvomaSpeaker(id=0, email=None, name="Rep", is_rep=True)],
        turns=[AvomaTranscriptTurn(speaker_id=0, text="hi", timestamps=[])],
        transcription_vtt_url=None,
    )

    with pytest.raises(TranscriptShapeError, match="m1"):
        transcript_to_storage_shape(transcript)


def test_build_call_metadata_includes_call_and_account_fields():
    call = MoonlightCall(
        id=1,
        avoma_meeting_uuid="m1",
        transcription_uuid="t1",
        account_id=2,
        crm_deal_id="deal-1",
        deal_stage="34443613",
        title="Acme <> Joveo",
        started_at=datetime(2026, 8, 1, 12, 0, tzinfo=timezone.utc),
        duration_s=1800,
        organizer_email="rep@joveo.com",
        avoma_type_label="Discovery",
        created_at=datetime(2026, 8, 1, 13, 0, tzinfo=timezone.utc),
    )
    account = MoonlightAccount(
        id=2,
        client_name="Acme Inc",
        crm_account_id="crm-acme-1",
        active=True,
        domain="acme.com",
        source="hubspot",
        created_at=datetime(2026, 1, 1, tzinfo=timezone.utc),
    )

    metadata = build_call_metadata(call, account)

    assert metadata == {
        "title": "Acme <> Joveo",
        "started_at": "2026-08-01T12:00:00+00:00",
        "duration_s": 1800,
        "organizer_email": "rep@joveo.com",
        "crm_deal_id": "deal-1",
        "deal_stage": "34443613",
        "account_name": "Acme Inc",
        "account_domain": "acme.com",
    }


def test_build_call_metadata_handles_missing_account():
    call = MoonlightCall(
        id=1,
        avoma_meeting_uuid="m1",
        transcription_uuid="t1",
        account_id=None,
        crm_deal_id=None,
        deal_stage=None,
        title=None,
        started_at=None,
        duration_s=None,
        organizer_email=None,
        avoma_type_label=None,
        created_at=datetime(2026, 8, 1, tzinfo=timezone.utc),
    )

    metadata = build_call_metadata(call, None)

    assert metadata["account_name"] is None
    assert metadata["account_domain"] is None
