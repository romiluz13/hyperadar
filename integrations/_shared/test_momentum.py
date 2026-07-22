"""Tests for the breakout prediction Momentum Score, fake-star filter, and publishing gate.

Seam: the public functions in integrations/_shared/momentum.py.
Pure function tests — no network, no database.
"""

from _shared.momentum import (
    _is_monotonic_growth,
    _velocity,
    compute_momentum_score,
    passes_fake_star_filter,
    should_publish_hidden_gem,
)


def _snapshot(stars: int, forks: int = 0) -> dict:
    """Build a daily snapshot dict."""
    return {"github_stars": stars, "github_forks": forks}


def test_velocity_gravity_decay_weights_recent_days_more():
    """_velocity should weight recent daily gains higher than older ones."""
    # 8 snapshots: stars go 100, 105, 110, 115, 120, 125, 130, 135
    # Each day gains 5 stars. With gravity decay over 7 days:
    # i=1: 5*1.0, i=2: 5*0.87, i=3: 5*0.77, i=4: 5*0.69, i=5: 5*0.625,
    # i=6: 5*0.57, i=7: 5*0.53
    history = [_snapshot(100 + 5 * d) for d in range(8)]
    vel = _velocity(history, 7)
    # Weighted sum: 5*(1.0+0.8696+0.7692+0.6897+0.625+0.5714+0.5333) ≈ 25.3
    # Simple sum would be 35
    assert 20 < vel < 35, (
        f"Gravity decay should reduce total below simple sum, got {vel}"
    )


def test_velocity_gravity_decay_zero_for_flat_history():
    """_velocity should return 0 when no stars are gained."""
    history = [_snapshot(100, 10) for _ in range(8)]
    assert _velocity(history, 7) == 0


def test_velocity_gravity_decay_only_counts_positive_gains():
    """_velocity should not count negative daily gains (star loss)."""
    # Stars: 100, 105, 110, 100, 100, 100, 100, 100
    history = [
        _snapshot(100),
        _snapshot(105),
        _snapshot(110),
        _snapshot(100),
        _snapshot(100),
        _snapshot(100),
        _snapshot(100),
        _snapshot(100),
    ]
    vel = _velocity(history, 7)
    # Only gains counted: day i=1: 0 (100->100), i=2: 0, i=3: 0, i=4: -10→0,
    # i=5: 5*0.625=3.125, i=6: 5*0.57=2.857, i=7: 5*0.53=2.667
    # Wait — i=1 is most recent (index -1 vs -2): history[-1]=100, history[-2]=100 → 0
    # i=2: history[-2]=100, history[-3]=100 → 0
    # i=3: history[-3]=100, history[-4]=100 → 0
    # i=4: history[-4]=100, history[-5]=110 → -10 → 0
    # i=5: history[-5]=110, history[-6]=105 → 5*0.625 = 3.125
    # i=6: history[-6]=105, history[-7]=100 → 5*0.571 = 2.857
    # i=7: history[-7]=100, history[-8]=100 → 0
    # Total ≈ 5.98 → int(5.98) = 5
    assert vel == 5, f"Only positive gains counted with decay, got {vel}"


def test_momentum_score_returns_zero_for_empty_history():
    assert compute_momentum_score([]) == 0


def test_momentum_score_returns_zero_for_single_entry():
    assert compute_momentum_score([_snapshot(50, 5)]) == 0


def test_momentum_score_returns_low_for_flat_history():
    """A repo with no star growth should score low."""
    history = [_snapshot(100, 10) for _ in range(14)]
    score = compute_momentum_score(history)
    assert score < 20, f"Flat history should score low, got {score}"


def test_momentum_score_returns_high_for_accelerating_history():
    """A repo with accelerating star growth should score high."""
    history = []
    stars = 10
    for day in range(30):
        stars += max(1, day // 3)
        history.append(_snapshot(stars, max(1, stars // 10)))
    score = compute_momentum_score(history)
    assert score >= 30, f"Accelerating history should score high, got {score}"


def test_momentum_score_rewards_relative_growth():
    """A small repo gaining fast should outscore a large repo gaining the same amount."""
    small_history = []
    s = 20
    for _ in range(14):
        s += 5
        small_history.append(_snapshot(s, s // 10))

    big_history = []
    b = 5000
    for _ in range(14):
        b += 5
        big_history.append(_snapshot(b, b // 10))

    small_score = compute_momentum_score(small_history)
    large_score = compute_momentum_score(big_history)
    assert small_score > large_score, (
        f"Small repo ({small_score}) should outscore large ({large_score})"
    )


def test_momentum_score_is_clamped_to_100():
    """Even extreme growth should not exceed 100."""
    history = []
    s = 0
    for day in range(30):
        s += 100 + day * 10
        history.append(_snapshot(s, s))
    score = compute_momentum_score(history)
    assert score <= 100, f"Score should be clamped to 100, got {score}"


def test_fake_star_filter_rejects_low_fork_star_ratio():
    """Fork/star < 0.02 is suspicious."""
    assert not passes_fake_star_filter(1000, 10)  # 0.01 ratio
    assert not passes_fake_star_filter(100, 1)  # 0.01 ratio


def test_fake_star_filter_accepts_healthy_fork_star_ratio():
    """Fork/star >= 0.02 passes."""
    assert passes_fake_star_filter(100, 5)  # 0.05 ratio
    assert passes_fake_star_filter(1000, 50)  # 0.05 ratio
    assert passes_fake_star_filter(50, 10)  # 0.2 ratio


def test_fake_star_filter_lets_through_zero_stars():
    """Can't evaluate a repo with zero stars — let it through."""
    assert passes_fake_star_filter(0, 0)
    assert passes_fake_star_filter(0, 5)


def test_fake_star_filter_rejects_stars_with_zero_forks():
    """Stars but zero forks is suspicious."""
    assert not passes_fake_star_filter(100, 0)
    assert not passes_fake_star_filter(50, 0)


def test_publishing_gate_rejects_low_score():
    assert not should_publish_hidden_gem(40, 10, 5, 0.1, 30)


def test_publishing_gate_rejects_zero_velocity():
    assert not should_publish_hidden_gem(70, 0, 5, 0.1, 30)


def test_publishing_gate_rejects_negative_acceleration():
    assert not should_publish_hidden_gem(70, 10, -5, 0.1, 30)


def test_publishing_gate_rejects_low_fork_star_ratio():
    assert not should_publish_hidden_gem(70, 10, 5, 0.01, 30)


def test_publishing_gate_rejects_recently_published():
    assert not should_publish_hidden_gem(70, 10, 5, 0.1, 7)


def test_publishing_gate_accepts_all_conditions_met():
    assert should_publish_hidden_gem(70, 10, 5, 0.1, 30)
    assert should_publish_hidden_gem(55, 1, 1, 0.02, 14)
    assert should_publish_hidden_gem(48, 1, 1, 0.02, 14)
    assert not should_publish_hidden_gem(47, 1, 1, 0.02, 14)


# --- Monotonicity gate tests ---


def test_monotonic_growth_passes_for_non_decreasing_stars():
    """_is_monotonic_growth returns True when stars never decrease."""
    history = [_snapshot(50), _snapshot(55), _snapshot(60), _snapshot(70)]
    assert _is_monotonic_growth(history) is True


def test_monotonic_growth_passes_for_equal_stars():
    """Non-decreasing includes flat (equal) star counts."""
    history = [_snapshot(50), _snapshot(50), _snapshot(50)]
    assert _is_monotonic_growth(history) is True


def test_monotonic_growth_passes_for_short_history():
    """History with 0 or 1 entries is vacuously monotonic."""
    assert _is_monotonic_growth([]) is True
    assert _is_monotonic_growth([_snapshot(50)]) is True


def test_monotonic_growth_fails_for_decreasing_stars():
    """_is_monotonic_growth returns False when any day has fewer stars."""
    # 50 -> 40 -> 60 -> 55 -> 70: dips at index 1 and 3
    history = [
        _snapshot(50),
        _snapshot(40),
        _snapshot(60),
        _snapshot(55),
        _snapshot(70),
    ]
    assert _is_monotonic_growth(history) is False


def test_monotonic_growth_fails_for_single_decrease():
    """Even a single dip should break monotonicity."""
    history = [_snapshot(100), _snapshot(90)]
    assert _is_monotonic_growth(history) is False


# --- Novelty bonus tests ---


def test_novelty_bonus_first_publication():
    """First publication (prior_post_count=0) adds +5 to the score."""
    history = [_snapshot(100, 20), _snapshot(110, 22)]
    base_score = compute_momentum_score(history, prior_post_count=99)
    first_score = compute_momentum_score(history, prior_post_count=0)
    assert first_score == base_score + 5


def test_novelty_bonus_second_publication():
    """Second publication (prior_post_count=1) adds +3 to the score."""
    history = [_snapshot(100, 20), _snapshot(110, 22)]
    base_score = compute_momentum_score(history, prior_post_count=99)
    second_score = compute_momentum_score(history, prior_post_count=1)
    assert second_score == base_score + 3


def test_novelty_bonus_third_plus_no_bonus():
    """Third+ publication (prior_post_count>=2) adds nothing."""
    history = [_snapshot(100, 20), _snapshot(110, 22)]
    base_score = compute_momentum_score(history, prior_post_count=99)
    third_score = compute_momentum_score(history, prior_post_count=2)
    assert third_score == base_score
    assert compute_momentum_score(history, prior_post_count=10) == base_score


def test_novelty_bonus_clamped_to_100():
    """Novelty bonus should not push the score above 100."""
    history = []
    s = 0
    for day in range(30):
        s += 100 + day * 10
        history.append(_snapshot(s, s))
    score = compute_momentum_score(history, prior_post_count=0)
    assert score == 100


# --- Monotonicity gate in publishing gate tests ---


def test_publishing_gate_rejects_non_monotonic_growth():
    """should_publish_hidden_gem rejects when is_monotonic is False."""
    assert not should_publish_hidden_gem(70, 10, 5, 0.1, 30, is_monotonic=False)


def test_publishing_gate_accepts_monotonic_default():
    """Default is_monotonic=True preserves backward compatibility."""
    assert should_publish_hidden_gem(70, 10, 5, 0.1, 30)
