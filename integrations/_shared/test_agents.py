"""T5 per-agent tests: each agent's source → write path with mocked sources.

Tests that each agent-creator can fetch candidates (mocked) and write a post
to MongoDB + Port via the shared write_post function.
"""

import os
import sys
from datetime import datetime, timezone

import pytest
from bson import ObjectId

# Add parent dir so we can import _shared
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from _shared.write_post import write_post  # noqa: E402


@pytest.fixture()
def db():
    import pymongo
    from dotenv import load_dotenv

    load_dotenv()
    client = pymongo.MongoClient(os.environ["MONGODB_URI"])
    return client[os.environ.get("MONGODB_DB", "hyperadar")]


def _cleanup(db, url):
    db.posts.delete_many({"project.url": url})
    db.projects.delete_many({"url": url})
    db.signals.delete_many({"projectId": url})


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
                "r/test can't shut up about this — 50 upvotes in 2h.",
                "hype looks real",
                {
                    "source": "reddit",
                    "metric": "upvotes",
                    "value": 50,
                    "delta": 12,
                    "summary": "upvotes=50, comments=12",
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
                "This 12-min demo hit 40k views in 48h.",
                "hype looks real",
                {
                    "source": "youtube",
                    "metric": "views",
                    "value": 40000,
                    "delta": 0,
                    "summary": "serp_rank=1",
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
                    "description": "47 stars but rising",
                    "topics": ["hn", "hidden-gem"],
                    "momentumScore": 45.0,
                    "hypeVerdict": "emerging",
                },
                "47 stars. But look at the trajectory.",
                "emerging",
                {
                    "source": "hn",
                    "metric": "stars",
                    "value": 47,
                    "delta": 0,
                    "summary": "stars=47, hidden gem",
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
                "test",
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
