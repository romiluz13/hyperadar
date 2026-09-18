"""Tests for fetch_breakout_candidates and hidden_gem_momentum_copy."""

from datetime import datetime, timedelta, timezone

import pytest

from _shared.evidence_copy import hidden_gem_momentum_copy
from _shared import mongo


def _snapshots(stars_sequence, forks=None):
    """Build N daily signal snapshots from a star-count sequence."""
    base = datetime.now(timezone.utc).replace(
        hour=0, minute=0, second=0, microsecond=0
    ) - timedelta(days=len(stars_sequence) - 1)
    snapshots = []
    for i, stars in enumerate(stars_sequence):
        snapshots.append(
            {
                "capturedAt": base + timedelta(days=i),
                "projectId": "https://github.com/test/breakout-repo",
                "postId": "",
                "github_stars": stars,
                "github_forks": forks if forks is not None else max(1, stars // 10),
            }
        )
    return snapshots


# ─── hidden_gem_momentum_copy ───


def test_momentum_copy_includes_score():
    text = hidden_gem_momentum_copy(78, 12, 3)
    assert "78" in text
    assert "Momentum" in text or "momentum" in text


def test_momentum_copy_includes_velocity():
    text = hidden_gem_momentum_copy(78, 12, 3)
    assert "12" in text
    assert "star" in text.lower()


def test_momentum_copy_includes_acceleration():
    text = hidden_gem_momentum_copy(78, 12, 5)
    assert "accelerat" in text.lower() or "breakout" in text.lower()


# ─── fetch_breakout_candidates ───


@pytest.mark.asyncio
async def test_breakout_returns_only_passing_repos(db, monkeypatch):
    """A repo with accelerating growth passes the gate; a flat repo does not."""
    from hidden_gems import source

    # Mock track_daily_snapshots — no network during tests.
    async def fake_track(db):
        return 0

    monkeypatch.setattr(source, "track_daily_snapshots", fake_track)

    # No HN story → no engagement boost → the raw gate score is asserted below.
    async def fake_engagement(repo_url, client=None):
        return None

    monkeypatch.setattr(source, "fetch_hn_engagement", fake_engagement)

    async_db = mongo._get_db()

    # Accelerating repo: 14 days, stars growing from 20→72 (4/day)
    accel_stars = [20 + i * 4 for i in range(14)]
    accel_snaps = _snapshots(accel_stars, forks=10)
    for s in accel_snaps:
        s["projectId"] = "https://github.com/test/accelerating-repo"

    # Flat repo: 14 days, no growth
    flat_stars = [100] * 14
    flat_snaps = _snapshots(flat_stars, forks=10)
    for s in flat_snaps:
        s["projectId"] = "https://github.com/test/flat-repo"

    # Short-history repo: only 3 days — should be excluded (< 7 days)
    short_snaps = _snapshots([10, 15, 20], forks=2)
    for s in short_snaps:
        s["projectId"] = "https://github.com/test/short-repo"

    all_urls = [
        "https://github.com/test/accelerating-repo",
        "https://github.com/test/flat-repo",
        "https://github.com/test/short-repo",
    ]

    # Seed signals
    db.signals.delete_many({"projectId": {"$in": all_urls}})
    db.posts.delete_many({"project.url": {"$in": all_urls}})
    db.signals.insert_many(accel_snaps + flat_snaps + short_snaps)

    try:
        results = await source.fetch_breakout_candidates(async_db)

        # The accelerating repo should pass; the flat and short repos should not
        result_urls = [r["url"] for r in results]
        assert "https://github.com/test/accelerating-repo" in result_urls
        assert "https://github.com/test/flat-repo" not in result_urls
        assert "https://github.com/test/short-repo" not in result_urls

        # The passing candidate should include momentum score and velocity
        passing = [
            r
            for r in results
            if r["url"] == "https://github.com/test/accelerating-repo"
        ][0]
        assert "momentumScore" in passing
        assert "velocity" in passing
        assert passing["momentumScore"] >= 55
        assert passing["velocity"] > 0
    finally:
        db.signals.delete_many({"projectId": {"$in": all_urls}})
        db.posts.delete_many({"project.url": {"$in": all_urls}})


@pytest.mark.asyncio
async def test_breakout_excludes_recently_published(db, monkeypatch):
    """A repo published <14 days ago should not be returned even if it passes."""
    from hidden_gems import source

    async def fake_track(db):
        return 0

    monkeypatch.setattr(source, "track_daily_snapshots", fake_track)

    # Hermetic even if the gate order changes: no live HN in tests.
    async def fake_engagement(repo_url, client=None):
        return None

    monkeypatch.setattr(source, "fetch_hn_engagement", fake_engagement)

    async_db = mongo._get_db()
    repo_url = "https://github.com/test/recently-published"

    # Accelerating history
    accel_stars = [20 + i * 3 for i in range(14)]
    accel_snaps = _snapshots(accel_stars, forks=10)
    for s in accel_snaps:
        s["projectId"] = repo_url

    # Insert a post from 3 days ago
    recent_post = {
        "agentHandle": "@hidden-gems",
        "body": "test post",
        "postedAt": datetime.now(timezone.utc) - timedelta(days=3),
        "project": {"url": repo_url, "title": "Recent Repo"},
        "portSyncStatus": "synced",
    }

    db.signals.delete_many({"projectId": repo_url})
    db.posts.delete_many({"project.url": repo_url})
    db.signals.insert_many(accel_snaps)
    db.posts.insert_one(recent_post)

    try:
        results = await source.fetch_breakout_candidates(async_db)
        result_urls = [r["url"] for r in results]
        assert repo_url not in result_urls, (
            "Recently published repo should be excluded by cooldown gate"
        )
    finally:
        db.signals.delete_many({"projectId": repo_url})
        db.posts.delete_many({"project.url": repo_url})


@pytest.mark.asyncio
async def test_breakout_cooldown_counts_from_newest_post(db, monkeypatch):
    """THE regression: an OLD post plus a RECENT post excludes the repo.

    The pre-fix unsorted ``find_one`` returned the old post, so recycled repos
    sailed through the cooldown daily.
    """
    from hidden_gems import source

    async def fake_track(db):
        return 0

    monkeypatch.setattr(source, "track_daily_snapshots", fake_track)

    # Hermetic even if the gate order changes: no live HN in tests.
    async def fake_engagement(repo_url, client=None):
        return None

    monkeypatch.setattr(source, "fetch_hn_engagement", fake_engagement)

    async_db = mongo._get_db()
    repo_url = "https://github.com/test/gems-newest-post-cooldown"

    accel_stars = [20 + i * 3 for i in range(14)]
    accel_snaps = _snapshots(accel_stars, forks=10)
    for s in accel_snaps:
        s["projectId"] = repo_url

    db.signals.delete_many({"projectId": repo_url})
    db.posts.delete_many({"project.url": repo_url})
    db.signals.insert_many(accel_snaps)
    db.posts.insert_many(
        [
            {
                "agentHandle": "@hidden-gems",
                "body": "old post",
                "postedAt": datetime.now(timezone.utc) - timedelta(days=45),
                "project": {"url": repo_url, "title": "Recycled Gem"},
                "portSyncStatus": "synced",
            },
            {
                "agentHandle": "@github-radar",
                "body": "recent post",
                "postedAt": datetime.now(timezone.utc) - timedelta(days=1),
                "project": {"url": repo_url, "title": "Recycled Gem"},
                "portSyncStatus": "synced",
            },
        ]
    )

    try:
        results = await source.fetch_breakout_candidates(async_db)
        assert repo_url not in [r["url"] for r in results], (
            "Repo posted yesterday by another agent must be excluded even "
            "though it also has a 45-day-old post"
        )
    finally:
        db.signals.delete_many({"projectId": repo_url})
        db.posts.delete_many({"project.url": repo_url})


@pytest.mark.asyncio
async def test_breakout_returns_empty_when_no_history(db, monkeypatch):
    """No signals → empty list, no errors."""
    from hidden_gems import source

    async def fake_track(db):
        return 0

    monkeypatch.setattr(source, "track_daily_snapshots", fake_track)

    async_db = mongo._get_db()
    results = await source.fetch_breakout_candidates(async_db)
    assert results == []


@pytest.mark.asyncio
async def test_breakout_engagement_boost_applied_post_gate(db, monkeypatch):
    """HN engagement raises the PUBLISHED score, never the gate decision.

    The pool-scaled threshold is computed from raw gate scores; the boost is
    applied after, so a repo cannot gate-crash via HN points — but a repo
    that passed carries engagement-weighted momentum and HN evidence fields.
    """
    from hidden_gems import source

    async def fake_track(db):
        return 0

    monkeypatch.setattr(source, "track_daily_snapshots", fake_track)

    repo_url = "https://github.com/test/engagement-boosted"
    holder = {"engagement": None}

    async def fake_engagement(url, client=None):
        assert url == repo_url
        return holder["engagement"]

    monkeypatch.setattr(source, "fetch_hn_engagement", fake_engagement)

    async_db = mongo._get_db()
    accel_stars = [20 + i * 4 for i in range(14)]
    accel_snaps = _snapshots(accel_stars, forks=10)
    for s in accel_snaps:
        s["projectId"] = repo_url

    db.signals.delete_many({"projectId": repo_url})
    db.posts.delete_many({"project.url": repo_url})
    db.signals.insert_many(accel_snaps)

    try:
        # Pass 1: no HN story — the raw gate score.
        raw = [
            r
            for r in await source.fetch_breakout_candidates(async_db)
            if r["url"] == repo_url
        ]
        assert len(raw) == 1
        base_score = raw[0]["momentumScore"]
        assert "hn_points" not in raw[0]

        # Pass 2: front-page HN story — published score rises by the boost.
        holder["engagement"] = {
            "points": 200,
            "comments": 100,
            "story_url": "https://news.ycombinator.com/item?id=1",
            "story_title": "Show HN: Engagement boosted",
            "top_comment": {"author": "alice", "text": "genuinely useful"},
        }
        boosted = [
            r
            for r in await source.fetch_breakout_candidates(async_db)
            if r["url"] == repo_url
        ]
        assert len(boosted) == 1
        expected = min(100, base_score + 15)  # full points (10) + comments (5)
        assert boosted[0]["momentumScore"] == expected
        assert boosted[0]["hn_points"] == 200
        assert boosted[0]["hn_comments"] == 100
        assert boosted[0]["hn_top_comment"]["author"] == "alice"
    finally:
        db.signals.delete_many({"projectId": repo_url})
        db.posts.delete_many({"project.url": repo_url})
