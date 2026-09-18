"""Tests for the cross-source corroboration gate (2+ sources to publish)."""

import os
import sys
import time

import httpx
import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from _shared import corroboration  # noqa: E402


def _candidate(url="https://github.com/owner/repo", **extra):
    c = {"url": url, "title": "owner/repo", "momentumScore": 70}
    c.update(extra)
    return c


_HN_STORY = {
    "points": 80,
    "comments": 25,
    "story_url": "https://news.ycombinator.com/item?id=1",
    "story_title": "Show HN: owner/repo",
    "top_comment": None,
}


class _Response:
    def __init__(self, status_code=200, payload=None):
        self.status_code = status_code
        self._payload = payload or {}

    def json(self):
        return self._payload


class _FakeClient:
    def __init__(self, responses):
        self._responses = list(responses)
        self.calls = []

    async def get(self, url, **kwargs):
        self.calls.append((url, kwargs))
        return self._responses.pop(0)


# ─── corroborated_candidates ───


@pytest.mark.asyncio
async def test_keeps_candidate_with_two_sources():
    async def hn_fetch(url):
        return _HN_STORY

    async def reddit_fetch(url):
        return False

    kept = await corroboration.corroborated_candidates(
        [_candidate()], hn_fetch=hn_fetch, reddit_fetch=reddit_fetch
    )
    assert len(kept) == 1
    assert kept[0]["corroborated_by"] == ["github", "hacker_news"]
    assert kept[0]["_hn_engagement"] == _HN_STORY


@pytest.mark.asyncio
async def test_keeps_candidate_with_reddit_instead_of_hn():
    async def hn_fetch(url):
        return None

    async def reddit_fetch(url):
        return True

    kept = await corroboration.corroborated_candidates(
        [_candidate()], hn_fetch=hn_fetch, reddit_fetch=reddit_fetch
    )
    assert len(kept) == 1
    assert kept[0]["corroborated_by"] == ["github", "reddit"]
    assert "_hn_engagement" not in kept[0]


@pytest.mark.asyncio
async def test_drops_single_source_candidate():
    """GitHub alone — however fast — is exactly the hype the gate filters."""

    async def hn_fetch(url):
        return None

    async def reddit_fetch(url):
        return False

    kept = await corroboration.corroborated_candidates(
        [_candidate(momentumScore=95)], hn_fetch=hn_fetch, reddit_fetch=reddit_fetch
    )
    assert kept == []


@pytest.mark.asyncio
async def test_reddit_unknown_does_not_corroborate():
    async def hn_fetch(url):
        return None

    async def reddit_fetch(url):
        return None  # blocked / failed check

    kept = await corroboration.corroborated_candidates(
        [_candidate()], hn_fetch=hn_fetch, reddit_fetch=reddit_fetch
    )
    assert kept == []


@pytest.mark.asyncio
async def test_both_external_sources_listed():
    async def hn_fetch(url):
        return _HN_STORY

    async def reddit_fetch(url):
        return True

    kept = await corroboration.corroborated_candidates(
        [_candidate()], hn_fetch=hn_fetch, reddit_fetch=reddit_fetch
    )
    assert kept[0]["corroborated_by"] == ["github", "hacker_news", "reddit"]


@pytest.mark.asyncio
async def test_filters_per_candidate():
    async def hn_fetch(url):
        return _HN_STORY if "hot-repo" in url else None

    async def reddit_fetch(url):
        return False

    kept = await corroboration.corroborated_candidates(
        [
            _candidate(url="https://github.com/owner/cold-repo"),
            _candidate(url="https://github.com/owner/hot-repo"),
        ],
        hn_fetch=hn_fetch,
        reddit_fetch=reddit_fetch,
    )
    assert [c["url"] for c in kept] == ["https://github.com/owner/hot-repo"]


# ─── fetch_reddit_mention ───


@pytest.mark.asyncio
async def test_hn_leg_uses_corroboration_window_not_engagement_window(monkeypatch):
    """The gate's HN check measures 14 days, not the 30-day boost window."""
    calls = {}

    async def fake_engagement(repo_url, client=None, max_age_days=30):
        calls["max_age_days"] = max_age_days
        return None

    monkeypatch.setattr(corroboration, "fetch_hn_engagement", fake_engagement)
    result = await corroboration._hn_within_window(
        "https://github.com/owner/repo"
    )
    assert result is None
    assert calls["max_age_days"] == 14


@pytest.mark.asyncio
async def test_reddit_mention_found():
    now = time.time()
    payload = {
        "data": {
            "children": [
                {
                    "data": {
                        "title": "Someone posted OWNER/REPO today",
                        "url": "",
                        "created_utc": now - 86400,  # yesterday
                    }
                },
                {"data": {"title": "unrelated", "url": "https://x.com"}},
            ]
        }
    }
    client = _FakeClient([_Response(payload=payload)])
    result = await corroboration.fetch_reddit_mention(
        "https://github.com/owner/repo", client
    )
    assert result is True
    # Search must be a quoted slug query over the last month.
    url, kwargs = client.calls[0]
    assert kwargs["params"]["q"] == '"owner/repo"'
    assert kwargs["params"]["t"] == "month"


@pytest.mark.asyncio
async def test_reddit_mention_selftext_counts():
    """A text post discussing the repo in the body corroborates it."""
    now = time.time()
    payload = {
        "data": {
            "children": [
                {
                    "data": {
                        "title": "anyone tried this?",
                        "url": "",
                        "selftext": "I mean github.com/owner/repo — looks solid",
                        "created_utc": now - 3600,
                    }
                }
            ]
        }
    }
    client = _FakeClient([_Response(payload=payload)])
    result = await corroboration.fetch_reddit_mention(
        "https://github.com/owner/repo", client
    )
    assert result is True


@pytest.mark.asyncio
async def test_reddit_mention_outside_window_is_not_corroboration():
    """A month-old thread is history, not corroboration of today's trending."""
    payload = {
        "data": {
            "children": [
                {
                    "data": {
                        "title": "owner/repo discussion",
                        "url": "",
                        "created_utc": time.time() - 30 * 86400,  # 30 days old
                    }
                }
            ]
        }
    }
    client = _FakeClient([_Response(payload=payload)])
    result = await corroboration.fetch_reddit_mention(
        "https://github.com/owner/repo", client
    )
    assert result is False


@pytest.mark.asyncio
async def test_reddit_mention_absent():
    payload = {"data": {"children": [{"data": {"title": "other things", "url": ""}}]}}
    client = _FakeClient([_Response(payload=payload)])
    result = await corroboration.fetch_reddit_mention(
        "https://github.com/owner/repo", client
    )
    assert result is False


@pytest.mark.asyncio
async def test_reddit_mention_blocked_returns_none():
    client = _FakeClient([_Response(status_code=403)])
    result = await corroboration.fetch_reddit_mention(
        "https://github.com/owner/repo", client
    )
    assert result is None


@pytest.mark.asyncio
async def test_reddit_mention_error_payload_returns_none():
    client = _FakeClient([_Response(payload={"error": 403})])
    result = await corroboration.fetch_reddit_mention(
        "https://github.com/owner/repo", client
    )
    assert result is None


@pytest.mark.asyncio
async def test_reddit_mention_http_error_returns_none():
    class _Boom:
        async def get(self, url, **kwargs):
            raise httpx.ConnectError("nope")

    result = await corroboration.fetch_reddit_mention(
        "https://github.com/owner/repo", _Boom()
    )
    assert result is None


@pytest.mark.asyncio
async def test_reddit_mention_non_github_url_returns_none():
    client = _FakeClient([])
    result = await corroboration.fetch_reddit_mention("https://example.com", client)
    assert result is None
    assert client.calls == []
