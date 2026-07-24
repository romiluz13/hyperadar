"""Unified source-typed Heat Score — cross-source discovery scoring.

A single 0–100 heat score with a source-typed entry point
``compute_heat_score(source_type, item, baseline, prior_posts)``. Each source
type adapts the common five-component model to its own signals:

    velocity  (up to 35) — how fast engagement is arriving
    breakout  (up to 25) — how much faster than this source's own baseline
    recency   (up to 20) — exponential decay, source-appropriate half-life
    depth     (up to 10) — conversation/engagement relative to reach
    novelty   (up to 10) — not already posted (dedup bonus)

The score is ``min(100, sum of components)``.

This is the new shared scorer (``_shared/heat.py``). The existing GitHub
momentum scorer (``_shared/momentum.py``) is GitHub-specific (stars/forks
time-series, fake-star filter) and becomes the ``github`` adapter behind this
dispatch in a later ticket; this module owns the ``youtube`` (and ``reddit``)
paths and the cross-source-comparable contract.
"""

from __future__ import annotations

import statistics
from math import exp

from _shared.momentum import compute_momentum_score, should_publish_hidden_gem


# --- Component weights (the new cross-source schema; sums to 100) -----------
_VELOCITY_WEIGHT = 35
_BREAKOUT_WEIGHT = 25
_RECENCY_WEIGHT = 20
_DEPTH_WEIGHT = 10
_NOVELTY_WEIGHT = 10

# --- Baseline confidence (the never-collapse guarantee) ----------------------
# Weight per baseline item: ``w = 1.0 - exp(-age_hours / STABLE_AGE_HOURS)``.
# An item is "stable" once its weight exceeds 0.5 (age > STABLE_AGE * ln 2).
# High confidence needs >= 3 stable items; fewer → low; none → fallback, and
# the breakout component is zeroed rather than fabricated from a thin baseline.
STABLE_AGE_HOURS = 6.0

# --- YouTube v1 constants ----------------------------------------------------
_YOUTUBE_VELOCITY_REF = 0.1  # views/hour/subscriber for full velocity marks
_YOUTUBE_RECENCY_HALF_LIFE_HOURS = 48.0
_YOUTUBE_MIN_AGE_HOURS = 1.0  # under this, velocity is 0 (creator-spike guard)
_YOUTUBE_DEFAULT_SUBSCRIBERS = 10000  # fallback when channel data is missing

# --- Reddit v1 constants -----------------------------------------------------
_REDDIT_VELOCITY_REF = 100.0  # upvotes/hour for full velocity marks
_REDDIT_RECENCY_HALF_LIFE_HOURS = 12.0

_OUTFORM_FULL_AT = 3.0  # outperform ratio that earns full breakout marks

# --- Publish gate (ticket 03) — stub threshold, calibrated in ticket 05 -------
_HEAT_THRESHOLD_INITIAL = 40
_REDDIT_COOLDOWN_DAYS = 7
_YOUTUBE_COOLDOWN_DAYS = 14
_REDDIT_MIN_UPVOTES = 20
_REDDIT_MIN_COMMENTS = 1
_YOUTUBE_MIN_VIEWS = 100
_YOUTUBE_MIN_AGE_HOURS = 1.0
_OUTFORM_FLOOR = 3.0


def should_publish_heat(
    source_type: str,
    heat_result: dict,
    item: dict,
    last_published_days: int,
    threshold: int = _HEAT_THRESHOLD_INITIAL,
) -> bool:
    """Gate whether a heat-scored item should be published.

    All of:
    - past the source cooldown (7d reddit, 14d youtube — the latter fixes the
      re-post-every-run bug)
    - heat_score >= threshold (stub 40, calibrated from the post-gate score
      distribution in ticket 05)
    - past the source noise floor (reddit: >=20 upvotes and >=1 comment;
      youtube: >=100 views and age >=1h)
    - when the baseline confidence is high, a genuine breakout (>=3.0x the
      source baseline). First discovery with no baseline (fallback) is NOT
      silenced by the outperform floor — the threshold + noise floor filter it.
    """
    if source_type == "reddit":
        cooldown = _REDDIT_COOLDOWN_DAYS
    elif source_type == "youtube":
        cooldown = _YOUTUBE_COOLDOWN_DAYS
    elif source_type == "github":
        return _github_should_publish(heat_result, item, last_published_days, threshold)
    else:
        raise ValueError(f"unsupported source type: {source_type!r}")

    if last_published_days < cooldown:
        return False
    if heat_result["heat_score"] < threshold:
        return False
    if not _passes_noise_floor(source_type, item):
        return False
    if (
        heat_result["baseline_confidence"] == "high"
        and heat_result["outperform_ratio"] < _OUTFORM_FLOOR
    ):
        return False
    return True


def _github_heat(item: dict, prior_posts: int) -> dict:
    """GitHub adapter — delegates to the existing momentum scorer.

    The existing ``compute_momentum_score`` is GitHub-specific (stars/forks
    time-series, fake-star filter, consistency, viral bonus). This adapter
    wraps its int return in the cross-source heat dict so GitHub is rankable
    on the common scale. The component breakdown carries the GitHub-specific
    components (not the 5-component heat schema) — ``heat_score`` is the
    cross-source-comparable field.
    """
    history = item.get("history", [])
    score = compute_momentum_score(history, prior_post_count=prior_posts)
    return {
        "heat_score": score,
        "velocity_score": 0,
        "breakout_score": 0,
        "recency_score": 0,
        "depth_score": 0,
        "novelty_score": 0,
        "outperform_ratio": 0.0,
        "baseline_confidence": "fallback",
    }


def _passes_noise_floor(source_type: str, item: dict) -> bool:
    """Source-specific noise floor (the hard minimums before the LLM sees it)."""
    if source_type == "reddit":
        return (
            item.get("upvotes", 0) >= _REDDIT_MIN_UPVOTES
            and item.get("comments", 0) >= _REDDIT_MIN_COMMENTS
        )
    if source_type == "youtube":
        return (
            item.get("view_count", 0) >= _YOUTUBE_MIN_VIEWS
            and item.get("age_hours", 0.0) >= _YOUTUBE_MIN_AGE_HOURS
        )
    return False


def derive_heat_threshold(post_gate_scores: list[int]) -> int:
    """Derive the publish threshold from the post-gate score distribution.

    Computes the 80th percentile of the scores that pass the noise floors,
    age caps, and outperformance gate (NOT all-scored items — the post-gate
    population). Silence-guarded: never goes below ``_HEAT_THRESHOLD_INITIAL``
    so a bad week does not publish noise. Returns the default when the
    distribution is too thin (<2 scores) to calibrate.
    """
    if len(post_gate_scores) < 2:
        return _HEAT_THRESHOLD_INITIAL
    sorted_scores = sorted(post_gate_scores)
    import math

    idx = math.ceil(0.8 * len(sorted_scores)) - 1
    idx = max(0, min(idx, len(sorted_scores) - 1))
    percentile = sorted_scores[idx]
    return max(percentile, _HEAT_THRESHOLD_INITIAL)


def _github_should_publish(
    heat_result: dict, item: dict, last_published_days: int, threshold: int
) -> bool:
    """GitHub publish gate — reuses the existing should_publish_hidden_gem.

    Delegates to the GitHub-specific gate (which checks the momentum score,
    velocity, acceleration, fork/star ratio, and monotonic growth) so GitHub
    keeps its existing behavior behind the unified dispatch.
    """
    return should_publish_hidden_gem(
        score=heat_result["heat_score"],
        velocity=item.get("velocity", 0),
        acceleration=item.get("acceleration", 0),
        fork_star_ratio=item.get("fork_star_ratio", 0.0),
        last_published_days=last_published_days,
        is_monotonic=item.get("is_monotonic", True),
    )


def _zero_result() -> dict:
    return {
        "heat_score": 0,
        "velocity_score": 0,
        "breakout_score": 0,
        "recency_score": 0,
        "depth_score": 0,
        "novelty_score": 0,
        "outperform_ratio": 0.0,
        "baseline_confidence": "fallback",
    }


def compute_heat_score(
    source_type: str, item: dict, baseline: list[dict], prior_posts: int = 0
) -> dict:
    """Compute a 0–100 source-typed heat score.

    ``source_type`` — ``"youtube"`` | ``"reddit"`` | ``"github"``.
    ``item`` — source-specific fields (see each adapter).
    ``baseline`` — recent items from the same source (same channel for
      youtube), *excluding* the scored item, each with ``view_count`` and
      ``age_hours`` (youtube) or the equivalent. Used for the breakout
      comparison against the source's own recent baseline.
    ``prior_posts`` — number of prior publications of this item (novelty).

    Returns a dict with ``heat_score`` and the per-component breakdown,
    ``outperform_ratio``, and ``baseline_confidence`` (``"high"`` |
    ``"low"`` | ``"fallback"``).
    """
    if source_type == "youtube":
        return _youtube_heat(item, baseline, prior_posts)
    if source_type == "reddit":
        return _reddit_heat(item, baseline, prior_posts)
    if source_type == "github":
        return _github_heat(item, prior_posts)
    raise ValueError(f"unsupported source type: {source_type!r}")


def _youtube_heat(item: dict, baseline: list[dict], prior_posts: int) -> dict:
    view_count = int(item.get("view_count", 0) or 0)
    age_hours = max(0.0, float(item.get("age_hours", 0.0) or 0.0))
    subs = int(item.get("channel_subscribers", 0) or 0)
    if subs <= 0:
        subs = _YOUTUBE_DEFAULT_SUBSCRIBERS
    like_count = item.get("like_count")

    # No engagement at all → no heat. The >=100-view noise floor is the publish
    # gate's job; zero views is a hard "no signal" floor at the scorer level.
    if view_count <= 0:
        return _zero_result()

    # --- velocity (35): views/hour/subscriber, available from the first fetch ---
    velocity_score = 0
    if age_hours >= _YOUTUBE_MIN_AGE_HOURS:
        views_per_hour = view_count / age_hours
        views_per_hour_per_sub = views_per_hour / subs
        velocity_score = min(
            _VELOCITY_WEIGHT,
            int(_VELOCITY_WEIGHT * views_per_hour_per_sub / _YOUTUBE_VELOCITY_REF),
        )

    # --- baseline confidence + breakout (25): the never-collapse guarantee ----
    baseline_rate, confidence = _baseline_rate(baseline, "view_count")
    breakout_score = 0
    outperform_ratio = 0.0
    if confidence == "high" and baseline_rate > 0 and age_hours > 0:
        item_rate = view_count / age_hours
        outperform_ratio = item_rate / baseline_rate
        if outperform_ratio >= 1.0:
            breakout_score = min(
                _BREAKOUT_WEIGHT,
                int(_BREAKOUT_WEIGHT * outperform_ratio / _OUTFORM_FULL_AT),
            )

    # --- recency (20): exponential decay, 48h half-life for youtube ------------
    recency_score = int(
        _RECENCY_WEIGHT * exp(-age_hours / _YOUTUBE_RECENCY_HALF_LIFE_HOURS)
    )

    # --- depth (10): like rate when like data is present, else 0 ---------------
    depth_score = 0
    if like_count is not None and view_count > 0:
        like_rate = like_count / view_count
        depth_score = min(_DEPTH_WEIGHT, int(_DEPTH_WEIGHT * like_rate * 10))

    # --- novelty (10): first publication only ----------------------------------
    novelty_score = _NOVELTY_WEIGHT if prior_posts == 0 else 0

    heat_score = min(
        100,
        velocity_score + breakout_score + recency_score + depth_score + novelty_score,
    )
    return {
        "heat_score": heat_score,
        "velocity_score": velocity_score,
        "breakout_score": breakout_score,
        "recency_score": recency_score,
        "depth_score": depth_score,
        "novelty_score": novelty_score,
        "outperform_ratio": round(outperform_ratio, 3),
        "baseline_confidence": confidence,
    }


def _reddit_heat(item: dict, baseline: list[dict], prior_posts: int) -> dict:
    upvotes = int(item.get("upvotes", 0) or 0)
    comments = int(item.get("comments", 0) or 0)
    age_hours = max(0.0, float(item.get("age_hours", 0.0) or 0.0))

    # No engagement at all → no heat. The >=20-upvote noise floor is the publish
    # gate's job; zero upvotes is a hard "no signal" floor at the scorer level.
    if upvotes <= 0:
        return _zero_result()

    # --- velocity (35): upvotes/hour, reference 100 for full marks -------------
    velocity_score = 0
    if age_hours > 0:
        upvotes_per_hour = upvotes / age_hours
        velocity_score = min(
            _VELOCITY_WEIGHT,
            int(_VELOCITY_WEIGHT * upvotes_per_hour / _REDDIT_VELOCITY_REF),
        )

    # --- baseline confidence + breakout (25): same decay-weighted guarantee ---
    baseline_rate, confidence = _baseline_rate(baseline, "upvotes")
    breakout_score = 0
    outperform_ratio = 0.0
    if confidence == "high" and baseline_rate > 0 and age_hours > 0:
        item_rate = upvotes / age_hours
        outperform_ratio = item_rate / baseline_rate
        if outperform_ratio >= 1.0:
            breakout_score = min(
                _BREAKOUT_WEIGHT,
                int(_BREAKOUT_WEIGHT * outperform_ratio / _OUTFORM_FULL_AT),
            )

    # --- recency (20): exponential decay, 12h time constant for reddit --------
    recency_score = int(
        _RECENCY_WEIGHT * exp(-age_hours / _REDDIT_RECENCY_HALF_LIFE_HOURS)
    )

    # --- depth (10): comment rate — conversation relative to reach ------------
    depth_score = 0
    if upvotes > 0 and comments > 0:
        comment_rate = comments / upvotes
        depth_score = min(_DEPTH_WEIGHT, int(comment_rate * 50))

    # --- novelty (10): first publication only ----------------------------------
    novelty_score = _NOVELTY_WEIGHT if prior_posts == 0 else 0

    heat_score = min(
        100,
        velocity_score + breakout_score + recency_score + depth_score + novelty_score,
    )
    return {
        "heat_score": heat_score,
        "velocity_score": velocity_score,
        "breakout_score": breakout_score,
        "recency_score": recency_score,
        "depth_score": depth_score,
        "novelty_score": novelty_score,
        "outperform_ratio": round(outperform_ratio, 3),
        "baseline_confidence": confidence,
    }


def _baseline_rate(baseline: list[dict], engagement_field: str) -> tuple[float, str]:
    """Decay-weighted, never-collapsing baseline rate + confidence level.

    ``engagement_field`` is the per-item engagement metric (``"view_count"``
    for youtube, ``"upvotes"`` for reddit). Returns ``(rate, confidence)``
    where confidence is ``"high"`` (>= 3 stable items, weight > 0.5),
    ``"low"`` (some items but < 3 stable), or ``"fallback"`` (no usable
    items). Only high confidence yields a usable rate; low/fallback zero the
    breakout component upstream.
    """
    if not baseline:
        return 0.0, "fallback"
    stable_rates = []
    for v in baseline:
        age = float(v.get("age_hours", 0.0) or 0.0)
        engagement = int(v.get(engagement_field, 0) or 0)
        if age <= 0 or engagement <= 0:
            continue
        weight = 1.0 - exp(-age / STABLE_AGE_HOURS)
        if weight > 0.5:
            stable_rates.append(engagement / age)
    if len(stable_rates) >= 3:
        return statistics.median(stable_rates), "high"
    if stable_rates or baseline:
        return 0.0, "low"
    return 0.0, "fallback"
