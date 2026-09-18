"""Tests for cross-source HN engagement (boost, quote, story matching)."""

import os
import sys
from datetime import datetime, timedelta, timezone

import httpx
import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from _shared import engagement  # noqa: E402


class _Response:
    def __init__(self, status_code=200, payload=None, text=""):
        self.status_code = status_code
        self._payload = payload or {}
        self.text = text

    def json(self):
        return self._payload

    def raise_for_status(self):
        if self.status_code >= 400:
            raise httpx.HTTPError(f"HTTP {self.status_code}")


class _FakeClient:
    """Serves queued responses in order; records calls for assertion."""

    def __init__(self, responses):
        self._responses = list(responses)
        self.calls = []

    async def get(self, url, **kwargs):
        self.calls.append((url, kwargs))
        return self._responses.pop(0)


# ─── repo_slug ───


def test_repo_slug_extracts_owner_repo():
    assert engagement.repo_slug("https://github.com/owner/repo") == "owner/repo"
    assert engagement.repo_slug("https://github.com/owner/repo/") == "owner/repo"
    assert engagement.repo_slug("https://GitHub.com/Owner/Repo") == "Owner/Repo"
    assert engagement.repo_slug("https://gitlab.com/owner/repo") is None
    assert engagement.repo_slug("") is None


# ─── engagement_boost ───


def test_engagement_boost_zero_without_attention():
    assert engagement.engagement_boost(0, 0) == 0
    assert engagement.engagement_boost(5, 2) == 0


def test_engagement_boost_full_marks_at_front_page_scale():
    assert engagement.engagement_boost(200, 100) == 15


def test_engagement_boost_caps_beyond_full_scale():
    assert engagement.engagement_boost(1000, 500) == 15


def test_engagement_boost_partial_story():
    # 30 points -> int(30/200*10)=1; 10 comments -> int(10/100*5)=0
    assert engagement.engagement_boost(30, 10) == 1


# ─── strip_html / pick_top_comment ───


def test_strip_html_collapses_markup():
    assert (
        engagement.strip_html('<p>hello <a href="x">world</a></p>\n')
        == "hello world"
    )


def test_pick_top_comment_skips_empty_and_deleted():
    children = [
        {"author": "bob", "text": ""},
        {"author": "[deleted]", "text": "gone"},
        {"author": "alice", "text": "<p>first real take</p>"},
    ]
    top = engagement.pick_top_comment(children)
    assert top == {"author": "alice", "text": "first real take"}


def test_pick_top_comment_truncates_long_text():
    children = [{"author": "alice", "text": "x" * 1000}]
    top = engagement.pick_top_comment(children)
    assert len(top["text"]) == 280


def test_pick_top_comment_empty_children():
    assert engagement.pick_top_comment([]) is None
    assert engagement.pick_top_comment(None) is None


# ─── fetch_hn_engagement ───


@pytest.mark.asyncio
async def test_fetch_hn_engagement_finds_url_matching_story():
    now = datetime.now(timezone.utc)
    story = {
        "objectID": "111",
        "url": "https://github.com/owner/repo",
        "title": "Show HN: Repo",
        "points": 120,
        "num_comments": 45,
        "created_at": (now - timedelta(days=2)).isoformat(),
    }
    other = {
        "objectID": "222",
        "url": "https://github.com/other/thing",
        "title": "Unrelated",
        "points": 500,
        "num_comments": 99,
    }
    search = _Response(payload={"hits": [other, story]})
    item = _Response(
        payload={
            "children": [{"author": "alice", "text": "<p>great find</p>"}]
        }
    )
    client = _FakeClient([search, item])

    result = await engagement.fetch_hn_engagement(
        "https://github.com/owner/repo", client
    )

    assert result is not None
    assert result["points"] == 120
    assert result["comments"] == 45
    assert result["story_url"] == "https://news.ycombinator.com/item?id=111"
    assert result["top_comment"] == {"author": "alice", "text": "great find"}
    # The search must be a phrase query over stories within the window.
    url, kwargs = client.calls[0]
    assert kwargs["params"]["query"] == '"owner/repo"'
    assert kwargs["params"]["tags"] == "story"


@pytest.mark.asyncio
async def test_fetch_hn_engagement_matches_title_when_no_url():
    hit = {
        "objectID": "333",
        "url": "",
        "title": "I built Owner/Repo for fun",
        "points": 10,
        "num_comments": 2,
    }
    client = _FakeClient([_Response(payload={"hits": [hit]}), _Response(payload={})])
    result = await engagement.fetch_hn_engagement(
        "https://github.com/owner/repo", client
    )
    assert result is not None
    assert result["points"] == 10
    assert result["top_comment"] is None


@pytest.mark.asyncio
async def test_fetch_hn_engagement_no_match_returns_none():
    client = _FakeClient([_Response(payload={"hits": []})])
    result = await engagement.fetch_hn_engagement(
        "https://github.com/owner/repo", client
    )
    assert result is None


@pytest.mark.asyncio
async def test_fetch_hn_engagement_non_github_url_returns_none():
    client = _FakeClient([])
    assert await engagement.fetch_hn_engagement("https://example.com", client) is None
    assert client.calls == []


@pytest.mark.asyncio
async def test_fetch_hn_engagement_item_failure_keeps_story():
    story = {
        "objectID": "444",
        "url": "https://github.com/owner/repo",
        "title": "Show HN: Repo",
        "points": 60,
        "num_comments": 12,
    }
    client = _FakeClient(
        [
            _Response(payload={"hits": [story]}),
            _Response(status_code=500),  # quote fetch fails
        ]
    )
    result = await engagement.fetch_hn_engagement(
        "https://github.com/owner/repo", client
    )
    assert result is not None
    assert result["points"] == 60
    assert result["top_comment"] is None  # the quote is enrichment, not a blocker


@pytest.mark.asyncio
async def test_fetch_hn_engagement_search_failure_degrades_to_none():
    """The HN search leg must degrade, never raise — a failed check is
    'no story measured', not a dead run."""
    client = _FakeClient([_Response(status_code=500)])
    result = await engagement.fetch_hn_engagement(
        "https://github.com/owner/repo", client
    )
    assert result is None


@pytest.mark.asyncio
async def test_story_title_match_requires_slug_boundary():
    """A story about owner/repo-utils must not corroborate owner/repo."""
    hit = {
        "objectID": "555",
        "url": "",
        "title": "I built owner/repo-utils and it rocks",
        "points": 400,
        "num_comments": 99,
    }
    client = _FakeClient([_Response(payload={"hits": [hit]}), _Response(payload={})])
    result = await engagement.fetch_hn_engagement(
        "https://github.com/owner/repo", client
    )
    assert result is None  # the boundary regex rejects the longer slug


@pytest.mark.asyncio
async def test_story_title_match_is_case_insensitive():
    hit = {
        "objectID": "666",
        "url": "",
        "title": "Show HN: Owner/Repo — a new take",
        "points": 40,
        "num_comments": 8,
    }
    client = _FakeClient([_Response(payload={"hits": [hit]}), _Response(payload={})])
    result = await engagement.fetch_hn_engagement(
        "https://github.com/owner/repo", client
    )
    assert result is not None
    assert result["points"] == 40
