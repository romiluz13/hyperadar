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
async def test_breakout_returns_empty_when_no_history(db, monkeypatch):
    """No signals → empty list, no errors."""
    from hidden_gems import source

    async def fake_track(db):
        return 0

    monkeypatch.setattr(source, "track_daily_snapshots", fake_track)

    async_db = mongo._get_db()
    results = await source.fetch_breakout_candidates(async_db)
    assert results == []
