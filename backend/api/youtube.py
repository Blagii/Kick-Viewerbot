"""YouTube target validation and optional public live-viewer statistics."""

import re
from urllib.parse import parse_qs, quote, unquote, urlsplit

import requests

_VIDEO_ID = re.compile(r"^[A-Za-z0-9_-]{11}$")
_CHANNEL_ID = re.compile(r"^UC[A-Za-z0-9_-]{22}$")
_HANDLE = re.compile(r"^[\w.-]{3,30}$", re.UNICODE)
_ALLOWED_HOSTS = {"youtube.com", "www.youtube.com", "m.youtube.com", "music.youtube.com"}


def normalize_youtube_target(value: str) -> str:
    """Return a safe canonical YouTube URL without lowercasing case-sensitive IDs.

    A bare handle points to the channel's current live stream. Video IDs, watch,
    short, live and embed links point to the corresponding video. Reject all
    other hosts and paths rather than turning this into an arbitrary URL opener.
    """
    if not isinstance(value, str):
        raise ValueError("Enter a YouTube video link, video ID, or @handle.")
    value = value.strip()
    if not value or any(ch.isspace() for ch in value):
        raise ValueError("Enter a YouTube video link, video ID, or @handle.")

    if _VIDEO_ID.fullmatch(value):
        return f"https://www.youtube.com/watch?v={value}"
    if _CHANNEL_ID.fullmatch(value):
        return f"https://www.youtube.com/channel/{value}/live"
    if value.startswith("@") or ("://" not in value and "/" not in value and "." not in value):
        handle = value[1:] if value.startswith("@") else value
        if not _HANDLE.fullmatch(handle):
            raise ValueError("Invalid YouTube handle. Use a video link or @handle.")
        return f"https://www.youtube.com/@{quote(handle, safe='.-_')}/live"

    if "://" not in value:
        value = "https://" + value
    try:
        parsed = urlsplit(value)
        host = (parsed.hostname or "").lower()
        if parsed.scheme.lower() not in ("http", "https") or parsed.username or parsed.password or parsed.port:
            raise ValueError
    except ValueError:
        raise ValueError("Invalid YouTube URL.") from None

    parts = [unquote(part) for part in parsed.path.split("/") if part]
    video_id = None
    if host == "youtu.be" and len(parts) == 1:
        video_id = parts[0]
    elif host in _ALLOWED_HOSTS:
        if parts == ["watch"]:
            video_id = parse_qs(parsed.query).get("v", [None])[0]
        elif len(parts) == 2 and parts[0] in ("live", "embed", "shorts", "v"):
            video_id = parts[1]
        elif parts and parts[0].startswith("@") and (len(parts) == 1 or parts[1:] == ["live"]):
            handle = parts[0][1:]
            if _HANDLE.fullmatch(handle):
                return f"https://www.youtube.com/@{quote(handle, safe='.-_')}/live"
        elif len(parts) in (2, 3) and parts[0] in ("channel", "user", "c") and (len(parts) == 2 or parts[2] == "live"):
            name = parts[1]
            if (parts[0] == "channel" and _CHANNEL_ID.fullmatch(name)) or (
                parts[0] in ("user", "c") and _HANDLE.fullmatch(name)
            ):
                return f"https://www.youtube.com/{parts[0]}/{quote(name, safe='.-_')}/live"

    if video_id and _VIDEO_ID.fullmatch(video_id):
        return f"https://www.youtube.com/watch?v={video_id}"
    raise ValueError("Unsupported YouTube URL. Use a video link, video ID, or @handle.")


def youtube_video_id(value: str):
    """Extract a video ID if the target is a specific video, otherwise None."""
    target = normalize_youtube_target(value)
    parsed = urlsplit(target)
    return parse_qs(parsed.query).get("v", [None])[0]


def fetch_live_viewers(video_id: str, api_key: str, http_get=None):
    """Return (count, message), distinguishing unavailable data from zero.

    Only the official YouTube Data API is queried. Never return API error bodies
    (which can include request details or credentials) to the browser.
    """
    if not _VIDEO_ID.fullmatch(video_id or ""):
        return None, "Use a YouTube video link to see its public live viewer count."
    if not api_key:
        return None, "Set YOUTUBE_API_KEY in the backend to show YouTube's live viewer count."
    try:
        response = (http_get or requests.get)(
            "https://www.googleapis.com/youtube/v3/videos",
            params={"part": "liveStreamingDetails", "id": video_id, "key": api_key},
            timeout=10,
        )
        if not response.ok:
            return None, f"YouTube Data API unavailable (HTTP {response.status_code}). Check the API key and quota."
        items = response.json().get("items", [])
        raw = items[0].get("liveStreamingDetails", {}).get("concurrentViewers") if items else None
        if raw is None:
            return None, "YouTube has not published a live viewer count for this video."
        count = int(raw)
        if count < 0:
            raise ValueError("Negative count")
        return count, "Live viewers reported by YouTube."
    except (requests.RequestException, ValueError, TypeError, KeyError, IndexError, AttributeError):
        return None, "Could not retrieve the live viewer count from YouTube."
