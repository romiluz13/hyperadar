"""Tests for fetch_trending_candidates_with_momentum (shared Momentum Score path)."""

from datetime import datetime, timedelta, timezone

import pytest

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
                "projectId": "https://github.com/test/trending-repo",
                "postId": "",
                "github_stars": stars,
                "github_forks": forks if forks is not None else max(1, stars // 10),
            }
        )
    return snapshots


@pytest.mark.asyncio
async def test_momentum_returns_candidates_with_score(db, monkeypatch):
    """A repo with >=7 days of accelerating growth gets a momentum score."""
    from github_radar import github_source

    async def fake_track(db):
        return 0

    monkeypatch.setattr(github_source, "track_daily_snapshots", fake_track)

    async_db = mongo._get_db()
    repo_url = "https://github.com/test/trending-accelerating"

    # Accelerating repo: 14 days, stars growing from 200→278 (6/day)
    accel_stars = [200 + i * 6 for i in range(14)]
    accel_snaps = _snapshots(accel_stars, forks=30)
    for s in accel_snaps:
        s["projectId"] = repo_url

    db.signals.delete_many({"projectId": repo_url})
    db.signals.insert_many(accel_snaps)

    try:
        results = await github_source.fetch_trending_candidates_with_momentum(async_db)

        result_urls = [r["url"] for r in results]
        assert repo_url in result_urls

        passing = [r for r in results if r["url"] == repo_url][0]
        assert "momentumScore" in passing
        assert "velocity" in passing
        assert "acceleration" in passing
        assert passing["velocity"] > 0
    finally:
        db.signals.delete_many({"projectId": repo_url})


@pytest.mark.asyncio
async def test_momentum_excludes_repos_with_insufficient_history(db, monkeypatch):
    """Repos with <7 days of snapshots are excluded."""
    from github_radar import github_source

    async def fake_track(db):
        return 0

    monkeypatch.setattr(github_source, "track_daily_snapshots", fake_track)

    async_db = mongo._get_db()
    repo_url = "https://github.com/test/trending-short-history"

    # Only 3 days — not enough for >=7 days threshold
    short_snaps = _snapshots([200, 210, 220], forks=30)
    for s in short_snaps:
        s["projectId"] = repo_url

    db.signals.delete_many({"projectId": repo_url})
    db.signals.insert_many(short_snaps)

    try:
        results = await github_source.fetch_trending_candidates_with_momentum(async_db)
        result_urls = [r["url"] for r in results]
        assert repo_url not in result_urls
    finally:
        db.signals.delete_many({"projectId": repo_url})


@pytest.mark.asyncio
async def test_momentum_filters_suspicious_fork_star_ratio(db, monkeypatch):
    """Repos with fork/star ratio < 0.02 are filtered out."""
    from github_radar import github_source

    async def fake_track(db):
        return 0

    monkeypatch.setattr(github_source, "track_daily_snapshots", fake_track)

    async_db = mongo._get_db()
    healthy_url = "https://github.com/test/trending-healthy"
    suspicious_url = "https://github.com/test/trending-suspicious"

    # Healthy repo: 14 days growth, good fork ratio
    healthy_stars = [200 + i * 6 for i in range(14)]
    healthy_snaps = _snapshots(healthy_stars, forks=100)
    for s in healthy_snaps:
        s["projectId"] = healthy_url

    # Suspicious repo: 14 days growth, bad fork ratio (forks=1, stars=278, ratio ~0.004)
    suspicious_stars = [200 + i * 6 for i in range(14)]
    suspicious_snaps = _snapshots(suspicious_stars, forks=1)
    for s in suspicious_snaps:
        s["projectId"] = suspicious_url

    db.signals.delete_many({"projectId": {"$in": [healthy_url, suspicious_url]}})
    db.signals.insert_many(healthy_snaps + suspicious_snaps)

    try:
        results = await github_source.fetch_trending_candidates_with_momentum(async_db)
        result_urls = [r["url"] for r in results]
        assert healthy_url in result_urls
        assert suspicious_url not in result_urls
    finally:
        db.signals.delete_many({"projectId": {"$in": [healthy_url, suspicious_url]}})


@pytest.mark.asyncio
async def test_momentum_returns_empty_when_no_history(db, monkeypatch):
    """No signals → empty list, no errors."""
    from github_radar import github_source

    async def fake_track(db):
        return 0

    monkeypatch.setattr(github_source, "track_daily_snapshots", fake_track)

    async_db = mongo._get_db()
    results = await github_source.fetch_trending_candidates_with_momentum(async_db)
    assert results == []


@pytest.mark.asyncio
async def test_momentum_excludes_non_monotonic_repos(db, monkeypatch):
    """A repo with a dip in star history is excluded even if it passes every
    other gate.  Locks the ``is_monotonic`` wiring: if someone drops the
    ``is_monotonic=`` argument in a refactor the default ``True`` would let
    this repo through.
    """
    from github_radar import github_source

    async def fake_track(db):
        return 0

    monkeypatch.setattr(github_source, "track_daily_snapshots", fake_track)

    async_db = mongo._get_db()
    repo_url = "https://github.com/test/non-monotonic-dip"

    # 14 days, dip at day 4 (215 < 220).  Otherwise strong growth:
    # velocity=50, acceleration=5, fork/star=30/315 ≈ 0.095, score ≈ 68.
    stars = [200, 210, 220, 215, 225, 235, 245, 255, 265, 275, 285, 295, 305, 315]
    snaps = _snapshots(stars, forks=30)
    for s in snaps:
        s["projectId"] = repo_url

    db.signals.delete_many({"projectId": repo_url})
    db.posts.delete_many({"project.url": repo_url})
    db.signals.insert_many(snaps)

    try:
        results = await github_source.fetch_trending_candidates_with_momentum(async_db)
        result_urls = [r["url"] for r in results]
        assert repo_url not in result_urls, (
            "Non-monotonic repo must be excluded by the is_monotonic gate"
        )
    finally:
        db.signals.delete_many({"projectId": repo_url})
        db.posts.delete_many({"project.url": repo_url})


@pytest.mark.asyncio
async def test_momentum_novelty_bonus_uses_prior_post_count(db, monkeypatch):
    """A repo with a prior post gets a lower novelty bonus (+3) than one
    without (+5).  Locks the ``prior_post_count`` wiring: if the arg is
    dropped, ``compute_momentum_score`` defaults to 0 and both repos get +5.
    """
    from github_radar import github_source

    async def fake_track(db):
        return 0

    monkeypatch.setattr(github_source, "track_daily_snapshots", fake_track)

    async_db = mongo._get_db()
    fresh_url = "https://github.com/test/novelty-fresh"
    prior_url = "https://github.com/test/novelty-prior"

    # Identical 14-day monotonic history for both repos.
    stars = [20 + i * 4 for i in range(14)]
    fresh_snaps = _snapshots(stars, forks=10)
    for s in fresh_snaps:
        s["projectId"] = fresh_url
    prior_snaps = _snapshots(stars, forks=10)
    for s in prior_snaps:
        s["projectId"] = prior_url

    # Insert a post for prior_url >14 days ago so the cooldown gate passes
    # but prior_count=1 (novelty bonus +3 instead of +5).
    old_post = {
        "agentHandle": "@github-radar",
        "body": "previous post",
        "postedAt": datetime.now(timezone.utc) - timedelta(days=20),
        "project": {"url": prior_url, "title": "Prior Repo"},
        "portSyncStatus": "synced",
    }

    db.signals.delete_many({"projectId": {"$in": [fresh_url, prior_url]}})
    db.posts.delete_many({"project.url": {"$in": [fresh_url, prior_url]}})
    db.signals.insert_many(fresh_snaps + prior_snaps)
    db.posts.insert_one(old_post)

    try:
        results = await github_source.fetch_trending_candidates_with_momentum(async_db)
        by_url = {r["url"]: r for r in results}

        assert fresh_url in by_url, "Fresh repo should pass the gate"
        assert prior_url in by_url, (
            "Repo with old prior post should still pass the cooldown gate"
        )

        fresh_score = by_url[fresh_url]["momentumScore"]
        prior_score = by_url[prior_url]["momentumScore"]
        assert fresh_score > prior_score, (
            f"Fresh repo ({fresh_score}) should out-score prior-post repo "
            f"({prior_score}) — novelty bonus +5 vs +3"
        )
    finally:
        db.signals.delete_many({"projectId": {"$in": [fresh_url, prior_url]}})
        db.posts.delete_many({"project.url": {"$in": [fresh_url, prior_url]}})
