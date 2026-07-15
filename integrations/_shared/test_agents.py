"""T5 per-agent tests: each agent's source → write path with mocked sources.

Tests that each agent-creator can fetch candidates (mocked) and write a post
through the shared persistence path. Port calls are isolated from the live catalog.
"""

import os
import sys
from datetime import datetime, timezone

import pytest
from bson import ObjectId

# Add parent dir so we can import _shared
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from _shared.write_post import write_post  # noqa: E402


@pytest.fixture(autouse=True)
def _isolate_port_catalog(monkeypatch):
    from _shared import port_client

    monkeypatch.setattr(port_client, "upsert_agent", lambda *_args: {"ok": True})
    monkeypatch.setattr(port_client, "upsert_project", lambda *_args: {"ok": True})
    monkeypatch.setattr(port_client, "upsert_post", lambda *_args: {"ok": True})


def _cleanup(db, url):
    db.posts.delete_many({"project.url": url})
    db.projects.delete_many({"url": url})
    db.signals.delete_many({"projectId": url})
    db.signal_receipts.delete_many({"signal.projectId": url})
    db.embeddings_audit.delete_many({"projectId": url})


class TestRedditPulse:
    """@reddit-pulse: mocked Reddit source → write_post."""

    async def test_reddit_candidate_writes_post(self, db):
        url = f"https://www.reddit.com/r/test/test-{datetime.now(timezone.utc).timestamp()}"
        try:
            post_id = await write_post(
                "@reddit-pulse",
                "Reddit Pulse",
                "test bio",
                "reddit",
                {
                    "url": url,
                    "title": "r/test trending post",
                    "kind": "thread",
                    "description": "test desc",
                    "topics": ["reddit", "ai"],
                    "momentumScore": 65.0,
                    "hypeVerdict": "hype looks real",
                },
                "Google search surfaced this Reddit result at position 4; 60/100 is a visibility proxy, not engagement.",
                "hype looks real",
                {
                    "source": "reddit",
                    "metric": "search visibility",
                    "value": 60,
                    "delta": 0,
                    "summary": "Google SERP rank=4; visibility proxy=60/100",
                },
                65.0,
            )
            assert post_id
            post = db.posts.find_one({"_id": ObjectId(post_id)})
            assert post["agentHandle"] == "@reddit-pulse"
            assert post["project"]["kind"] == "thread"
        finally:
            _cleanup(db, url)


class TestYouTubeTrends:
    """@youtube-trends: mocked YouTube source → write_post."""

    async def test_youtube_candidate_writes_post(self, db):
        url = f"https://www.youtube.com/watch?v=test{datetime.now(timezone.utc).timestamp()}"
        try:
            post_id = await write_post(
                "@youtube-trends",
                "YouTube Trends",
                "test bio",
                "youtube",
                {
                    "url": url,
                    "title": "AI Agent Demo",
                    "kind": "video",
                    "description": "12-min demo",
                    "topics": ["youtube", "ai"],
                    "momentumScore": 80.0,
                    "hypeVerdict": "hype looks real",
                },
                "40k YouTube views observed. This 12-minute agent demo is worth inspecting.",
                "hype looks real",
                {
                    "source": "youtube",
                    "metric": "views",
                    "value": 40000,
                    "delta": 0,
                    "summary": "YouTube views=40000; search position=1 for a named query",
                },
                80.0,
            )
            assert post_id
            post = db.posts.find_one({"_id": ObjectId(post_id)})
            assert post["agentHandle"] == "@youtube-trends"
            assert post["project"]["kind"] == "video"
        finally:
            _cleanup(db, url)


class TestHiddenGems:
    """@hidden-gems: mocked HN/GitHub source → write_post."""

    async def test_hidden_gem_writes_post(self, db):
        url = f"https://github.com/test/hidden-gem-{datetime.now(timezone.utc).timestamp()}"
        try:
            post_id = await write_post(
                "@hidden-gems",
                "Hidden Gems",
                "test bio",
                "web",
                {
                    "url": url,
                    "title": "test/hidden-gem",
                    "kind": "repo",
                    "description": "47 GitHub stars in a recent-repository search",
                    "topics": ["hn", "hidden-gem"],
                    "momentumScore": 45.0,
                    "hypeVerdict": "emerging",
                },
                "47 GitHub stars observed; growth trajectory was not measured.",
                "emerging",
                {
                    "source": "hn",
                    "metric": "github_stars",
                    "value": 47,
                    "delta": 0,
                    "summary": "GitHub stars=47; discovered in recent-repository search",
                },
                45.0,
            )
            assert post_id
            post = db.posts.find_one({"_id": ObjectId(post_id)})
            assert post["agentHandle"] == "@hidden-gems"
            assert post["verdict"] == "emerging"
        finally:
            _cleanup(db, url)


class TestUrlValidation:
    """URL scheme validation in write_post (security fix)."""

    async def test_javascript_url_rejected(self, db):
        url = "javascript:alert('xss')"
        with pytest.raises(ValueError, match="Invalid URL scheme"):
            await write_post(
                "@test",
                "test",
                "test",
                "test",
                {
                    "url": url,
                    "title": "xss",
                    "kind": "site",
                    "description": "",
                    "topics": [],
                    "momentumScore": 0,
                    "hypeVerdict": "inflated",
                },
                "test",
                "inflated",
                {"source": "test", "metric": "mentions", "value": 0, "delta": 0},
                0,
            )

    async def test_https_url_accepted(self, db):
        url = f"https://github.com/test/safe-{datetime.now(timezone.utc).timestamp()}"
        try:
            post_id = await write_post(
                "@test",
                "test",
                "test",
                "web",
                {
                    "url": url,
                    "title": "safe",
                    "kind": "repo",
                    "description": "",
                    "topics": [],
                    "momentumScore": 50,
                    "hypeVerdict": "emerging",
                },
                "test",
                "emerging",
                {"source": "test", "metric": "stars", "value": 100, "delta": 0},
                50,
            )
            assert post_id
        finally:
            _cleanup(db, url)
