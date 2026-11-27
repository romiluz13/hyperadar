"""The farm check: signs that a repo's stars were bought (ADR 0007).

Event data that could show who starred is gone (ADR 0007), so a fork and
burst check does that job. It is the only fork filter @github-radar applies.
"""

from datetime import date, datetime, timedelta

FORKLESS_STARS_THIS_WEEK = 300
BURST_SHARE = 0.80
BURST_RULE_MIN_AGE = timedelta(days=30)
HN_BURST_EXCUSE_POINTS = 100


def farm_reason(
    *,
    forks: int,
    stars_this_week: int,
    days: list[tuple[date, int]],
    created_at: datetime,
    now: datetime,
    hn_points_by_day: dict[date, int],
) -> str | None:
    """Why a repo looks farmed, or None when it passes.

    ``days`` is star history's last 7 complete days; ``hn_points_by_day`` holds
    the best matching HN story's points for each UTC day that has one.
    """
    if forks == 0 and stars_this_week >= FORKLESS_STARS_THIS_WEEK:
        return f"no forks with {stars_this_week} stars this week"
    if now - created_at <= BURST_RULE_MIN_AGE or stars_this_week <= 0:
        return None
    burst_day, burst_stars = max(days, key=lambda day: day[1])
    if burst_stars <= BURST_SHARE * stars_this_week:
        return None
    if hn_points_by_day.get(burst_day, 0) >= HN_BURST_EXCUSE_POINTS:
        return None
    return (
        f"one day ({burst_day}) holds {burst_stars} of {stars_this_week} stars "
        f"this week, with no {HN_BURST_EXCUSE_POINTS}-point HN story that day"
    )
