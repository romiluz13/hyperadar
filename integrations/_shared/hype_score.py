"""Hype score: how hard a repo is breaking out this week (CONTEXT.md, Hype score).

It orders the daily digest's repo candidates (ADR 0007). It applies to repos
only: other kinds rank by their own signal (ADR 0008).
"""

import math
from datetime import datetime, timedelta

HYPE_NEW_REPO_AGE = timedelta(days=90)
HYPE_NEW_REPO_BOOST = 1.15
# A repo this small when the week began has no base to break out of, so
# relative growth is measured against at least this many stars.
_RELATIVE_GROWTH_MIN_BASE = 100


def _log_count(count: int | None) -> float:
    # Star history reports net per-day deltas, so a day can lose stars.
    return math.log10(1 + max(0, count or 0))


def hype_score(
    *,
    stars_this_week: int,
    stars_today: int,
    total_stars: int | None,
    hn_points: int | None,
    created_at: datetime | None,
    now: datetime,
) -> float:
    """The hype score, ×1.15 for a repo created under 90 days before ``now``.

    ``0.55·log10(1+starsThisWeek)
    + 0.20·min(1, starsThisWeek / max(total − starsThisWeek, 100))
    + 0.15·log10(1+starsToday) + 0.10·log10(1+hnPoints)``. Unknown ``total_stars`` earns no relative growth, and an unknown
    ``created_at`` no boost.
    """
    relative = 0.0
    if total_stars is not None:
        base = max(total_stars - stars_this_week, _RELATIVE_GROWTH_MIN_BASE)
        relative = min(1.0, stars_this_week / base)
    score = (
        0.55 * _log_count(stars_this_week)
        + 0.20 * relative
        + 0.15 * _log_count(stars_today)
        + 0.10 * _log_count(hn_points)
    )
    if created_at is not None and now - created_at < HYPE_NEW_REPO_AGE:
        score *= HYPE_NEW_REPO_BOOST
    return score
