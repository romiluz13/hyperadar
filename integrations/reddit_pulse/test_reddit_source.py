"""Tests for Reddit source cooldown and engagement_velocity."""

import asyncio
import importlib.util
import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest


class FakeProcess:
    def __init__(self, output: str):
        self.output = output
        self.returncode = 0

    async def communicate(self):
        return self.output.encode(), b""


class HangingProcess:
    def __init__(self):
        self.returncode = None
        self.killed = False
        self._released = asyncio.Event()

    async def communicate(self):
        await self._released.wait()
        return b"", b""

    def kill(self):
        self.killed = True
        self.returncode = -9
        self._released.set()


def test_normalize_reddit_url_strips_query_params():
    source = load_source()
    assert (
        source._normalize_reddit_url(
            "https://www.reddit.com/r/LocalLLaMA/comments/abc/?utm_source=share"
        )
        == "https://www.reddit.com/r/LocalLLaMA/comments/abc"
    )
    assert (
        source._normalize_reddit_url(
            "https://www.reddit.com/r/LocalLLaMA/comments/abc/"
        )
        == "https://www.reddit.com/r/LocalLLaMA/comments/abc"
    )
    assert (
        source._normalize_reddit_url("https://www.reddit.com/r/LocalLLaMA/comments/abc")
        == "https://www.reddit.com/r/LocalLLaMA/comments/abc"
    )


def load_source():
    path = Path(__file__).parent / "reddit_source.py"
    spec = importlib.util.spec_from_file_location("reddit_test_source", path)
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _make_post(
    url: str,
    title: str = "Test post",
    upvotes: int = 100,
    comments: int = 10,
    community_name: str = "LocalLLaMA",
    created_utc: float | None = None,
) -> dict:
    post = {
        "url": url,
        "title": title,
        "description": "desc",
        "num_upvotes": upvotes,
        "num_comments": comments,
        "community_name": community_name,
    }
    if created_utc is not None:
        post["created_utc"] = created_utc
    return post


class FakePosts:
    """Mock posts collection. Returns canned find_one results by URL."""

    def __init__(self, url_to_post: dict | None = None):
        self._url_to_post = url_to_post or {}

    async def find_one(self, query, *_args, **_kwargs):
        url = query.get("project.url", "")
        # Normalize keys so trailing-slash / query-param variants match
        for stored_url, value in self._url_to_post.items():
            normalized_stored = stored_url.split("?")[0].rstrip("/")
            if normalized_stored == url:
                return value
        return None


class FakeDb:
    def __init__(self, url_to_post: dict | None = None):
        self.posts = FakePosts(url_to_post)


@pytest.mark.asyncio
async def test_cooldown_skips_recently_posted_threads(monkeypatch):
    source = load_source()
    monkeypatch.setattr(source.shutil, "which", lambda _: "/usr/local/bin/bdata")

    old_url = "https://www.reddit.com/r/LocalLLaMA/comments/old/thread_old/"
    recent_url = "https://www.reddit.com/r/LocalLLaMA/comments/new/thread_new/"

    async def fake_create_subprocess_exec(*_args, **_kwargs):
        return FakeProcess(
            json.dumps(
                [
                    _make_post(old_url, upvotes=200, title="Old thread"),
                    _make_post(recent_url, upvotes=500, title="Recent thread"),
                ]
            )
        )

    monkeypatch.setattr(
        source.asyncio, "create_subprocess_exec", fake_create_subprocess_exec
    )

    # Recent post was published 2 days ago → should be filtered out.
    recent_posted_at = datetime.now(timezone.utc) - timedelta(days=2)
    db = FakeDb(
        url_to_post={
            recent_url: {"postedAt": recent_posted_at},
        }
    )

    candidates = await source.fetch_reddit_candidates(max_results=10, db=db)

    urls = [c["url"] for c in candidates]
    assert source._normalize_reddit_url(old_url) in urls
    assert source._normalize_reddit_url(recent_url) not in urls


@pytest.mark.asyncio
async def test_cooldown_keeps_threads_posted_7_or_more_days_ago(monkeypatch):
    source = load_source()
    monkeypatch.setattr(source.shutil, "which", lambda _: "/usr/local/bin/bdata")

    url = "https://www.reddit.com/r/MachineLearning/comments/abc/kept_thread/"

    async def fake_create_subprocess_exec(*_args, **_kwargs):
        return FakeProcess(json.dumps([_make_post(url, upvotes=150)]))

    monkeypatch.setattr(
        source.asyncio, "create_subprocess_exec", fake_create_subprocess_exec
    )

    # Posted exactly 7 days ago → should be kept (>= COOLDOWN_DAYS).
    posted_at = datetime.now(timezone.utc) - timedelta(days=7)
    db = FakeDb(url_to_post={url: {"postedAt": posted_at}})

    candidates = await source.fetch_reddit_candidates(max_results=10, db=db)
    assert len(candidates) == 1
    assert candidates[0]["url"] == source._normalize_reddit_url(url)


@pytest.mark.asyncio
async def test_cooldown_keeps_never_posted_threads(monkeypatch):
    source = load_source()
    monkeypatch.setattr(source.shutil, "which", lambda _: "/usr/local/bin/bdata")

    url = "https://www.reddit.com/r/singularity/comments/xyz/never_posted/"

    async def fake_create_subprocess_exec(*_args, **_kwargs):
        return FakeProcess(json.dumps([_make_post(url, upvotes=120)]))

    monkeypatch.setattr(
        source.asyncio, "create_subprocess_exec", fake_create_subprocess_exec
    )

    db = FakeDb()  # no posts at all

    candidates = await source.fetch_reddit_candidates(max_results=10, db=db)
    assert len(candidates) == 1
    assert candidates[0]["url"] == source._normalize_reddit_url(url)


@pytest.mark.asyncio
async def test_engagement_velocity_is_computed_and_sorted(monkeypatch):
    source = load_source()
    monkeypatch.setattr(source.shutil, "which", lambda _: "/usr/local/bin/bdata")

    # First post: 100 upvotes, 1 hour old → velocity 100
    # Second post: 300 upvotes, 100 hours old → velocity 3
    # The first post should rank higher after sorting.
    now = datetime.now(timezone.utc)
    post_a = _make_post(
        "https://www.reddit.com/r/LocalLLaMA/comments/a/thread_a/",
        upvotes=100,
        created_utc=(now - timedelta(hours=1)).timestamp(),
    )
    post_b = _make_post(
        "https://www.reddit.com/r/LocalLLaMA/comments/b/thread_b/",
        upvotes=300,
        created_utc=(now - timedelta(hours=100)).timestamp(),
    )

    async def fake_create_subprocess_exec(*_args, **_kwargs):
        return FakeProcess(json.dumps([post_a, post_b]))

    monkeypatch.setattr(
        source.asyncio, "create_subprocess_exec", fake_create_subprocess_exec
    )

    db = FakeDb()
    candidates = await source.fetch_reddit_candidates(max_results=10, db=db)

    assert len(candidates) == 2
    assert "engagement_velocity" in candidates[0]
    assert "engagement_velocity" in candidates[1]
    # Higher velocity first
    assert candidates[0]["engagement_velocity"] >= candidates[1]["engagement_velocity"]
    assert (
        candidates[0]["url"]
        == "https://www.reddit.com/r/LocalLLaMA/comments/a/thread_a"
    )


@pytest.mark.asyncio
async def test_engagement_velocity_falls_back_to_1_hour(monkeypatch):
    source = load_source()
    monkeypatch.setattr(source.shutil, "which", lambda _: "/usr/local/bin/bdata")

    url = "https://www.reddit.com/r/OpenAI/comments/fb/fallback/"
    # No created_utc field → age defaults to 1 hour → velocity = upvotes / 1
    post = _make_post(url, upvotes=42, created_utc=None)

    async def fake_create_subprocess_exec(*_args, **_kwargs):
        return FakeProcess(json.dumps([post]))

    monkeypatch.setattr(
        source.asyncio, "create_subprocess_exec", fake_create_subprocess_exec
    )

    db = FakeDb()
    candidates = await source.fetch_reddit_candidates(max_results=10, db=db)
    assert len(candidates) == 1
    assert candidates[0]["engagement_velocity"] == 42.0


@pytest.mark.asyncio
async def test_cooldown_matches_despite_trailing_slash(db):
    """Cooldown should match even if stored URL has trailing slash.

    Regression test for asymmetric URL normalization: the candidate dict
    stores the raw URL while the cooldown lookup normalizes it. After the fix,
    the URL is normalized at construction so both stored and lookup sides match.
    """
    from _shared.mongo import _get_db

    source = load_source()
    url_with_slash = "https://www.reddit.com/r/LocalLLaMA/comments/abc123/"
    # Insert a post with the normalized URL posted 2 days ago
    db.posts.insert_one(
        {
            "project": {"url": source._normalize_reddit_url(url_with_slash)},
            "postedAt": datetime.now(timezone.utc) - timedelta(days=2),
        }
    )

    # The cooldown should find this post and skip it (< COOLDOWN_DAYS)
    test_db = _get_db()
    days = await source._last_posted_days(test_db, url_with_slash)
    assert days < source.COOLDOWN_DAYS, (
        f"Should be < {source.COOLDOWN_DAYS} days, got {days}"
    )

    # Cleanup
    db.posts.delete_one({"project.url": source._normalize_reddit_url(url_with_slash)})
