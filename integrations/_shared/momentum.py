"""Breakout prediction: Momentum Score, fake-star filter, and publishing gate.

The Momentum Score (0–100) is a composite signal that identifies repos
accelerating toward breakout. Formula borrowed from OSS Pulse with
modifications for HypeRadar's MongoDB time-series signals.

Fake-star filtering uses the fork/star ratio threshold from CMU StarScout
research (arXiv:2412.13459): ratio < 0.02 is highly suspicious.
"""

from collections.abc import Sequence

# Weight constants for the Momentum Score
_VELOCITY_WEIGHT = 35
_ACCELERATION_WEIGHT = 25
_GROWTH_WEIGHT = 20
_DEPTH_WEIGHT = 10
_CONSISTENCY_WEIGHT = 10
_VIRAL_BONUS = 10

# Thresholds
# NOTE: 48 is temporary — lower than the original 55 to account for the
# gravity-decay weighted velocity being ~72% of raw for uniform growth.
# Replace with a data-driven threshold (80th percentile of tracked repos)
# once 7+ days of snapshot data exist (see roadmap task #8).
_PUBLISH_SCORE_THRESHOLD = 48
_MIN_FORK_STAR_RATIO = 0.02
_REPUBLISH_COOLDOWN_DAYS = 14
_SUSPICIOUS_FORK_STAR_RATIO = 0.02


def _stars_at(history: Sequence[dict], days_ago: int) -> int:
    """Get the star count from `days_ago` days ago in the history."""
    if len(history) <= days_ago:
        return history[0].get("github_stars", 0) if history else 0
    return history[-(days_ago + 1)].get("github_stars", 0)


def _raw_velocity(history: Sequence[dict], days: int) -> int:
    """Simple cumulative stars gained in the last `days` days (no decay).

    Used by _acceleration for apples-to-apples week-over-week comparison.
    The velocity *score component* uses gravity-decayed _velocity; the
    acceleration *comparison* uses raw deltas to avoid mixing scales.
    """
    if len(history) < 2:
        return 0
    current = history[-1].get("github_stars", 0)
    past = _stars_at(history, days)
    return max(0, current - past)


def _velocity(history: Sequence[dict], days: int) -> int:
    """Stars gained in the last `days` days, with HN-style gravity decay.

    Recent days are weighted higher than older days. Weight for day i
    (1 = most recent) is ``1.0 / (1 + (i - 1) * 0.15)`` so the most recent
    day gets weight 1.0 and decays to ~0.53 for 7 days ago.
    """
    if len(history) < 2:
        return 0
    total = 0
    for i in range(1, min(days, len(history) - 1) + 1):
        daily_gain = history[-i].get("github_stars", 0) - history[-(i + 1)].get(
            "github_stars", 0
        )
        weight = 1.0 / (1 + (i - 1) * 0.15)
        total += max(0, daily_gain) * weight
    return int(total)


def _acceleration(history: Sequence[dict]) -> int:
    """Change in weekly velocity (this week minus last week).

    Uses _raw_velocity for both weeks to ensure apples-to-apples comparison.
    The gravity-decayed _velocity is used for the velocity score component,
    not for the acceleration comparison.
    """
    if len(history) < 14:
        return 0
    this_week = _raw_velocity(history, 7)
    last_week_start = _stars_at(history, 14)
    seven_ago = _stars_at(history, 7)
    last_week = max(0, seven_ago - last_week_start)
    return this_week - last_week


def _relative_growth(history: Sequence[dict]) -> float:
    """New stars as a fraction of total stars (rewards small repos breaking out)."""
    if not history:
        return 0.0
    current = history[-1].get("github_stars", 0)
    if current == 0:
        return 0.0
    gained = _velocity(history, 7)
    return gained / current


def _engagement_depth(history: Sequence[dict]) -> float:
    """Fork/star ratio (higher = deeper engagement, not shallow hype)."""
    if not history:
        return 0.0
    stars = history[-1].get("github_stars", 0)
    forks = history[-1].get("github_forks", 0)
    if stars == 0:
        return 0.0
    return forks / stars


def _consistency(history: Sequence[dict]) -> int:
    """Count of windows (7d, 14d, 30d) with positive velocity (0–3).

    A window only counts if the history is long enough to actually measure it.
    A 5-day repo can't have 14d or 30d consistency.
    """
    if len(history) < 2:
        return 0
    windows = [7, 14, 30]
    count = 0
    for w in windows:
        if len(history) < w + 1:
            break  # not enough history for this window or any larger one
        vel = _velocity(history, w)
        if vel > 0:
            count += 1
    return count


def _viral_bonus(history: Sequence[dict]) -> int:
    """+10 if >5× baseline spike in the last 7 days."""
    if len(history) < 8:
        return 0
    recent = _raw_velocity(history, 7)
    baseline_start = _stars_at(history, 14)
    seven_ago = _stars_at(history, 7)
    baseline = max(1, seven_ago - baseline_start)
    if recent > 5 * baseline:
        return _VIRAL_BONUS
    return 0


def compute_momentum_score(history: Sequence[dict], prior_post_count: int = 0) -> int:
    """Compute a 0–100 Momentum Score from a repo's signal history.

    Each history entry is a daily snapshot with at least:
    - capturedAt: timestamp
    - github_stars: current star count
    - github_forks: current fork count

    Score components:
    - Velocity (35%): 7-day star gain with gravity decay, scaled
    - Acceleration (25%): week-over-week raw velocity change, scaled
    - Relative Growth (20%): new stars / total stars, scaled
    - Engagement Depth (10%): fork/star ratio, scaled
    - Consistency (10%): positive velocity across multiple windows
    + Viral Bonus (+10): if >5× baseline spike
    + Novelty Bonus (+5/+3/0): first/second/3+ publication
    """
    if not history or len(history) < 2:
        return 0

    velocity = _velocity(history, 7)
    acceleration = _acceleration(history)
    relative = _relative_growth(history)
    depth = _engagement_depth(history)
    consistency_count = _consistency(history)

    # Velocity: 0–35 points, scaled by raw count (capped at 50 stars/week)
    velocity_score = (
        min(_VELOCITY_WEIGHT, int((velocity / 50) * _VELOCITY_WEIGHT))
        if velocity > 0
        else 0
    )

    # Acceleration: 0–25 points (positive acceleration only)
    accel_score = (
        min(_ACCELERATION_WEIGHT, max(0, acceleration)) if acceleration > 0 else 0
    )

    # Relative Growth: 0–20 points (higher fraction = more breakout)
    growth_score = min(_GROWTH_WEIGHT, int(relative * 100)) if relative > 0 else 0

    # Engagement Depth: 0–10 points (fork/star ratio, 0.1 = full marks)
    depth_score = min(_DEPTH_WEIGHT, int(depth * 100)) if depth > 0 else 0

    # Consistency: 0–10 points (up to 3 windows, ~3.3 each)
    consistency_score = min(_CONSISTENCY_WEIGHT, consistency_count * 4)

    # Viral bonus
    bonus = _viral_bonus(history)

    # Novelty bonus: +5 for first publication, +3 for second, 0 for 3+
    if prior_post_count == 0:
        novelty_bonus = 5
    elif prior_post_count == 1:
        novelty_bonus = 3
    else:
        novelty_bonus = 0

    total = (
        velocity_score
        + accel_score
        + growth_score
        + depth_score
        + consistency_score
        + bonus
        + novelty_bonus
    )
    return min(100, max(0, total))


def passes_fake_star_filter(stars: int, forks: int) -> bool:
    """Check if a repo passes the fake-star filter.

    Uses fork/star ratio: < 0.02 is highly suspicious (CMU StarScout).
    A repo with stars but zero forks is suspicious.
    A repo with zero stars can't be evaluated — let it through.
    """
    if stars == 0:
        return True
    if forks == 0:
        return False
    return (forks / stars) >= _SUSPICIOUS_FORK_STAR_RATIO


def _is_monotonic_growth(history: Sequence[dict]) -> bool:
    """Check if star count is non-decreasing across the history."""
    if len(history) < 2:
        return True
    for i in range(1, len(history)):
        if history[i].get("github_stars", 0) < history[i - 1].get("github_stars", 0):
            return False
    return True


def should_publish_hidden_gem(
    score: int,
    velocity: int,
    acceleration: int,
    fork_star_ratio: float,
    last_published_days: int,
    is_monotonic: bool = True,
) -> bool:
    """Gate whether a repo should be published as a hidden gem.

    All conditions must be met:
    - score >= threshold (Momentum Score — currently 48, temporary)
    - velocity > 0 (currently growing)
    - acceleration > 0 (growth is accelerating)
    - fork/star_ratio >= 0.02 (passes fake-star filter)
    - last_published_days >= 14 (not recently published)
    - is_monotonic (growth is non-decreasing across the tracking window)
    """
    return (
        score >= _PUBLISH_SCORE_THRESHOLD
        and velocity > 0
        and acceleration > 0
        and fork_star_ratio >= _MIN_FORK_STAR_RATIO
        and last_published_days >= _REPUBLISH_COOLDOWN_DAYS
        and is_monotonic
    )
