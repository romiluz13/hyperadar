"""Tests for the cross-source corroboration gate (2+ sources to publish)."""

import logging
import os
import sys
import time
from datetime import datetime, timezone

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

    async def reddit_fetch(url, corpus=None):
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

    async def reddit_fetch(url, corpus=None):
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

    async def reddit_fetch(url, corpus=None):
        return False

    kept = await corroboration.corroborated_candidates(
        [_candidate(momentumScore=95)], hn_fetch=hn_fetch, reddit_fetch=reddit_fetch
    )
    assert kept == []


@pytest.mark.asyncio
async def test_reddit_unknown_does_not_corroborate():
    async def hn_fetch(url):
        return None

    async def reddit_fetch(url, corpus=None):
        return None  # blocked / failed check

    kept = await corroboration.corroborated_candidates(
        [_candidate()], hn_fetch=hn_fetch, reddit_fetch=reddit_fetch
    )
    assert kept == []


@pytest.mark.asyncio
async def test_both_external_sources_listed():
    async def hn_fetch(url):
        return _HN_STORY

    async def reddit_fetch(url, corpus=None):
        return True

    kept = await corroboration.corroborated_candidates(
        [_candidate()], hn_fetch=hn_fetch, reddit_fetch=reddit_fetch
    )
    assert kept[0]["corroborated_by"] == ["github", "hacker_news", "reddit"]


@pytest.mark.asyncio
async def test_filters_per_candidate():
    async def hn_fetch(url):
        return _HN_STORY if "hot-repo" in url else None

    async def reddit_fetch(url, corpus=None):
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


# ─── corpus leg (@reddit-pulse snapshots) ───


class _FakeCursor:
    def __init__(self, docs):
        self._docs = list(docs)

    async def to_list(self, length=None):
        return self._docs


class _FakeSnapshots:
    def __init__(self, docs):
        self._docs = list(docs)
        self.queries = []

    def find(self, query, projection):
        self.queries.append(query)
        return _FakeCursor(self._docs)


class _FakeDb:
    def __init__(self, docs):
        self.reddit_post_snapshots = _FakeSnapshots(docs)


class _NoNetworkClient:
    """Any live call during a corpus-authoritative test is a bug."""

    async def get(self, url, **kwargs):
        raise AssertionError(f"live Reddit search must not run: {url}")


@pytest.mark.asyncio
async def test_corpus_mention_in_title():
    """The corpus answers authoritatively — no live Reddit call at all."""
    result = await corroboration.fetch_reddit_mention(
        "https://github.com/owner/repo",
        _NoNetworkClient(),
        corpus=["Show HN-adjacent: owner/repo is great"],
    )
    assert result is True


@pytest.mark.asyncio
async def test_corpus_mention_in_description_case_insensitive():
    result = await corroboration.fetch_reddit_mention(
        "https://github.com/owner/repo",
        _NoNetworkClient(),
        corpus=["anyone tried this? ", "paper thread about Owner/Repo here"],
    )
    assert result is True


@pytest.mark.asyncio
async def test_corpus_mention_requires_slug_boundary():
    """A thread about owner/repo-utils must not corroborate owner/repo."""
    result = await corroboration.fetch_reddit_mention(
        "https://github.com/owner/repo",
        _NoNetworkClient(),
        corpus=["owner/repo-utils discussion", "check out owner/repo2"],
    )
    assert result is False


@pytest.mark.asyncio
async def test_empty_corpus_falls_back_to_live():
    """No @reddit-pulse data → the (usually blocked) live leg is the fallback."""
    now = time.time()
    payload = {
        "data": {
            "children": [
                {"data": {"title": "owner/repo thread", "created_utc": now - 60}}
            ]
        }
    }
    client = _FakeClient([_Response(payload=payload)])
    result = await corroboration.fetch_reddit_mention(
        "https://github.com/owner/repo", client, corpus=[]
    )
    assert result is True
    assert len(client.calls) == 1


@pytest.mark.asyncio
async def test_corpus_absent_falls_back_to_live():
    payload = {"data": {"children": []}}
    client = _FakeClient([_Response(payload=payload)])
    result = await corroboration.fetch_reddit_mention(
        "https://github.com/owner/repo", client, corpus=None
    )
    assert result is False


@pytest.mark.asyncio
async def test_load_reddit_corpus_reads_window_and_text():
    db = _FakeDb(
        [
            {"title": "owner/repo thread", "description": "nice"},
            {"title": "other", "description": "github.com/owner/repo discussed"},
        ]
    )
    corpus = await corroboration.load_reddit_corpus(db)
    assert corpus == [
        "owner/repo thread nice",
        "other github.com/owner/repo discussed",
    ]
    (query,) = db.reddit_post_snapshots.queries
    since = query["capturedAt"]["$gte"]
    age_days = (
        datetime.now(timezone.utc) - since
    ).total_seconds() / 86400
    assert 13.9 < age_days < 14.1  # the corroboration window, not 30d


@pytest.mark.asyncio
async def test_load_reddit_corpus_db_error_returns_empty():
    class _BrokenSnapshots:
        def find(self, query, projection):
            raise RuntimeError("atlas down")

    class _BrokenDb:
        reddit_post_snapshots = _BrokenSnapshots()

    corpus = await corroboration.load_reddit_corpus(_BrokenDb())
    assert corpus == []


@pytest.mark.asyncio
async def test_blocked_live_leg_warns_once_per_process(monkeypatch, caplog):
    monkeypatch.setattr(corroboration, "_live_reddit_warned", False)
    with caplog.at_level(logging.WARNING):
        for _ in range(3):
            result = await corroboration.fetch_reddit_mention(
                "https://github.com/owner/repo", _FakeClient([_Response(403)])
            )
            assert result is None
    warnings = [r for r in caplog.records if r.levelno == logging.WARNING]
    assert len(warnings) == 1
    assert "@reddit-pulse corpus" in warnings[0].getMessage()


@pytest.mark.asyncio
async def test_corroborated_candidates_preloads_corpus_once():
    """The corpus is one query per pool, not one per candidate."""
    db = _FakeDb([{"title": "owner/hot-repo thread", "description": ""}])
    seen = []

    async def reddit_fetch(url, corpus=None):
        seen.append(corpus)
        slug = url.rsplit("/", 1)[-1]
        return bool(corpus and any(slug in t for t in corpus))

    async def hn_fetch(url):
        return None

    kept = await corroboration.corroborated_candidates(
        [
            _candidate(url="https://github.com/owner/hot-repo"),
            _candidate(url="https://github.com/owner/cold-repo"),
        ],
        db=db,
        hn_fetch=hn_fetch,
        reddit_fetch=reddit_fetch,
    )
    assert len(db.reddit_post_snapshots.queries) == 1
    assert seen == [
        ["owner/hot-repo thread "],
        ["owner/hot-repo thread "],
    ]
    assert [c["url"] for c in kept] == ["https://github.com/owner/hot-repo"]
    assert kept[0]["corroborated_by"] == ["github", "reddit"]


@pytest.mark.asyncio
async def test_corroborated_candidates_without_db_passes_none_corpus():
    async def reddit_fetch(url, corpus=None):
        assert corpus is None
        return False

    async def hn_fetch(url):
        return _HN_STORY

    kept = await corroboration.corroborated_candidates(
        [_candidate()], hn_fetch=hn_fetch, reddit_fetch=reddit_fetch
    )
    assert len(kept) == 1
