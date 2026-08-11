from app.avoma.client import AvomaTranscript
from app.db.client_models import MoonlightAccount, MoonlightCall
from app.domain.transcript import Transcript, TranscriptTurn


class TranscriptShapeError(Exception):
    """Avoma returned a turn we can't anchor to a moment in the call.

    Not silently tolerated: an unanchorable turn is what let the gap step
    fabricate timestamps. The call is skipped and counted so it's visible,
    rather than stored in a form that can't support dialogue evidence.
    """


def transcript_to_storage_shape(transcript: AvomaTranscript) -> dict:
    """Avoma's transcript shape (turns keyed by speaker_id + a separate
    speakers list, timestamps per word) -> call_storage.transcript's contract
    shape (app.domain.transcript.Transcript)."""
    speaker_label_by_id = {
        speaker.id: speaker.name or speaker.email or f"speaker-{speaker.id}"
        for speaker in transcript.speakers
    }

    turns = []
    for index, turn in enumerate(transcript.turns):
        if not turn.timestamps:
            raise TranscriptShapeError(
                f"turn {index} of meeting {transcript.meeting_uuid} has no timestamps, "
                "so it cannot be cited as dialogue evidence"
            )
        turns.append(
            TranscriptTurn(
                speaker=speaker_label_by_id.get(turn.speaker_id, f"speaker-{turn.speaker_id}"),
                text=turn.text,
                # Avoma timestamps are per word; the earliest is the turn's start.
                start_s=min(turn.timestamps),
            )
        )

    return Transcript(turns=turns).model_dump()


def build_call_metadata(call: MoonlightCall, account: MoonlightAccount | None) -> dict:
    return {
        "title": call.title,
        "started_at": call.started_at.isoformat() if call.started_at else None,
        "duration_s": call.duration_s,
        "organizer_email": call.organizer_email,
        "crm_deal_id": call.crm_deal_id,
        "deal_stage": call.deal_stage,
        "account_name": account.client_name if account else None,
        "account_domain": account.domain if account else None,
    }
