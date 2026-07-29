"""Tests for the YouTube Data API v3 source (fetch_youtube_candidates).

Seam: the public ``fetch_youtube_candidates`` function. The HTTP I/O is
mocked at the ``_youtube_api_get`` seam (a single function wrapping the
YouTube Data API v3 calls), so the candidate-dict shape + int/date
conversions are tested without hitting the network.
"""

import logging
from unittest.mock import patch

import httpx
import pytest

from source import fetch_youtube_candidates


def _canned_api_responses():
    """Canned YouTube Data API v3 responses for one channel + one video."""
    return {
        # channels.list: handle -> channel ID + subscriber count
        "channels": {
            "items": [
                {
                    "id": "UC_TEST",
                    "statistics": {"subscriberCount": "50000"},
                    "snippet": {"title": "Test Channel"},
                }
            ]
        },
        # search.list: recent uploads -> video IDs + titles + dates
        "search": {
            "items": [
                {
                    "id": {"kind": "youtube#video", "videoId": "vid1"},
                    "snippet": {
                        "title": "Test Video",
                        "publishedAt": "2026-07-22T17:45:00Z",
                        "channelTitle": "Test Channel",
                    },
                }
            ]
        },
        # videos.list: video IDs -> view/like/comment counts
        "videos": {
            "items": [
                {
                    "id": "vid1",
                    "statistics": {
                        "viewCount": "12345",
                        "likeCount": "100",
                        "commentCount": "5",
                    },
                    "snippet": {
                        "title": "Test Video",
                        "publishedAt": "2026-07-22T17:45:00Z",
                        "channelTitle": "Test Channel",
                    },
                }
            ]
        },
    }


@pytest.mark.asyncio
async def test_fetch_youtube_candidates_returns_api_data_with_correct_shape():
    """fetch_youtube_candidates uses the YouTube Data API v3 (channels.list,
    search.list, videos.list) and returns candidates with the shape the gate
    + velocity layer expect: url, title, kind, channel, viewCount (int),
    uploadDate (YYYYMMDD converted from ISO 8601), channel_url,
    channel_subscribers (int), like_count (int), description, topics."""
    canned = _canned_api_responses()
    channel_input_url = "https://www.youtube.com/@testchannel/videos"

    async def fake_api_get(path, params):
        if "channels" in path:
            return canned["channels"]
        if "search" in path:
            return canned["search"]
        if "videos" in path:
            return canned["videos"]
        return {}

    with (
        patch("source.CHANNELS", [channel_input_url]),
        patch("source._youtube_api_get", new=fake_api_get),
    ):
        candidates = await fetch_youtube_candidates(max_results=5)

    assert len(candidates) == 1
    c = candidates[0]
    assert c["url"] == "https://www.youtube.com/watch?v=vid1"
    assert c["title"] == "Test Video"
    assert c["kind"] == "video"
    assert c["channel"] == "Test Channel"
    assert c["viewCount"] == 12345
    assert c["uploadDate"] == "20260722"  # ISO 8601 -> YYYYMMDD
    assert c["channel_url"] == channel_input_url
    assert c["channel_subscribers"] == 50000
    assert c["like_count"] == 100
    assert "description" in c
    assert "youtube" in c["topics"]


@pytest.mark.asyncio
async def test_fetch_youtube_candidates_skips_zero_view_videos():
    """A video the API reports with 0 views is dropped (matches the yt-dlp
    source's non-zero-view invariant so the gate sees only real view counts)."""
    canned = _canned_api_responses()
    # Override: the video has 0 views.
    canned["videos"]["items"][0]["statistics"]["viewCount"] = "0"

    async def fake_api_get(path, params):
        if "channels" in path:
            return canned["channels"]
        if "search" in path:
            return canned["search"]
        if "videos" in path:
            return canned["videos"]
        return {}

    with (
        patch("source.CHANNELS", ["https://www.youtube.com/@testchannel/videos"]),
        patch("source._youtube_api_get", new=fake_api_get),
    ):
        candidates = await fetch_youtube_candidates(max_results=5)

    assert candidates == []


@pytest.mark.asyncio
async def test_auth_error_raises_not_silently_swallowed():
    """A 403 auth failure is global (bad/expired key) — it must raise a clear
    error, not be silently swallowed into [] (which masks key-rotation issues).

    The raised RuntimeError must also NOT leak the API key via its chained
    cause: httpx's raise_for_status() embeds ``?key=...`` in the error message,
    so ``raise ... from e`` would let the key surface in any formatted
    traceback (GHA logs). The fix is ``raise ... from None``.
    """
    key = "AIzaFAKEKEY_must_not_leak"

    async def fake_api_get(path, params):
        # Reproduce the real leak vector: raise_for_status() builds the error
        # message from the request URL, which carries the key as a query param.
        req = httpx.Request(
            "GET",
            f"https://www.googleapis.com/youtube/v3/channels?key={key}&forHandle=test",
        )
        resp = httpx.Response(403, request=req)
        resp.raise_for_status()

    with (
        patch("source.CHANNELS", ["https://www.youtube.com/@testchannel/videos"]),
        patch("source._youtube_api_get", new=fake_api_get),
    ):
        with pytest.raises(RuntimeError, match="auth") as excinfo:
            await fetch_youtube_candidates(max_results=5)

    # The chained cause must be suppressed so a formatted traceback can't leak
    # the key (httpx embeds ?key=... in the cause's message).
    assert excinfo.value.__cause__ is None
    import traceback

    formatted = "".join(
        traceback.format_exception(
            type(excinfo.value), excinfo.value, excinfo.value.__traceback__
        )
    )
    assert key not in formatted


@pytest.mark.asyncio
async def test_error_log_excludes_api_key(caplog):
    """A non-auth API error (e.g. 500) soft-fails per channel, and the log line
    must NOT contain the API key — httpx embeds it in the request URL, which the
    default exception __str__ would leak into GHA logs.
    """
    key = "AIzaFAKEKEY_does_not_log"

    async def fake_api_get(path, params):
        # Reproduce how the real _youtube_api_get raises: resp.raise_for_status()
        # constructs the error message WITH the full request URL (which carries
        # the key as a query param), so a naive `logging.warning("...%s", e)`
        # would leak the key into GHA logs.
        req = httpx.Request(
            "GET",
            f"https://www.googleapis.com/youtube/v3/channels?key={key}&forHandle=test",
        )
        resp = httpx.Response(500, request=req)
        resp.raise_for_status()

    with (
        patch("source.CHANNELS", ["https://www.youtube.com/@testchannel/videos"]),
        patch("source._youtube_api_get", new=fake_api_get),
        caplog.at_level(logging.WARNING),
    ):
        result = await fetch_youtube_candidates(max_results=5)

    assert result == []  # soft-fail
    assert key not in caplog.text  # the key must not leak into logs


@pytest.mark.asyncio
async def test_videos_list_error_soft_fails():
    """A non-auth error in the batched videos.list call soft-fails (returns []
    — no stats means all candidates filter as zero-view) rather than crashing
    the whole run.
    """
    canned = _canned_api_responses()

    async def fake_api_get(path, params):
        if "channels" in path:
            return canned["channels"]
        if "search" in path:
            return canned["search"]
        if "videos" in path:
            req = httpx.Request("GET", "https://www.googleapis.com/youtube/v3/videos")
            resp = httpx.Response(500, request=req)
            raise httpx.HTTPStatusError("Server Error", request=req, response=resp)
        return {}

    with (
        patch("source.CHANNELS", ["https://www.youtube.com/@testchannel/videos"]),
        patch("source._youtube_api_get", new=fake_api_get),
    ):
        result = await fetch_youtube_candidates(max_results=5)

    assert result == []  # soft-fail, no crash


@pytest.mark.asyncio
async def test_missing_api_key_raises_not_soft_fails(monkeypatch):
    """A missing YOUTUBE_API_KEY is a global failure (the key is the
    prerequisite for the whole source), so fetch_youtube_candidates must raise
    immediately — not soft-fail per channel and mask as "no videos found".
    The per-channel except catches the RuntimeError that _youtube_api_get
    raises, so without an entry guard the missing key produces a silent [].
    """
    monkeypatch.delenv("YOUTUBE_API_KEY", raising=False)
    # _youtube_api_get is NOT patched — the real one should raise on the
    # missing key, and fetch_youtube_candidates must surface that, not swallow it.
    with patch("source.CHANNELS", ["https://www.youtube.com/@testchannel/videos"]):
        with pytest.raises(RuntimeError, match="YOUTUBE_API_KEY"):
            await fetch_youtube_candidates(max_results=5)
