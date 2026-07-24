"""Tests for the unified source-typed Heat Score.

Seam: the public ``compute_heat_score`` in integrations/_shared/heat.py.
Pure function tests — no network, no database.

The heat score is a 0–100 source-typed score with five components
(velocity 35, breakout 25, recency 20, depth 10, novelty 10) capped at 100.
For YouTube the day-1 signal is views-per-hour-per-subscriber against a
decay-weighted, never-collapsing channel baseline.
"""

import pytest

from _shared.heat import (
    STABLE_AGE_HOURS,
    _HEAT_THRESHOLD_INITIAL,
    compute_heat_score,
    derive_heat_threshold,
    should_publish_heat,
)


def _yt_item(
    view_count: int,
    age_hours: float = 24.0,
    channel_subscribers: int = 10000,
    like_count: int | None = None,
) -> dict:
    """Build a YouTube item dict for compute_heat_score."""
    item = {
        "view_count": view_count,
        "age_hours": age_hours,
        "channel_subscribers": channel_subscribers,
    }
    if like_count is not None:
        item["like_count"] = like_count
    return item


def _baseline_video(view_count: int, age_hours: float) -> dict:
    """Build a baseline item (another video from the same channel)."""
    return {"view_count": view_count, "age_hours": age_hours}


# ---------------------------------------------------------------------------
# Score shape
# ---------------------------------------------------------------------------


def test_compute_heat_score_returns_full_dict_shape():
    """The result must carry every documented field."""
    result = compute_heat_score(
        "youtube", _yt_item(5000, age_hours=24.0), baseline=[], prior_posts=0
    )
    for field in (
        "heat_score",
        "velocity_score",
        "breakout_score",
        "recency_score",
        "depth_score",
        "novelty_score",
        "outperform_ratio",
        "baseline_confidence",
    ):
        assert field in result, f"missing field: {field}"
    assert isinstance(result["heat_score"], int)
    assert result["baseline_confidence"] in ("high", "low", "fallback")


def test_compute_heat_score_zero_views_scores_zero():
    """A video with no views has no heat."""
    result = compute_heat_score(
        "youtube", _yt_item(0, age_hours=24.0), baseline=[], prior_posts=0
    )
    assert result["heat_score"] == 0
    assert result["velocity_score"] == 0


# ---------------------------------------------------------------------------
# 100 cap
# ---------------------------------------------------------------------------


def test_compute_heat_score_clamped_to_100():
    """Even extreme engagement must not exceed 100."""
    # 1M views in 2h on a 1K-sub channel = runaway velocity, full breakout.
    baseline = [_baseline_video(100, age_hours=200 + i * 10) for i in range(10)]
    result = compute_heat_score(
        "youtube",
        _yt_item(1_000_000, age_hours=2.0, channel_subscribers=1000),
        baseline=baseline,
        prior_posts=0,
    )
    assert result["heat_score"] <= 100, f"clamped to 100, got {result['heat_score']}"


def test_compute_heat_score_never_negative():
    """No combination of inputs produces a negative score."""
    result = compute_heat_score(
        "youtube", _yt_item(50, age_hours=0.5), baseline=[], prior_posts=5
    )
    assert result["heat_score"] >= 0


# ---------------------------------------------------------------------------
# Monotonicity with engagement
# ---------------------------------------------------------------------------


def test_compute_heat_score_monotonic_with_views():
    """More views at the same age and channel score higher."""
    baseline = [_baseline_video(1000, age_hours=100 + i) for i in range(8)]
    low = compute_heat_score(
        "youtube", _yt_item(500, age_hours=24.0), baseline=baseline, prior_posts=0
    )
    high = compute_heat_score(
        "youtube", _yt_item(50_000, age_hours=24.0), baseline=baseline, prior_posts=0
    )
    assert high["heat_score"] > low["heat_score"], (
        f"50K views ({high['heat_score']}) should outscore 500 ({low['heat_score']})"
    )


# ---------------------------------------------------------------------------
# Min age (the day-1 noise floor at the scorer level)
# ---------------------------------------------------------------------------


def test_compute_heat_score_below_min_age_has_zero_velocity():
    """A video younger than 1h is in the creator-spike phase; velocity is 0."""
    result = compute_heat_score(
        "youtube", _yt_item(100_000, age_hours=0.5), baseline=[], prior_posts=0
    )
    assert result["velocity_score"] == 0, (
        f"under-1h video velocity must be 0, got {result['velocity_score']}"
    )


def test_compute_heat_score_at_min_age_has_velocity():
    """A video at exactly 1h starts earning velocity."""
    # 1000 views in 1h on a 1K-sub channel = 1.0 views/hr/sub (well above 0.1 ref).
    result = compute_heat_score(
        "youtube",
        _yt_item(1000, age_hours=1.0, channel_subscribers=1000),
        baseline=[],
        prior_posts=0,
    )
    assert result["velocity_score"] > 0


# ---------------------------------------------------------------------------
# Baseline confidence: the never-collapse guarantee
# ---------------------------------------------------------------------------


def test_compute_heat_score_empty_young_baseline_does_not_collapse():
    """All baseline videos <1h old must NOT make the item max the breakout.

    This is the core failure mode the design prevents: a channel that posts in
    bursts (all recent videos <1h) must not fabricate a huge outperform ratio
    from a near-zero denominator. The breakout component is zeroed and the
    baseline confidence is NOT high.
    """
    young_baseline = [_baseline_video(10, age_hours=0.1) for _ in range(10)]
    result = compute_heat_score(
        "youtube",
        _yt_item(5000, age_hours=24.0),
        baseline=young_baseline,
        prior_posts=0,
    )
    assert result["breakout_score"] == 0, (
        f"young baseline must zero breakout, got {result['breakout_score']}"
    )
    assert result["baseline_confidence"] != "high", (
        f"young baseline must not be high confidence, got {result['baseline_confidence']}"
    )
    assert result["outperform_ratio"] == 0.0


def test_compute_heat_score_empty_baseline_is_fallback():
    """No baseline items at all → fallback confidence, breakout zeroed."""
    result = compute_heat_score(
        "youtube", _yt_item(5000, age_hours=24.0), baseline=[], prior_posts=0
    )
    assert result["baseline_confidence"] == "fallback"
    assert result["breakout_score"] == 0
    assert result["outperform_ratio"] == 0.0


def test_compute_heat_score_high_confidence_with_stable_baseline():
    """≥3 stable baseline items (age > STABLE_AGE*ln2) → high confidence, breakout computed."""
    # STABLE_AGE hours; items older than ~4.2h have weight > 0.5 (stable).
    stable_age = STABLE_AGE_HOURS * 1.5  # comfortably stable
    baseline = [_baseline_video(1000, age_hours=stable_age + i) for i in range(5)]
    # Item has 10x the baseline rate (5000 views/24h vs ~1000/~stable_age).
    result = compute_heat_score(
        "youtube", _yt_item(50_000, age_hours=24.0), baseline=baseline, prior_posts=0
    )
    assert result["baseline_confidence"] == "high"
    assert result["outperform_ratio"] > 1.0
    assert result["breakout_score"] > 0


def test_compute_heat_score_low_confidence_with_few_stable():
    """Baseline items exist but <3 stable → low confidence, breakout zeroed."""
    # Only 1 stable item — not enough for high confidence.
    baseline = [_baseline_video(1000, age_hours=STABLE_AGE_HOURS * 2)]
    result = compute_heat_score(
        "youtube", _yt_item(50_000, age_hours=24.0), baseline=baseline, prior_posts=0
    )
    assert result["baseline_confidence"] == "low"
    assert result["breakout_score"] == 0


# ---------------------------------------------------------------------------
# Recency
# ---------------------------------------------------------------------------


def test_compute_heat_score_recency_decays_with_age():
    """A fresh video gets more recency than a stale one (same engagement)."""
    baseline = [_baseline_video(1000, age_hours=100) for _ in range(5)]
    fresh = compute_heat_score(
        "youtube", _yt_item(5000, age_hours=2.0), baseline=baseline, prior_posts=0
    )
    stale = compute_heat_score(
        "youtube", _yt_item(50_000, age_hours=300.0), baseline=baseline, prior_posts=0
    )
    # Recency component itself: 20 * exp(-age/48)
    assert fresh["recency_score"] > stale["recency_score"], (
        f"fresh recency {fresh['recency_score']} should beat stale {stale['recency_score']}"
    )


def test_compute_heat_score_recency_at_zero_age_is_full():
    """At age 0, recency is the full 20 (exp(0) = 1)."""
    result = compute_heat_score(
        "youtube", _yt_item(5000, age_hours=0.0), baseline=[], prior_posts=0
    )
    assert result["recency_score"] == 20


# ---------------------------------------------------------------------------
# Novelty
# ---------------------------------------------------------------------------


def test_compute_heat_score_novelty_first_publication():
    """First publication (prior_posts=0) earns the full novelty bonus."""
    result = compute_heat_score(
        "youtube", _yt_item(5000, age_hours=24.0), baseline=[], prior_posts=0
    )
    assert result["novelty_score"] == 10


def test_compute_heat_score_novelty_zero_for_repeat():
    """A previously-published item earns no novelty."""
    result = compute_heat_score(
        "youtube", _yt_item(5000, age_hours=24.0), baseline=[], prior_posts=2
    )
    assert result["novelty_score"] == 0


# ---------------------------------------------------------------------------
# Channel-relative fairness (the core cross-source property)
# ---------------------------------------------------------------------------


def test_compute_heat_score_small_channel_outranks_large_same_views():
    """Same views on a smaller channel score higher (channel-relative velocity)."""
    baseline = [_baseline_video(1000, age_hours=100) for _ in range(5)]
    small = compute_heat_score(
        "youtube",
        _yt_item(5000, age_hours=24.0, channel_subscribers=1000),
        baseline=baseline,
        prior_posts=0,
    )
    large = compute_heat_score(
        "youtube",
        _yt_item(5000, age_hours=24.0, channel_subscribers=100_000),
        baseline=baseline,
        prior_posts=0,
    )
    assert small["heat_score"] > large["heat_score"], (
        f"small channel ({small['heat_score']}) should outrank large ({large['heat_score']})"
    )


# ---------------------------------------------------------------------------
# Depth (when like data is present)
# ---------------------------------------------------------------------------


def test_compute_heat_score_depth_from_like_rate():
    """Like count relative to views contributes to depth when present."""
    baseline = [_baseline_video(1000, age_hours=100) for _ in range(5)]
    with_likes = compute_heat_score(
        "youtube",
        _yt_item(5000, age_hours=24.0, like_count=1000),
        baseline=baseline,
        prior_posts=0,
    )
    without_likes = compute_heat_score(
        "youtube",
        _yt_item(5000, age_hours=24.0, like_count=None),
        baseline=baseline,
        prior_posts=0,
    )
    assert with_likes["depth_score"] > without_likes["depth_score"]


def test_compute_heat_score_unknown_source_type_raises():
    """An unsupported source type is a programming error, not silent."""
    with pytest.raises(ValueError):
        compute_heat_score("myspace", _yt_item(5000), baseline=[], prior_posts=0)


# ---------------------------------------------------------------------------
# Reddit adapter (ticket 02)
# ---------------------------------------------------------------------------


def _rd_item(upvotes: int, comments: int = 5, age_hours: float = 6.0) -> dict:
    """Build a Reddit item dict for compute_heat_score."""
    return {"upvotes": upvotes, "comments": comments, "age_hours": age_hours}


def _rd_baseline_post(upvotes: int, age_hours: float) -> dict:
    """Build a Reddit baseline item (another post from the same subreddit)."""
    return {"upvotes": upvotes, "age_hours": age_hours}


def test_reddit_heat_score_returns_full_dict_shape():
    result = compute_heat_score(
        "reddit", _rd_item(100, 10, 6.0), baseline=[], prior_posts=0
    )
    for field in (
        "heat_score",
        "velocity_score",
        "breakout_score",
        "recency_score",
        "depth_score",
        "novelty_score",
        "outperform_ratio",
        "baseline_confidence",
    ):
        assert field in result, f"missing field: {field}"
    assert isinstance(result["heat_score"], int)
    assert result["baseline_confidence"] in ("high", "low", "fallback")


def test_reddit_zero_upvotes_scores_zero():
    """A post with no upvotes has no heat."""
    result = compute_heat_score(
        "reddit", _rd_item(0, 0, 6.0), baseline=[], prior_posts=0
    )
    assert result["heat_score"] == 0
    assert result["velocity_score"] == 0


def test_reddit_monotonic_with_upvotes():
    """More upvotes at the same age score higher."""
    baseline = [_rd_baseline_post(50, 100) for _ in range(5)]
    low = compute_heat_score(
        "reddit", _rd_item(50, 5, 6.0), baseline=baseline, prior_posts=0
    )
    high = compute_heat_score(
        "reddit", _rd_item(5000, 500, 6.0), baseline=baseline, prior_posts=0
    )
    assert high["heat_score"] > low["heat_score"], (
        f"5K upvotes ({high['heat_score']}) should outrank 50 ({low['heat_score']})"
    )


def test_reddit_baseline_high_confidence_with_stable_posts():
    """≥3 stable subreddit posts → high confidence, breakout computed."""
    stable = [
        _rd_baseline_post(50, age_hours=STABLE_AGE_HOURS * 2 + i) for i in range(5)
    ]
    result = compute_heat_score(
        "reddit", _rd_item(5000, 500, 6.0), baseline=stable, prior_posts=0
    )
    assert result["baseline_confidence"] == "high"
    assert result["outperform_ratio"] > 1.0
    assert result["breakout_score"] > 0


def test_reddit_baseline_young_does_not_collapse():
    """All subreddit posts <1h old must not fabricate a huge outperform ratio."""
    young = [_rd_baseline_post(10, 0.1) for _ in range(10)]
    result = compute_heat_score(
        "reddit", _rd_item(5000, 500, 6.0), baseline=young, prior_posts=0
    )
    assert result["breakout_score"] == 0
    assert result["baseline_confidence"] != "high"
    assert result["outperform_ratio"] == 0.0


def test_reddit_recency_decays_with_age():
    """A fresh post gets more recency than a stale one (same upvotes)."""
    fresh = compute_heat_score(
        "reddit", _rd_item(100, 10, 1.0), baseline=[], prior_posts=0
    )
    stale = compute_heat_score(
        "reddit", _rd_item(100, 10, 72.0), baseline=[], prior_posts=0
    )
    assert fresh["recency_score"] > stale["recency_score"]


def test_reddit_recency_at_zero_age_is_full():
    result = compute_heat_score(
        "reddit", _rd_item(100, 10, 0.0), baseline=[], prior_posts=0
    )
    assert result["recency_score"] == 20


def test_reddit_recency_decays_faster_than_youtube():
    """Reddit's 12h time constant decays faster than YouTube's 48h."""
    age = 24.0
    yt = compute_heat_score(
        "youtube", _yt_item(5000, age, 1000), baseline=[], prior_posts=0
    )
    rd = compute_heat_score(
        "reddit", _rd_item(100, 10, age), baseline=[], prior_posts=0
    )
    assert rd["recency_score"] < yt["recency_score"], (
        f"reddit ({rd['recency_score']}) should decay faster than youtube ({yt['recency_score']})"
    )


def test_reddit_depth_from_comments():
    """More comments relative to upvotes → more depth (driving conversation)."""
    baseline = [_rd_baseline_post(50, 100) for _ in range(5)]
    with_comments = compute_heat_score(
        "reddit", _rd_item(1000, 200, 6.0), baseline=baseline, prior_posts=0
    )
    without = compute_heat_score(
        "reddit", _rd_item(1000, 0, 6.0), baseline=baseline, prior_posts=0
    )
    assert with_comments["depth_score"] > without["depth_score"]


def test_reddit_novelty_first_publication():
    result = compute_heat_score(
        "reddit", _rd_item(100, 10, 6.0), baseline=[], prior_posts=0
    )
    assert result["novelty_score"] == 10


def test_reddit_novelty_zero_for_repeat():
    result = compute_heat_score(
        "reddit", _rd_item(100, 10, 6.0), baseline=[], prior_posts=3
    )
    assert result["novelty_score"] == 0


# ---------------------------------------------------------------------------
# Cross-source rankability (the common scale)
# ---------------------------------------------------------------------------


def test_cross_source_scores_are_rankable_on_common_scale():
    """A reddit heat score and a youtube heat score are both 0-100 ints."""
    yt = compute_heat_score(
        "youtube", _yt_item(5000, 24.0, 1000), baseline=[], prior_posts=0
    )
    rd = compute_heat_score(
        "reddit", _rd_item(500, 50, 6.0), baseline=[], prior_posts=0
    )
    assert isinstance(yt["heat_score"], int)
    assert isinstance(rd["heat_score"], int)
    assert 0 <= yt["heat_score"] <= 100
    assert 0 <= rd["heat_score"] <= 100


# ---------------------------------------------------------------------------
# Publish gate (ticket 03): should_publish_heat
# ---------------------------------------------------------------------------


def _heat(score: int, outperform: float = 0.0, confidence: str = "fallback") -> dict:
    """Build a heat_result dict for the gate."""
    return {
        "heat_score": score,
        "velocity_score": 0,
        "breakout_score": 0,
        "recency_score": 0,
        "depth_score": 0,
        "novelty_score": 0,
        "outperform_ratio": outperform,
        "baseline_confidence": confidence,
    }


def test_gate_rejects_within_reddit_cooldown():
    """A reddit thread posted <7 days ago is skipped."""
    assert not should_publish_heat(
        "reddit", _heat(80), _rd_item(5000, 500, 6.0), last_published_days=3
    )


def test_gate_rejects_within_youtube_cooldown():
    """A youtube video posted <14 days ago is skipped."""
    assert not should_publish_heat(
        "youtube", _heat(80), _yt_item(50000, 24.0, 1000), last_published_days=10
    )


def test_gate_accepts_at_reddit_cooldown_boundary():
    """Exactly 7 days (reddit) is allowed."""
    assert should_publish_heat(
        "reddit", _heat(80), _rd_item(5000, 500, 6.0), last_published_days=7
    )


def test_gate_accepts_at_youtube_cooldown_boundary():
    """Exactly 14 days (youtube) is allowed."""
    assert should_publish_heat(
        "youtube", _heat(80), _yt_item(50000, 24.0, 1000), last_published_days=14
    )


def test_gate_rejects_below_heat_threshold():
    """Below the stub threshold (40), nothing publishes."""
    assert not should_publish_heat(
        "reddit", _heat(39), _rd_item(5000, 500, 6.0), last_published_days=30
    )


def test_gate_accepts_at_heat_threshold():
    assert should_publish_heat(
        "reddit", _heat(40), _rd_item(5000, 500, 6.0), last_published_days=30
    )


def test_gate_rejects_reddit_below_noise_floor_upvotes():
    """A reddit post with <20 upvotes is rejected even with a high heat score."""
    assert not should_publish_heat(
        "reddit", _heat(80), _rd_item(15, 5, 6.0), last_published_days=30
    )


def test_gate_rejects_reddit_below_noise_floor_comments():
    """A reddit post with 0 comments is rejected."""
    assert not should_publish_heat(
        "reddit", _heat(80), _rd_item(5000, 0, 6.0), last_published_days=30
    )


def test_gate_rejects_youtube_below_noise_floor_views():
    """A youtube video with <100 views is rejected."""
    assert not should_publish_heat(
        "youtube", _heat(80), _yt_item(50, 24.0, 1000), last_published_days=30
    )


def test_gate_rejects_youtube_below_min_age():
    """A youtube video under 1h is rejected (noise floor)."""
    assert not should_publish_heat(
        "youtube", _heat(80), _yt_item(50000, 0.5, 1000), last_published_days=30
    )


def test_gate_rejects_low_outperform_when_high_confidence():
    """High-confidence baseline but outperform < 3.0x is not a breakout."""
    assert not should_publish_heat(
        "reddit",
        _heat(80, outperform=2.5, confidence="high"),
        _rd_item(5000, 500, 6.0),
        last_published_days=30,
    )


def test_gate_accepts_outperform_at_floor_when_high_confidence():
    """Exactly 3.0x outperform with a high-confidence baseline passes."""
    assert should_publish_heat(
        "reddit",
        _heat(80, outperform=3.0, confidence="high"),
        _rd_item(5000, 500, 6.0),
        last_published_days=30,
    )


def test_gate_accepts_fallback_without_outperform_requirement():
    """A new source with no baseline (fallback) is not silenced by the outperform floor.

    A brand-new channel/video has no baseline to outperform; the gate must not
    require 3.0x outperform in that case (the heat threshold + noise floor do
    the filtering). This is the silence-guard for first discovery.
    """
    assert should_publish_heat(
        "youtube",
        _heat(60, outperform=0.0, confidence="fallback"),
        _yt_item(50000, 24.0, 1000),
        last_published_days=30,
    )


def test_gate_unknown_source_type_raises():
    import pytest

    with pytest.raises(ValueError):
        should_publish_heat(
            "myspace", _heat(80), _yt_item(5000), last_published_days=30
        )


# ---------------------------------------------------------------------------
# GitHub adapter (ticket 07) — delegates to the existing momentum scorer
# ---------------------------------------------------------------------------


def _gh_history(stars_start: int, days: int = 14, daily_gain: int = 5) -> list[dict]:
    """Build a GitHub star-history (daily snapshots)."""
    return [
        {
            "github_stars": stars_start + i * daily_gain,
            "github_forks": max(1, (stars_start + i * daily_gain) // 10),
        }
        for i in range(days)
    ]


def test_github_heat_delegates_to_momentum_score():
    """compute_heat_score('github', ...) returns the momentum score as heat_score."""
    history = _gh_history(100, days=14, daily_gain=10)
    result = compute_heat_score(
        "github", {"history": history}, baseline=[], prior_posts=0
    )
    assert result["heat_score"] > 0
    assert isinstance(result["heat_score"], int)
    assert 0 <= result["heat_score"] <= 100
    assert result["baseline_confidence"] == "fallback"


def test_github_heat_more_stars_scores_higher():
    """A faster-growing repo outscores a slower one (delegates to momentum)."""
    slow = compute_heat_score(
        "github", {"history": _gh_history(100, 14, 1)}, baseline=[], prior_posts=0
    )
    fast = compute_heat_score(
        "github", {"history": _gh_history(100, 14, 50)}, baseline=[], prior_posts=0
    )
    assert fast["heat_score"] >= slow["heat_score"]


def test_github_should_publish_reuses_hidden_gem_gate():
    """The github gate delegates to should_publish_hidden_gem."""
    heat = _heat(70)
    item = {
        "velocity": 10,
        "acceleration": 5,
        "fork_star_ratio": 0.1,
        "is_monotonic": True,
    }
    assert should_publish_heat("github", heat, item, last_published_days=30)
    assert not should_publish_heat("github", heat, item, last_published_days=7)
    assert not should_publish_heat("github", _heat(30), item, last_published_days=30)


# ---------------------------------------------------------------------------
# Calibration (ticket 05): derive_heat_threshold from the post-gate distribution
# ---------------------------------------------------------------------------


def test_derive_heat_threshold_returns_80th_percentile():
    """The threshold is the 80th percentile of the post-gate score distribution."""
    scores = [10, 20, 30, 40, 50, 60, 70, 80, 90, 100]
    threshold = derive_heat_threshold(scores)
    assert threshold == 80


def test_derive_heat_threshold_has_silence_guard():
    """The threshold never goes below the minimum floor (silence guard)."""
    # All low scores — 80th percentile would be ~20, but the floor is 40.
    scores = [10, 12, 15, 18, 20]
    threshold = derive_heat_threshold(scores)
    assert threshold >= _HEAT_THRESHOLD_INITIAL, (
        f"silence guard: threshold {threshold} must be >= floor {_HEAT_THRESHOLD_INITIAL}"
    )


def test_derive_heat_threshold_empty_scores_returns_default():
    """No post-gate scores → the default threshold (not zero, not error)."""
    threshold = derive_heat_threshold([])
    assert threshold == _HEAT_THRESHOLD_INITIAL


def test_derive_heat_threshold_single_score_returns_default():
    """One score is not a distribution → the default threshold."""
    threshold = derive_heat_threshold([55])
    assert threshold == _HEAT_THRESHOLD_INITIAL
