from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db.client_models import MoonlightCall
from app.db.models import CallStorage


def list_all_client_calls(client_session: Session) -> list[MoonlightCall]:
    return client_session.query(MoonlightCall).all()


def filter_new_calls(our_session: Session, calls: list[MoonlightCall]) -> list[MoonlightCall]:
    """Keeps only calls not yet present in our call_storage, checked via an
    indexed WHERE-IN against the candidate list — never a full scan of
    call_storage history."""
    if not calls:
        return []
    candidate_ids = [c.avoma_meeting_uuid for c in calls]
    existing_ids = set(
        our_session.execute(
            select(CallStorage.avoma_recording_id).where(
                CallStorage.avoma_recording_id.in_(candidate_ids)
            )
        )
        .scalars()
        .all()
    )
    return [c for c in calls if c.avoma_meeting_uuid not in existing_ids]
