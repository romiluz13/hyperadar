"""Tests for the hype score (CONTEXT.md, Hype score).

Seam: ``hype_score`` in ``integrations/_shared/hype_score.py``. Expected values
are worked by hand from the weighted sum it documents, to six decimals.
"""

import os
import sys
from datetime import datetime, timedelta, timezone

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from _shared.hype_score import hype_score  # noqa: E402

NOW = datetime(2026, 11, 26, 6, 0, tzinfo=timezone.utc)


def _score(**overrides) -> float:
    args = {
        "stars_this_week": 1000,
        "stars_today": 200,
        "total_stars": 11000,
        "hn_points": None,
        "created_at": None,
        "now": NOW,
    }
    args.update(overrides)
    return hype_score(**args)


def test_weekly_stars_relative_growth_and_stars_today():
    # 0.55·log10(1001) + 0.20·(1000/10000) + 0.15·log10(201)
    assert _score() == pytest.approx(2.015718, abs=1e-6)


def test_relative_growth_is_capped_at_one():
    # 500 new on a 100-star base is 5× growth, worth no more than 1×.
    score = _score(stars_this_week=500, stars_today=0, total_stars=600)
    assert score == pytest.approx(0.55 * 2.699838 + 0.20, abs=1e-6)


def test_a_base_under_100_stars_counts_as_100():
    # 50 new on a 10-star base: 50 / max(10, 100) = 0.5.
    score = _score(stars_this_week=50, stars_today=0, total_stars=60)
    assert score == pytest.approx(1.039164, abs=1e-6)


def test_hn_points_add_a_tenth_of_their_log():
    # 0.55·log10(101) + 0.20·0.1 + 0.15·log10(11) + 0.10·log10(100)
    score = _score(stars_this_week=100, stars_today=10, total_stars=1100, hn_points=99)
    assert score == pytest.approx(1.478586, abs=1e-6)


def test_a_repo_under_90_days_old_is_boosted_by_15_percent():
    assert _score(created_at=NOW - timedelta(days=30)) == pytest.approx(
        2.318076, abs=1e-6
    )
    assert _score(created_at=NOW - timedelta(days=89, hours=23)) == pytest.approx(
        2.318076, abs=1e-6
    )


def test_a_repo_90_days_old_or_more_is_not_boosted():
    assert _score(created_at=NOW - timedelta(days=90)) == pytest.approx(
        2.015718, abs=1e-6
    )
    assert _score(created_at=NOW - timedelta(days=400)) == pytest.approx(
        2.015718, abs=1e-6
    )


def test_a_net_star_loss_today_counts_as_zero_not_a_math_error():
    # Star history reports net per-day deltas, which can be negative.
    score = _score(stars_this_week=200, stars_today=-5, total_stars=5200)
    assert score == pytest.approx(1.274758, abs=1e-6)


def test_unknown_total_stars_earn_no_relative_growth():
    assert _score(total_stars=None) == pytest.approx(1.995718, abs=1e-6)
