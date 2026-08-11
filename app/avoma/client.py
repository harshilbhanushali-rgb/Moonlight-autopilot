from __future__ import annotations

from dataclasses import dataclass

import httpx


@dataclass(frozen=True)
class AvomaSpeaker:
    id: int
    email: str | None
    name: str | None
    is_rep: bool


@dataclass(frozen=True)
class AvomaTranscriptTurn:
    speaker_id: int
    text: str
    timestamps: list[float]


@dataclass(frozen=True)
class AvomaTranscript:
    meeting_uuid: str
    uuid: str
    speakers: list[AvomaSpeaker]
    turns: list[AvomaTranscriptTurn]
    transcription_vtt_url: str | None


def _parse_transcript(raw: dict) -> AvomaTranscript:
    speakers = [
        AvomaSpeaker(
            id=s["id"], email=s.get("email"), name=s.get("name"), is_rep=s.get("is_rep", False)
        )
        for s in raw.get("speakers", [])
    ]
    turns = [
        AvomaTranscriptTurn(
            speaker_id=t["speaker_id"], text=t["transcript"], timestamps=t.get("timestamps", [])
        )
        for t in raw.get("transcript", [])
    ]
    return AvomaTranscript(
        meeting_uuid=raw["meeting_uuid"],
        uuid=raw["uuid"],
        speakers=speakers,
        turns=turns,
        transcription_vtt_url=raw.get("transcription_vtt_url"),
    )


class AvomaClient:
    """Thin wrapper over Avoma's /v1/transcriptions/ endpoint. Fetches by
    meeting_uuid (the Avoma Recording ID / avoma_meeting_uuid), never by
    date range — matches the Call Fetcher's "known ID, not a date-range
    query" invariant."""

    def __init__(
        self,
        *,
        base_url: str,
        api_key: str,
        transport: httpx.BaseTransport | None = None,
        timeout: float = 30.0,
    ):
        self._http = httpx.Client(
            base_url=base_url,
            headers={"Authorization": f"Bearer {api_key}"},
            transport=transport,
            timeout=timeout,
        )

    def get_transcript_by_meeting_uuid(self, meeting_uuid: str) -> AvomaTranscript | None:
        response = self._http.get(
            "/v1/transcriptions/", params={"meeting_uuid": meeting_uuid}
        )
        response.raise_for_status()
        results = response.json()
        if not results:
            return None
        return _parse_transcript(results[0])
