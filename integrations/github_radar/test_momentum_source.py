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

    # Accelerating repo: 14 days, stars growing from 200→256 (4/day)
    accel_stars = [200 + i * 4 for i in range(14)]
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
    healthy_stars = [200 + i * 4 for i in range(14)]
    healthy_snaps = _snapshots(healthy_stars, forks=100)
    for s in healthy_snaps:
        s["projectId"] = healthy_url

    # Suspicious repo: 14 days growth, bad fork ratio (forks=1, stars=256, ratio ~0.004)
    suspicious_stars = [200 + i * 4 for i in range(14)]
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
