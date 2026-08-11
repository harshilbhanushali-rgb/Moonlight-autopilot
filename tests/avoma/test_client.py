import httpx
import pytest

from app.avoma.client import AvomaClient

RAW_TRANSCRIPT = {
    "meeting_uuid": "123e4567-e89b-12d3-a456-426614174000",
    "speakers": [
        {"email": "rep@example.com", "id": 0, "is_rep": True, "name": "Rep Name"}
    ],
    "transcript": [
        {"speaker_id": 0, "timestamps": [0.77, 0.89, 1.04, 1.19], "transcript": "Hey, how are you?"}
    ],
    "transcription_vtt_url": "https://example.com/vtt",
    "uuid": "095be615-a8ad-4c33-8e9c-c7612fbf6c9f",
}


def make_client(handler, **overrides):
    transport = httpx.MockTransport(handler)
    kwargs = dict(base_url="https://api.avoma.com", api_key="secret-token", transport=transport)
    kwargs.update(overrides)
    return AvomaClient(**kwargs)


def test_get_transcript_by_meeting_uuid_returns_parsed_transcript_when_found():
    captured = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["url"] = request.url
        captured["headers"] = request.headers
        return httpx.Response(200, json=[RAW_TRANSCRIPT])

    client = make_client(handler)

    result = client.get_transcript_by_meeting_uuid("123e4567-e89b-12d3-a456-426614174000")

    assert result.meeting_uuid == "123e4567-e89b-12d3-a456-426614174000"
    assert result.uuid == "095be615-a8ad-4c33-8e9c-c7612fbf6c9f"
    assert result.speakers[0].email == "rep@example.com"
    assert result.speakers[0].is_rep is True
    assert result.turns[0].speaker_id == 0
    assert result.turns[0].text == "Hey, how are you?"
    assert result.transcription_vtt_url == "https://example.com/vtt"
    assert captured["url"].params["meeting_uuid"] == "123e4567-e89b-12d3-a456-426614174000"
    assert captured["headers"]["authorization"] == "Bearer secret-token"


def test_get_transcript_by_meeting_uuid_returns_none_when_not_found():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=[])

    client = make_client(handler)

    result = client.get_transcript_by_meeting_uuid("does-not-exist")

    assert result is None


def test_get_transcript_by_meeting_uuid_raises_on_non_2xx_response():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(500, json={"error": "boom"})

    client = make_client(handler)

    with pytest.raises(httpx.HTTPStatusError):
        client.get_transcript_by_meeting_uuid("some-uuid")
