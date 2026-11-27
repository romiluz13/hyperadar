"""The farm check (Q33): two signs of bought stars, and the HN-burst exception."""

from datetime import date, datetime, timedelta, timezone

import farm_check

_NOW = datetime(2026, 11, 26, 12, tzinfo=timezone.utc)
_OLD = _NOW - timedelta(days=400)
_YOUNG = _NOW - timedelta(days=20)
_BURST_DAY = date(2026, 11, 25)


def _week(burst: int, rest: int = 10) -> list[tuple[date, int]]:
    """Seven days newest first: ``burst`` stars yesterday, ``rest`` on each other day."""
    return [(_BURST_DAY, burst)] + [
        (_BURST_DAY - timedelta(days=i), rest) for i in range(1, 7)
    ]


def _reason(**overrides):
    days = overrides.pop("days", _week(burst=100, rest=100))
    facts = {
        "forks": 50,
        "stars_this_week": sum(stars for _, stars in days),
        "days": days,
        "created_at": _OLD,
        "now": _NOW,
        "hn_points_by_day": {},
    }
    facts.update(overrides)
    return farm_check.farm_reason(**facts)


def test_steady_growth_with_forks_passes():
    assert _reason() is None


def test_no_forks_and_300_stars_this_week_is_excluded():
    assert "forks" in _reason(forks=0, stars_this_week=300)


def test_no_forks_below_300_stars_this_week_passes():
    assert _reason(forks=0, stars_this_week=299) is None


def test_one_day_over_80_percent_of_the_week_on_an_old_repo_is_excluded():
    # 940 of 1,000 stars on one day: 94%.
    assert "one day" in _reason(days=_week(burst=940))


def test_exactly_80_percent_on_one_day_passes():
    # 240 of 300 stars: exactly 80%, and "over 80%" is the rule.
    assert _reason(days=_week(burst=240)) is None


def test_a_burst_on_a_repo_30_days_old_or_younger_passes():
    assert _reason(days=_week(burst=940), created_at=_YOUNG) is None


def test_a_burst_with_a_100_point_hn_story_that_day_passes():
    assert _reason(days=_week(burst=940), hn_points_by_day={_BURST_DAY: 100}) is None


def test_a_burst_with_a_99_point_hn_story_that_day_is_excluded():
    assert _reason(days=_week(burst=940), hn_points_by_day={_BURST_DAY: 99})


def test_a_big_hn_story_on_another_day_does_not_excuse_the_burst():
    other_day = _BURST_DAY - timedelta(days=3)
    assert _reason(days=_week(burst=940), hn_points_by_day={other_day: 800})
