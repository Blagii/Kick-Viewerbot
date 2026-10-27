import pytest
import requests

from backend.api.youtube import fetch_live_viewers, normalize_youtube_target, youtube_video_id


@pytest.mark.parametrize("source, expected", [
    ("AbCDef12_-X", "https://www.youtube.com/watch?v=AbCDef12_-X"),
    ("https://youtube.com/watch?list=xyz&v=AbCDef12_-X&t=30", "https://www.youtube.com/watch?v=AbCDef12_-X"),
    ("https://youtu.be/AbCDef12_-X?t=3", "https://www.youtube.com/watch?v=AbCDef12_-X"),
    ("youtube.com/live/AbCDef12_-X", "https://www.youtube.com/watch?v=AbCDef12_-X"),
    ("https://www.youtube.com/embed/AbCDef12_-X", "https://www.youtube.com/watch?v=AbCDef12_-X"),
    ("@Case.Sensitive", "https://www.youtube.com/@Case.Sensitive/live"),
    ("SomeChannelName", "https://www.youtube.com/@SomeChannelName/live"),
    ("https://www.youtube.com/@SomeChannel/live", "https://www.youtube.com/@SomeChannel/live"),
    ("UC_x5XG1OV2P6uZZ5FSM9Ttw", "https://www.youtube.com/channel/UC_x5XG1OV2P6uZZ5FSM9Ttw/live"),
    ("https://www.youtube.com/channel/UC_x5XG1OV2P6uZZ5FSM9Ttw", "https://www.youtube.com/channel/UC_x5XG1OV2P6uZZ5FSM9Ttw/live"),
])
def test_normalize_youtube_target(source, expected):
    assert normalize_youtube_target(source) == expected


@pytest.mark.parametrize("source", [
    "", None, "https://kick.com/xqc", "https://youtube.com.evil.test/watch?v=AbCDef12_-X",
    "http://localhost/watch?v=AbCDef12_-X", "https://user@youtube.com/watch?v=AbCDef12_-X",
    "https://youtube.com:444/watch?v=AbCDef12_-X", "https://youtube.com/redirect?q=https://evil.test",
    "https://youtube.com/watch?v=invalid", "https://youtu.be/AbCDef12_-X/extra", "@bad/handle",
])
def test_reject_non_youtube_and_invalid_urls(source):
    with pytest.raises(ValueError):
        normalize_youtube_target(source)


def test_video_id_preserves_case_and_channel_has_no_video_id():
    assert youtube_video_id("https://youtu.be/AbCDef12_-X") == "AbCDef12_-X"
    assert youtube_video_id("@SomeChannel") is None


class Response:
    def __init__(self, payload, status=200):
        self.payload = payload
        self.status_code = status
        self.ok = status == 200

    def json(self):
        return self.payload


def test_viewer_count_api_and_zero_are_not_confused_with_unavailable():
    calls = []
    def get(url, **kwargs):
        calls.append((url, kwargs))
        return Response({"items": [{"liveStreamingDetails": {"concurrentViewers": "0"}}]})
    assert fetch_live_viewers("AbCDef12_-X", "secret", get)[0] == 0
    assert calls[0][0] == "https://www.googleapis.com/youtube/v3/videos"
    assert calls[0][1]["params"]["id"] == "AbCDef12_-X"
    assert fetch_live_viewers("AbCDef12_-X", "secret", lambda *a, **kw: Response({"items": []}))[0] is None
    assert fetch_live_viewers("AbCDef12_-X", "", get)[0] is None
    assert len(calls) == 1


def test_viewer_count_does_not_leak_api_key_on_errors():
    count, message = fetch_live_viewers("AbCDef12_-X", "secret", lambda *a, **kw: Response({}, 403))
    assert count is None and "403" in message and "secret" not in message
    def failed(*a, **kw):
        raise requests.ConnectionError("secret")
    count, message = fetch_live_viewers("AbCDef12_-X", "secret", failed)
    assert count is None and "secret" not in message


def test_viewer_count_handles_unexpected_api_payload():
    count, message = fetch_live_viewers("AbCDef12_-X", "secret", lambda *a, **kw: Response(["unexpected"]))
    assert count is None and "secret" not in message
