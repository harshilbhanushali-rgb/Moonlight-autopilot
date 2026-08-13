from app.avoma.client import AvomaTranscript
from app.db.client_models import MoonlightAccount, MoonlightCall
from app.domain.transcript import Transcript, TranscriptSpeaker, TranscriptTurn


class TranscriptShapeError(Exception):
    """Avoma returned a transcript we can't use as evidence.

    Two cases, both refused at the boundary rather than stored in a degraded
    form:

    - a turn we can't anchor to a moment in the call. An unanchorable turn is
      what let the gap step fabricate timestamps.
    - no speakers at all. Without them nothing can tell whether anyone on the
      client's side spoke, so the input gate would have to abstain on every
      call, silently.

    Either way the call is skipped and counted so it's visible.
    """


def transcript_to_storage_shape(transcript: AvomaTranscript) -> dict:
    """Avoma's transcript shape (turns keyed by speaker_id + a separate
    speakers list, timestamps per word) -> call_storage.transcript's contract
    shape (app.domain.transcript.Transcript)."""
    if not transcript.speakers:
        raise TranscriptShapeError(
            f"meeting {transcript.meeting_uuid} has no speakers, so it cannot be "
            "judged for client participation"
        )

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
                # Kept even when it resolves to nothing: an unresolvable id is
                # what tells the input gate to abstain instead of reading
                # unattributable speech as rep-only speech.
                speaker_id=turn.speaker_id,
                text=turn.text,
                # Avoma timestamps are per word; the earliest is the turn's start.
                start_s=min(turn.timestamps),
            )
        )

    speakers = [
        TranscriptSpeaker(id=s.id, name=s.name, email=s.email, is_rep=s.is_rep)
        for s in transcript.speakers
    ]

    return Transcript(turns=turns, speakers=speakers).model_dump()


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
