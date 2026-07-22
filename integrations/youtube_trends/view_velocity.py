"""YouTube view velocity tracking — daily view snapshots + 7-day velocity.

Stores daily view count snapshots in MongoDB (youtube_view_snapshots collection)
and computes view velocity = views gained in the last 7 days. Only videos with
velocity > 0 (or first discovery with no prior history) are published.

Channel-relative velocity normalizes by channel subscriber count so a 5K-view
video from a 1K-subscriber channel scores higher than a 5K-view video from a
100K-subscriber channel.
"""

from datetime import datetime, timedelta, timezone

from _shared.mongo import _get_db

VELOCITY_WINDOW_DAYS = 7
# Default subscriber count when channel data is unavailable.
_DEFAULT_CHANNEL_SUBSCRIBERS = 10000


def _to_utc(dt: datetime) -> datetime:
    """Ensure a datetime is timezone-aware UTC (MongoDB returns naive UTC)."""
    if dt.tzinfo is None:
        return dt.replace(tzinfo=timezone.utc)
    return dt


def compute_view_velocity(current_views: int, snapshots: list[dict]) -> int:
    """Compute views gained in the last 7 days from snapshot history.

    Uses the most recent snapshot that is ≥7 days old as the baseline.
    Returns 0 if no snapshot ≥7 days old exists.
    """
    if not snapshots:
        return 0
    now = datetime.now(timezone.utc)
    cutoff = now - timedelta(days=VELOCITY_WINDOW_DAYS)
    eligible = [s for s in snapshots if _to_utc(s.get("capturedAt", now)) <= cutoff]
    if not eligible:
        return 0
    eligible.sort(key=lambda s: _to_utc(s["capturedAt"]), reverse=True)
    baseline = eligible[0].get("viewCount", 0)
    return max(0, current_views - baseline)


def channel_relative_velocity(
    view_velocity: int,
    channel_subscribers: int,
) -> float:
    """Normalize view velocity by channel subscriber count.

    A video gaining 5K views/week on a 1K-subscriber channel = 5.0 (breakout).
    A video gaining 5K views/week on a 100K-subscriber channel = 0.05 (nothing).

    Falls back to a default subscriber count when channel data is unavailable
    or zero — avoids division by zero while giving a reasonable baseline.
    """
    if view_velocity <= 0:
        return 0.0
    subs = (
        channel_subscribers if channel_subscribers > 0 else _DEFAULT_CHANNEL_SUBSCRIBERS
    )
    return view_velocity / subs


async def save_view_snapshot(url: str, view_count: int) -> None:
    """Save a daily view count snapshot for a YouTube video."""
    db = _get_db()
    await db.youtube_view_snapshots.insert_one(
        {
            "url": url,
            "viewCount": view_count,
            "capturedAt": datetime.now(timezone.utc),
        }
    )


async def get_view_velocity(url: str) -> int:
    """Read view velocity for a YouTube video from stored snapshots.

    Returns views gained in the last 7 days, or 0 if insufficient history.
    """
    db = _get_db()
    cursor = (
        db.youtube_view_snapshots.find({"url": url}).sort("capturedAt", -1).limit(100)
    )
    snapshots = await cursor.to_list(length=100)
    if not snapshots:
        return 0
    current_views = snapshots[0].get("viewCount", 0)
    return compute_view_velocity(current_views, snapshots)
