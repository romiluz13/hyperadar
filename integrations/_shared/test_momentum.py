"""Tests for the breakout prediction Momentum Score, fake-star filter, and publishing gate.

Seam: the public functions in integrations/_shared/momentum.py.
Pure function tests — no network, no database.
"""

from _shared.momentum import (
    compute_momentum_score,
    passes_fake_star_filter,
    should_publish_hidden_gem,
)


def _snapshot(stars: int, forks: int = 0) -> dict:
    """Build a daily snapshot dict."""
    return {"github_stars": stars, "github_forks": forks}


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
