import logging
from dataclasses import dataclass
from datetime import datetime, timezone

from app.db.client_models import MoonlightAccount, MoonlightCall
from app.db.models import CallStorage
from app.services.fetcher.repository import filter_new_calls, list_all_client_calls
from app.services.fetcher.transform import (
    TranscriptShapeError,
    build_call_metadata,
    transcript_to_storage_shape,
)

logger = logging.getLogger(__name__)


@dataclass
class FetchSummary:
    candidates: int
    fetched: int
    skipped_no_transcript: int
    skipped_malformed_transcript: int = 0


def fetch_and_store_call(*, client_session, our_session, avoma_client, call: MoonlightCall) -> bool:
    """Fetches one call's transcript from Avoma and writes it to
    call_storage. Returns False (no write) if Avoma doesn't have a
    transcript yet — the call simply stays absent from call_storage and is
    retried automatically on the next run; no separate status/retry
    tracking needed for that case."""
    transcript = avoma_client.get_transcript_by_meeting_uuid(call.avoma_meeting_uuid)
    if transcript is None:
        return False

    account = client_session.get(MoonlightAccount, call.account_id) if call.account_id else None
    our_session.add(
        CallStorage(
            avoma_recording_id=call.avoma_meeting_uuid,
            client_record_id=account.crm_account_id if account else "unknown",
            transcript=transcript_to_storage_shape(transcript),
            call_metadata=build_call_metadata(call, account),
            fetched_at=datetime.now(timezone.utc),
        )
    )
    our_session.commit()
    return True


def fetch_new_calls(*, client_session, our_session, avoma_client, limit: int | None = None) -> FetchSummary:
    all_calls = list_all_client_calls(client_session)
    new_calls = filter_new_calls(our_session, all_calls)
    if limit is not None:
        new_calls = new_calls[:limit]

    fetched = 0
    skipped_no_transcript = 0
    skipped_malformed_transcript = 0
    for call in new_calls:
        try:
            stored = fetch_and_store_call(
                client_session=client_session,
                our_session=our_session,
                avoma_client=avoma_client,
                call=call,
            )
        except TranscriptShapeError:
            # One unanchorable transcript must not abort the run — the
            # scheduler skips the analyser entirely when the fetcher raises.
            logger.exception(
                "skipping call %s: transcript cannot be anchored to timestamps",
                call.avoma_meeting_uuid,
            )
            our_session.rollback()
            skipped_malformed_transcript += 1
            continue

        if stored:
            fetched += 1
        else:
            skipped_no_transcript += 1

    return FetchSummary(
        candidates=len(new_calls),
        fetched=fetched,
        skipped_no_transcript=skipped_no_transcript,
        skipped_malformed_transcript=skipped_malformed_transcript,
    )
