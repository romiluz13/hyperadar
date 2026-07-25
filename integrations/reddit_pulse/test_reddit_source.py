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
    post_id: str | None = None,
) -> dict:
    if post_id is None and "/comments/" in url:
        post_id = f"t3_{url.split('/comments/', 1)[1].split('/', 1)[0]}"
    post = {
        "url": url,
        "title": title,
        "description": "desc",
        "num_upvotes": upvotes,
        "num_comments": comments,
        "community_name": community_name,
    }
    if post_id is not None:
        post["post_id"] = post_id
    if created_utc is not None:
        post["created_utc"] = created_utc
    return post


@pytest.mark.asyncio
async def test_fetch_builds_permalink_from_real_bdata_post_shape(monkeypatch):
    source = load_source()

    async def fake_create_subprocess_exec(*_args, **_kwargs):
        return FakeProcess(
            json.dumps(
                [
                    {
                        "post_id": "t3_1v5rwld",
                        "url": "https://www.reddit.com/r/ClaudeAI/rising/",
                        "title": "Test post",
                        "description": "desc",
                        "num_upvotes": 100,
                        "num_comments": 10,
                        "community_name": "ClaudeAI",
                    }
                ]
            )
        )

    monkeypatch.setattr(
        source.asyncio, "create_subprocess_exec", fake_create_subprocess_exec
    )

    candidates = await source._fetch_one_subreddit(
        "https://www.reddit.com/r/ClaudeAI/rising/"
    )

    assert len(candidates) == 1
    assert candidates[0]["url"] == (
        "https://www.reddit.com/r/ClaudeAI/comments/1v5rwld"
    )
    assert candidates[0]["evidence_url"] == candidates[0]["url"]
    assert "/rising" not in candidates[0]["url"]


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "post_id, community_name",
    [(None, "ClaudeAI"), ("t3_1v5rwld", "")],
)
async def test_fetch_skips_posts_without_canonical_identity(
    monkeypatch, post_id, community_name
):
    source = load_source()

    async def fake_create_subprocess_exec(*_args, **_kwargs):
        return FakeProcess(
            json.dumps(
                [
                    _make_post(
                        "https://www.reddit.com/r/ClaudeAI/rising/",
                        community_name=community_name,
                        post_id=post_id,
                    )
                ]
            )
        )

    monkeypatch.setattr(
        source.asyncio, "create_subprocess_exec", fake_create_subprocess_exec
    )

    candidates = await source._fetch_one_subreddit(
        "https://www.reddit.com/r/ClaudeAI/rising/"
    )

    assert candidates == []


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


class FakeSnapshots:
    """Mock reddit_post_snapshots collection."""

    def __init__(self):
        self._docs = []

    async def insert_one(self, doc):
        self._docs.append(doc)
        return type("R", (), {"inserted_id": "fake"})()

    async def find_one(self, query, *_args, **_kwargs):
        url = query.get("url", "")
        docs = [d for d in self._docs if d.get("url") == url]
        if not docs:
            return None
        return max(
            docs,
            key=lambda d: d.get(
                "capturedAt", datetime.min.replace(tzinfo=timezone.utc)
            ),
        )


class FakeDb:
    def __init__(self, url_to_post: dict | None = None):
        self.posts = FakePosts(url_to_post)
        self.reddit_post_snapshots = FakeSnapshots()


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
    recent_permalink = "https://www.reddit.com/r/LocalLLaMA/comments/new"
    db = FakeDb(
        url_to_post={
            recent_permalink: {"postedAt": recent_posted_at},
        }
    )

    candidates = await source.fetch_reddit_candidates(max_results=10, db=db)

    urls = [c["url"] for c in candidates]
    assert "https://www.reddit.com/r/LocalLLaMA/comments/old" in urls
    assert recent_permalink not in urls


@pytest.mark.asyncio
async def test_cooldown_keeps_threads_posted_7_or_more_days_ago(monkeypatch):
    source = load_source()
    monkeypatch.setattr(source.shutil, "which", lambda _: "/usr/local/bin/bdata")

    url = "https://www.reddit.com/r/MachineLearning/comments/abc/kept_thread/"

    async def fake_create_subprocess_exec(*_args, **_kwargs):
        return FakeProcess(
            json.dumps([_make_post(url, upvotes=150, community_name="MachineLearning")])
        )

    monkeypatch.setattr(
        source.asyncio, "create_subprocess_exec", fake_create_subprocess_exec
    )

    # Posted exactly 7 days ago → should be kept (>= COOLDOWN_DAYS).
    posted_at = datetime.now(timezone.utc) - timedelta(days=7)
    permalink = "https://www.reddit.com/r/MachineLearning/comments/abc"
    db = FakeDb(url_to_post={permalink: {"postedAt": posted_at}})

    candidates = await source.fetch_reddit_candidates(max_results=10, db=db)
    assert len(candidates) == 1
    assert candidates[0]["url"] == permalink


@pytest.mark.asyncio
async def test_cooldown_keeps_never_posted_threads(monkeypatch):
    source = load_source()
    monkeypatch.setattr(source.shutil, "which", lambda _: "/usr/local/bin/bdata")

    url = "https://www.reddit.com/r/singularity/comments/xyz/never_posted/"

    async def fake_create_subprocess_exec(*_args, **_kwargs):
        return FakeProcess(
            json.dumps([_make_post(url, upvotes=120, community_name="singularity")])
        )

    monkeypatch.setattr(
        source.asyncio, "create_subprocess_exec", fake_create_subprocess_exec
    )

    db = FakeDb()  # no posts at all

    candidates = await source.fetch_reddit_candidates(max_results=10, db=db)
    assert len(candidates) == 1
    assert candidates[0]["url"] == ("https://www.reddit.com/r/singularity/comments/xyz")


@pytest.mark.asyncio
async def test_engagement_velocity_is_computed_and_sorted(monkeypatch):
    source = load_source()
    monkeypatch.setattr(source.shutil, "which", lambda _: "/usr/local/bin/bdata")

    # Both posts pass the gate (enough upvotes/comments, fresh enough for heat >= 40).
    now = datetime.now(timezone.utc)
    post_a = _make_post(
        "https://www.reddit.com/r/LocalLLaMA/comments/a/thread_a/",
        upvotes=100,
        comments=50,
        created_utc=(now - timedelta(hours=1)).timestamp(),
        post_id="t3_a",
    )
    post_b = _make_post(
        "https://www.reddit.com/r/LocalLLaMA/comments/b/thread_b/",
        upvotes=500,
        comments=50,
        created_utc=(now - timedelta(hours=2)).timestamp(),
        post_id="t3_b",
    )

    async def fake_create_subprocess_exec(*_args, **_kwargs):
        return FakeProcess(json.dumps([post_a, post_b]))

    monkeypatch.setattr(
        source.asyncio, "create_subprocess_exec", fake_create_subprocess_exec
    )

    db = FakeDb()
    candidates = await source.fetch_reddit_candidates(max_results=10, db=db)

    assert len(candidates) == 2
    # engagement_velocity is still computed (backward-compat field).
    assert "engagement_velocity" in candidates[0]
    # Candidates are sorted by heat_score (highest first).
    assert "heat_score" in candidates[0]
    assert candidates[0]["heat_score"] >= candidates[1]["heat_score"]


@pytest.mark.asyncio
async def test_engagement_velocity_falls_back_to_1_hour(monkeypatch):
    source = load_source()
    monkeypatch.setattr(source.shutil, "which", lambda _: "/usr/local/bin/bdata")

    url = "https://www.reddit.com/r/OpenAI/comments/fb/fallback/"
    # No created_utc field → age defaults to 1 hour → velocity = upvotes / 1
    post = _make_post(url, upvotes=42, created_utc=None, post_id="t3_fb")

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


# ---------------------------------------------------------------------------
# Shared heat score attachment (ticket 02 — expand: heat_score beside fields)
# ---------------------------------------------------------------------------


def test_attach_reddit_heat_adds_heat_score_field():
    """Each candidate gets a heat_score (int, 0-100) beside existing fields."""
    source = load_source()
    candidates = [
        {
            "url": "https://www.reddit.com/r/LocalLLaMA/comments/a",
            "title": "A",
            "subreddit": "LocalLLaMA",
            "num_upvotes": 5000,
            "num_comments": 500,
            "age_hours": 6.0,
        },
        {
            "url": "https://www.reddit.com/r/LocalLLaMA/comments/b",
            "title": "B",
            "subreddit": "LocalLLaMA",
            "num_upvotes": 50,
            "num_comments": 5,
            "age_hours": 6.0,
        },
    ]
    result = source.attach_reddit_heat(candidates)
    for c in result:
        assert "heat_score" in c
        assert isinstance(c["heat_score"], int)
        assert 0 <= c["heat_score"] <= 100
        assert "outperform_ratio" in c
        assert "baseline_confidence" in c
    a = next(c for c in result if c["url"].endswith("/a"))
    b = next(c for c in result if c["url"].endswith("/b"))
    assert a["heat_score"] > b["heat_score"], (
        f"5K upvotes ({a['heat_score']}) should outrank 50 ({b['heat_score']})"
    )


def test_attach_reddit_heat_empty_list_returns_empty():
    source = load_source()
    assert source.attach_reddit_heat([]) == []


def test_attach_reddit_heat_preserves_existing_fields():
    """Expand: heat_score is added beside existing fields, not replacing them."""
    source = load_source()
    candidates = [
        {
            "url": "https://www.reddit.com/r/LocalLLaMA/comments/a",
            "title": "A",
            "subreddit": "LocalLLaMA",
            "num_upvotes": 5000,
            "num_comments": 500,
            "age_hours": 6.0,
            "visibility_score": 80.0,
            "engagement_velocity": 833.3,
        },
    ]
    result = source.attach_reddit_heat(candidates)
    assert result[0]["visibility_score"] == 80.0
    assert result[0]["engagement_velocity"] == 833.3
    assert "heat_score" in result[0]


def test_attach_reddit_heat_missing_age_hours_is_handled():
    """A candidate without age_hours must not crash (velocity stays 0)."""
    source = load_source()
    candidates = [
        {
            "url": "https://www.reddit.com/r/LocalLLaMA/comments/x",
            "title": "X",
            "subreddit": "LocalLLaMA",
            "num_upvotes": 5000,
            "num_comments": 500,
        },
    ]
    result = source.attach_reddit_heat(candidates)
    assert isinstance(result[0]["heat_score"], int)


# ---------------------------------------------------------------------------
# Regression: cooldown query bug (fix A — same-content-every-day root cause)
# ---------------------------------------------------------------------------


async def test_last_posted_days_uses_most_recent_post_not_oldest(db):
    """_last_posted_days must measure from the MOST RECENT post, not the oldest.

    Regression: the old find_one had no sort=[("postedAt", -1)], so it
    returned the oldest post's age. Once the oldest post was >7 days old, the
    URL was permanently eligible and re-posted every single day.
    """
    from _shared.mongo import _get_db

    source = load_source()
    url = "https://www.reddit.com/r/LocalLLaMA/comments/regression_sort123"
    normalized = source._normalize_reddit_url(url)
    # An OLD post (30 days ago) and a RECENT post (2 days ago) for the same URL.
    db.posts.insert_one(
        {
            "project": {"url": normalized},
            "postedAt": datetime.now(timezone.utc) - timedelta(days=30),
        }
    )
    db.posts.insert_one(
        {
            "project": {"url": normalized},
            "postedAt": datetime.now(timezone.utc) - timedelta(days=2),
        }
    )
    test_db = _get_db()
    days = await source._last_posted_days(test_db, url)
    # Must measure from the MOST RECENT (2 days), not the oldest (30 days).
    assert days <= 3, f"Expected ~2 (most recent), got {days} — oldest-post bug"
    assert days < source.COOLDOWN_DAYS, f"Should be within cooldown, got {days}"
    # Cleanup
    db.posts.delete_many({"project.url": normalized})


@pytest.mark.asyncio
async def test_gate_rejects_below_noise_floor(monkeypatch):
    """The publish gate rejects candidates below the noise floor (>=20 upvotes, >=1 comment).

    A post with 15 upvotes passes the fetch's 10-upvote filter but fails the
    gate's noise floor — the gate, not the fetch, is the quality filter.
    """
    source = load_source()
    monkeypatch.setattr(source.shutil, "which", lambda _: "/usr/local/bin/bdata")

    hot_url = "https://www.reddit.com/r/LocalLLaMA/comments/hot/thread_hot/"
    noisy_url = "https://www.reddit.com/r/LocalLLaMA/comments/noisy/thread_noisy/"

    async def fake_create_subprocess_exec(*_args, **_kwargs):
        return FakeProcess(
            json.dumps(
                [
                    _make_post(
                        hot_url,
                        upvotes=5000,
                        comments=500,
                        title="Hot",
                    ),
                    _make_post(
                        noisy_url,
                        upvotes=15,
                        comments=0,
                        title="Noisy",
                    ),
                ]
            )
        )

    monkeypatch.setattr(
        source.asyncio, "create_subprocess_exec", fake_create_subprocess_exec
    )

    db = FakeDb()  # no prior posts → both pass cooldown
    candidates = await source.fetch_reddit_candidates(max_results=10, db=db)
    urls = [c["url"] for c in candidates]
    assert "https://www.reddit.com/r/LocalLLaMA/comments/hot" in urls
    assert "https://www.reddit.com/r/LocalLLaMA/comments/noisy" not in urls


@pytest.mark.asyncio
async def test_gate_rejects_below_heat_threshold(monkeypatch):
    """The publish gate rejects candidates below the heat threshold (40)."""
    source = load_source()
    monkeypatch.setattr(source.shutil, "which", lambda _: "/usr/local/bin/bdata")

    # 20 upvotes, 1 comment, age=1h → just above noise floor but low heat.
    lukewarm_url = "https://www.reddit.com/r/LocalLLaMA/comments/lw/thread_lw/"

    async def fake_create_subprocess_exec(*_args, **_kwargs):
        return FakeProcess(
            json.dumps(
                [
                    _make_post(
                        lukewarm_url,
                        upvotes=20,
                        comments=1,
                        title="Lukewarm",
                        post_id="t3_lw",
                    )
                ]
            )
        )

    monkeypatch.setattr(
        source.asyncio, "create_subprocess_exec", fake_create_subprocess_exec
    )

    db = FakeDb()
    candidates = await source.fetch_reddit_candidates(max_results=10, db=db)
    urls = [c["url"] for c in candidates]
    # 20 upvotes/1h → velocity=8, recency≈18, depth=min(10,int(1/20*50))=2, novelty=10
    # total ≈ 38 < 40 → gate rejects (below threshold).
    assert source._normalize_reddit_url(lukewarm_url) not in urls
