"""Tests for YouTube view velocity tracking.

Seam: the public functions in view_velocity.py + source.fetch_youtube_candidates_with_velocity.
Pure-function tests for compute_view_velocity; MongoDB-backed tests for snapshot
storage and retrieval; integration test for the velocity-filtered discovery path.
"""

from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, patch

import pytest

from source import attach_youtube_heat, fetch_youtube_candidates_with_velocity
from view_velocity import (
    channel_relative_velocity,
    compute_view_velocity,
    get_view_velocity,
    save_view_snapshot,
)


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
    """Videos with low heat (flat views) are excluded by the publish gate."""
    now = datetime.now(timezone.utc)
    two_days_ago = (now - timedelta(days=2)).strftime("%Y%m%d")

    raw_candidates = [
        {
            "url": "https://www.youtube.com/watch?v=growing",
            "title": "Growing Video",
            "kind": "video",
            "description": "By Test · 5000 views",
            "topics": ["youtube", "ai", "video", "test"],
            "channel": "Test",
            "viewCount": 5000,
            "uploadDate": two_days_ago,
            "channel_subscribers": 1000,
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
            "uploadDate": two_days_ago,
            "channel_subscribers": 1000,
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
    """First discovery (no prior snapshots) should be included if it passes the gate."""
    now = datetime.now(timezone.utc)
    two_days_ago = (now - timedelta(days=2)).strftime("%Y%m%d")
    raw_candidates = [
        {
            "url": "https://www.youtube.com/watch?v=newvid",
            "title": "New Video",
            "kind": "video",
            "description": "By Test · 5000 views",
            "topics": ["youtube", "ai", "video", "test"],
            "channel": "Test",
            "viewCount": 5000,
            "uploadDate": two_days_ago,
            "channel_subscribers": 1000,
        },
    ]

    with patch(
        "source.fetch_youtube_candidates",
        new_callable=AsyncMock,
        return_value=raw_candidates,
    ):
        result = await fetch_youtube_candidates_with_velocity(max_results=10)

    # First discovery should pass through (passes the gate)
    assert len(result) == 1
    assert result[0]["url"] == "https://www.youtube.com/watch?v=newvid"
    # Snapshot should be saved
    docs = list(db.youtube_view_snapshots.find({"url": raw_candidates[0]["url"]}))
    assert len(docs) == 1
    assert docs[0]["viewCount"] == 5000


# ---------------------------------------------------------------------------
# Channel-relative velocity
# ---------------------------------------------------------------------------


def test_channel_relative_velocity_normalizes_by_subscribers():
    """5K views/week on 1K subs = 5.0 (breakout)."""
    assert channel_relative_velocity(5000, 1000) == 5.0


def test_channel_relative_velocity_small_channel_scores_higher():
    """Same velocity on smaller channel = higher relative score."""
    small = channel_relative_velocity(5000, 1000)
    large = channel_relative_velocity(5000, 100000)
    assert small > large


def test_channel_relative_velocity_zero_for_zero_velocity():
    """No velocity → 0.0."""
    assert channel_relative_velocity(0, 1000) == 0.0


def test_channel_relative_velocity_falls_back_for_zero_subs():
    """Zero subscribers → use default (10000), don't divide by zero."""
    result = channel_relative_velocity(5000, 0)
    assert result == 0.5  # 5000 / 10000


# ---------------------------------------------------------------------------
# Shared heat score attachment (ticket 01 — expand: heat_score beside fields)
# ---------------------------------------------------------------------------


def _now_two_days_ago() -> tuple[datetime, str]:
    now = datetime.now(timezone.utc)
    return now, (now - timedelta(days=2)).strftime("%Y%m%d")


def test_attach_youtube_heat_adds_heat_score_field():
    """Each candidate gets a heat_score (int, 0-100) beside existing fields."""
    now, two_days_ago = _now_two_days_ago()
    candidates = [
        {
            "url": "https://www.youtube.com/watch?v=a",
            "title": "A",
            "channel": "Ch",
            "viewCount": 50000,
            "uploadDate": two_days_ago,
            "channel_subscribers": 1000,
        },
        {
            "url": "https://www.youtube.com/watch?v=b",
            "title": "B",
            "channel": "Ch",
            "viewCount": 1000,
            "uploadDate": two_days_ago,
            "channel_subscribers": 1000,
        },
    ]
    result = attach_youtube_heat(candidates, now=now)
    for c in result:
        assert "heat_score" in c
        assert isinstance(c["heat_score"], int)
        assert 0 <= c["heat_score"] <= 100
        assert "outperform_ratio" in c
        assert "baseline_confidence" in c
    # The high-view video outscored the low-view one (channel-relative velocity).
    a = next(c for c in result if c["url"].endswith("a"))
    b = next(c for c in result if c["url"].endswith("b"))
    assert a["heat_score"] > b["heat_score"], (
        f"50K views ({a['heat_score']}) should outrank 1K ({b['heat_score']})"
    )


def test_attach_youtube_heat_empty_list_returns_empty():
    assert attach_youtube_heat([]) == []


def test_attach_youtube_heat_preserves_existing_fields():
    """Expand: heat_score is added beside existing fields, not replacing them."""
    now, two_days_ago = _now_two_days_ago()
    candidates = [
        {
            "url": "https://www.youtube.com/watch?v=a",
            "title": "A",
            "channel": "Ch",
            "viewCount": 5000,
            "uploadDate": two_days_ago,
            "channel_subscribers": 1000,
            "viewVelocity": 1000,
            "channelRelativeVelocity": 1.0,
        },
    ]
    result = attach_youtube_heat(candidates, now=now)
    assert result[0]["viewVelocity"] == 1000
    assert result[0]["channelRelativeVelocity"] == 1.0
    assert "heat_score" in result[0]


def test_attach_youtube_heat_missing_upload_date_is_handled():
    """A candidate without uploadDate must not crash (age falls back to 0)."""
    candidates = [
        {
            "url": "https://www.youtube.com/watch?v=x",
            "title": "X",
            "channel": "Ch",
            "viewCount": 5000,
            "channel_subscribers": 1000,
        },
    ]
    result = attach_youtube_heat(candidates)
    assert isinstance(result[0]["heat_score"], int)


async def test_fetch_youtube_candidates_with_velocity_attaches_heat_score(db):
    """End-to-end: the velocity-filtered fetch returns heat_score on each video."""
    now = datetime.now(timezone.utc)
    two_days_ago = (now - timedelta(days=2)).strftime("%Y%m%d")
    raw_candidates = [
        {
            "url": "https://www.youtube.com/watch?v=growing",
            "title": "Growing Video",
            "kind": "video",
            "description": "By Test · 5000 views",
            "topics": ["youtube", "ai", "video", "test"],
            "channel": "Test",
            "viewCount": 5000,
            "uploadDate": two_days_ago,
            "channel_subscribers": 1000,
        },
    ]
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
    assert len(result) == 1
    assert "heat_score" in result[0]
    assert isinstance(result[0]["heat_score"], int)


# ---------------------------------------------------------------------------
# Fix B: day-1 surfacing (remove the 7-day velocity drop)
# ---------------------------------------------------------------------------


async def test_fetch_surfaces_day1_video_with_prior_snapshots(db):
    """A 2-day-old video with prior snapshots is surfaced, not dropped by the old 7-day gate.

    The old fetch dropped any video with velocity=0 and prior snapshots (i.e.,
    not enough 7-day history). The day-1 heat scorer (views/hour/subscriber)
    works from the first fetch, so the fetch must not drop these videos.
    """
    now = datetime.now(timezone.utc)
    two_days_ago = (now - timedelta(days=2)).strftime("%Y%m%d")
    raw_candidates = [
        {
            "url": "https://www.youtube.com/watch?v=day1surf",
            "title": "Day 1 Video",
            "kind": "video",
            "description": "By Test · 5000 views",
            "topics": ["youtube", "ai", "video", "test"],
            "channel": "Test",
            "viewCount": 5000,
            "uploadDate": two_days_ago,
            "channel_subscribers": 1000,
        },
    ]
    # Prior snapshot from 1 day ago → 7-day velocity is 0 (no 7-day-old baseline).
    # The OLD code dropped this; the NEW code surfaces it (day-1 heat applies).
    db.youtube_view_snapshots.insert_one(
        {
            "url": "https://www.youtube.com/watch?v=day1surf",
            "viewCount": 4000,
            "capturedAt": now - timedelta(days=1),
        }
    )
    with patch(
        "source.fetch_youtube_candidates",
        new_callable=AsyncMock,
        return_value=raw_candidates,
    ):
        result = await fetch_youtube_candidates_with_velocity(max_results=10)
    urls = [c["url"] for c in result]
    assert "https://www.youtube.com/watch?v=day1surf" in urls
    assert "heat_score" in result[0]
    # Cleanup
    db.youtube_view_snapshots.delete_many(
        {"url": "https://www.youtube.com/watch?v=day1surf"}
    )


# ---------------------------------------------------------------------------
# Fix C: YouTube cooldown (the re-post-every-run bug fix)
# ---------------------------------------------------------------------------


async def test_fetch_youtube_skips_recently_published(db):
    """A video published within the 14-day YouTube cooldown is not surfaced."""
    now = datetime.now(timezone.utc)
    two_days_ago = (now - timedelta(days=2)).strftime("%Y%m%d")
    video_url = "https://www.youtube.com/watch?v=cooldown"
    raw_candidates = [
        {
            "url": video_url,
            "title": "Recently Published",
            "kind": "video",
            "description": "By Test · 5000 views",
            "topics": ["youtube", "ai", "video", "test"],
            "channel": "Test",
            "viewCount": 5000,
            "uploadDate": two_days_ago,
            "channel_subscribers": 1000,
        },
    ]
    # Insert a post for this URL posted 2 days ago (within the 14-day cooldown).
    db.posts.insert_one(
        {
            "project": {"url": video_url},
            "postedAt": now - timedelta(days=2),
        }
    )
    with patch(
        "source.fetch_youtube_candidates",
        new_callable=AsyncMock,
        return_value=raw_candidates,
    ):
        result = await fetch_youtube_candidates_with_velocity(max_results=10)
    urls = [c["url"] for c in result]
    assert video_url not in urls
    # Cleanup
    db.posts.delete_many({"project.url": video_url})
