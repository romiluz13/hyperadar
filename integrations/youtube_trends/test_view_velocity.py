"""Tests for YouTube view velocity tracking.

Seam: the public functions in view_velocity.py + source.fetch_youtube_candidates_with_velocity.
Pure-function tests for compute_view_velocity; MongoDB-backed tests for snapshot
storage and retrieval; integration test for the velocity-filtered discovery path.
"""

from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, patch

import pytest

from view_velocity import (
    compute_view_velocity,
    get_view_velocity,
    save_view_snapshot,
)
from source import fetch_youtube_candidates_with_velocity


# ---------------------------------------------------------------------------
# Pure function: compute_view_velocity
# ---------------------------------------------------------------------------


def test_compute_view_velocity_returns_zero_for_empty_history():
    """No prior snapshots → velocity is 0."""
    assert compute_view_velocity(1000, []) == 0


def test_compute_view_velocity_returns_zero_for_single_snapshot():
    """One snapshot (today) is not enough to measure growth."""
    now = datetime.now(timezone.utc)
    snapshots = [{"viewCount": 900, "capturedAt": now}]
    assert compute_view_velocity(1000, snapshots) == 0


def test_compute_view_velocity_returns_delta_from_7_days_ago():
    """Velocity = current views - views 7 days ago."""
    now = datetime.now(timezone.utc)
    snapshots = [
        {"viewCount": 800, "capturedAt": now - timedelta(days=7)},
        {"viewCount": 900, "capturedAt": now - timedelta(days=3)},
    ]
    assert compute_view_velocity(1000, snapshots) == 200


def test_compute_view_velocity_returns_zero_for_flat_views():
    """No growth → velocity 0."""
    now = datetime.now(timezone.utc)
    snapshots = [
        {"viewCount": 1000, "capturedAt": now - timedelta(days=7)},
    ]
    assert compute_view_velocity(1000, snapshots) == 0


def test_compute_view_velocity_ignores_snapshots_newer_than_7_days_only():
    """Only uses snapshots ≥7 days old as the baseline."""
    now = datetime.now(timezone.utc)
    snapshots = [
        {"viewCount": 950, "capturedAt": now - timedelta(days=6)},
        {"viewCount": 900, "capturedAt": now - timedelta(days=3)},
    ]
    # No snapshot ≥7 days old → velocity 0 (not enough history)
    assert compute_view_velocity(1000, snapshots) == 0


def test_compute_view_velocity_uses_oldest_within_7d_window():
    """When multiple snapshots ≥7 days old exist, uses the most recent one ≤7d."""
    now = datetime.now(timezone.utc)
    snapshots = [
        {"viewCount": 700, "capturedAt": now - timedelta(days=14)},
        {"viewCount": 800, "capturedAt": now - timedelta(days=7)},
    ]
    assert compute_view_velocity(1000, snapshots) == 200


# ---------------------------------------------------------------------------
# MongoDB-backed: save_view_snapshot + get_view_velocity
# ---------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def _clean_snapshots(db):
    """Clear the youtube_view_snapshots collection before each test."""
    db.youtube_view_snapshots.delete_many({})
    yield
    db.youtube_view_snapshots.delete_many({})


async def test_save_view_snapshot_stores_to_mongodb(db):
    url = "https://www.youtube.com/watch?v=abc123"
    await save_view_snapshot(url, 5000)
    docs = list(db.youtube_view_snapshots.find({"url": url}))
    assert len(docs) == 1
    assert docs[0]["viewCount"] == 5000
    assert "capturedAt" in docs[0]


async def test_get_view_velocity_returns_zero_without_history():
    url = "https://www.youtube.com/watch?v=nohistory"
    assert await get_view_velocity(url) == 0


async def test_get_view_velocity_returns_delta_from_7_days_ago(db):
    url = "https://www.youtube.com/watch?v=velvid"
    now = datetime.now(timezone.utc)

    # Insert a snapshot from 8 days ago
    db.youtube_view_snapshots.insert_one(
        {"url": url, "viewCount": 1000, "capturedAt": now - timedelta(days=8)}
    )
    # Insert today's snapshot
    await save_view_snapshot(url, 1500)

    velocity = await get_view_velocity(url)
    assert velocity == 500


async def test_get_view_velocity_returns_zero_when_flat(db):
    url = "https://www.youtube.com/watch?v=flatvid"
    now = datetime.now(timezone.utc)

    db.youtube_view_snapshots.insert_one(
        {"url": url, "viewCount": 2000, "capturedAt": now - timedelta(days=10)}
    )
    await save_view_snapshot(url, 2000)

    velocity = await get_view_velocity(url)
    assert velocity == 0


async def test_get_view_velocity_ignores_recent_only_snapshots(db):
    """Snapshots from <7 days ago alone are not enough for a baseline."""
    url = "https://www.youtube.com/watch?v=recentonly"
    now = datetime.now(timezone.utc)

    db.youtube_view_snapshots.insert_one(
        {"url": url, "viewCount": 1000, "capturedAt": now - timedelta(days=3)}
    )
    await save_view_snapshot(url, 1500)

    velocity = await get_view_velocity(url)
    assert velocity == 0


# ---------------------------------------------------------------------------
# Integration: fetch_youtube_candidates_with_velocity
# ---------------------------------------------------------------------------


async def test_fetch_youtube_candidates_with_velocity_filters_zero_velocity(db):
    """Videos with zero velocity (flat views) should be excluded."""
    now = datetime.now(timezone.utc)

    raw_candidates = [
        {
            "url": "https://www.youtube.com/watch?v=growing",
            "title": "Growing Video",
            "kind": "video",
            "description": "By Test · 5000 views",
            "topics": ["youtube", "ai", "video", "test"],
            "channel": "Test",
            "viewCount": 5000,
            "channel_url": "https://www.youtube.com/@Test/videos",
        },
        {
            "url": "https://www.youtube.com/watch?v=flat",
            "title": "Flat Video",
            "kind": "video",
            "description": "By Test · 1000 views",
            "topics": ["youtube", "ai", "video", "test"],
            "channel": "Test",
            "viewCount": 1000,
        },
    ]

    # Flat video has prior snapshot from 10 days ago with same view count
    db.youtube_view_snapshots.insert_one(
        {
            "url": "https://www.youtube.com/watch?v=flat",
            "viewCount": 1000,
            "capturedAt": now - timedelta(days=10),
        }
    )
    # Growing video has prior snapshot from 10 days ago with fewer views
    db.youtube_view_snapshots.insert_one(
        {
            "url": "https://www.youtube.com/watch?v=growing",
            "viewCount": 3000,
            "capturedAt": now - timedelta(days=10),
        }
    )

    with patch(
        "source.fetch_youtube_candidates",
        new_callable=AsyncMock,
        return_value=raw_candidates,
    ):
        result = await fetch_youtube_candidates_with_velocity(max_results=10)

    urls = [c["url"] for c in result]
    assert "https://www.youtube.com/watch?v=growing" in urls
    assert "https://www.youtube.com/watch?v=flat" not in urls
    # Growing video should have viewVelocity set
    growing = next(c for c in result if c["url"].endswith("growing"))
    assert growing["viewVelocity"] == 2000


async def test_fetch_youtube_candidates_with_velocity_includes_first_discovery(db):
    """First discovery (no prior snapshots) should be included with velocity = 0."""
    raw_candidates = [
        {
            "url": "https://www.youtube.com/watch?v=newvid",
            "title": "New Video",
            "kind": "video",
            "description": "By Test · 300 views",
            "topics": ["youtube", "ai", "video", "test"],
            "channel": "Test",
            "viewCount": 300,
        },
    ]

    with patch(
        "source.fetch_youtube_candidates",
        new_callable=AsyncMock,
        return_value=raw_candidates,
    ):
        result = await fetch_youtube_candidates_with_velocity(max_results=10)

    # First discovery should pass through (no prior history to compare)
    assert len(result) == 1
    assert result[0]["url"] == "https://www.youtube.com/watch?v=newvid"
    # Snapshot should be saved
    docs = list(db.youtube_view_snapshots.find({"url": raw_candidates[0]["url"]}))
    assert len(docs) == 1
    assert docs[0]["viewCount"] == 300
