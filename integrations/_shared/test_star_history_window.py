"""Tests for stars this week from GitHub's star history (``_shared.star_history``).

Pure and hermetic: a fake HTTP client serves responses shaped like the live
``stargazers/history`` body, and nothing here imports the database.
"""

import logging
from datetime import date, datetime, timezone

import pytest

from _shared.star_history import (
    StarHistoryBatchFailed,
    check_trending_agreement,
    fetch_stars_this_week,
    fetch_stars_this_week_batch,
)

_DAY = 86400
# Sunday 2026-11-22 00:00 UTC, the newest week of a live response on 2026-11-26.
_WEEK = 1795305600
_PREV_WEEK = _WEEK - 7 * _DAY
_NEXT_WEEK = _WEEK + 7 * _DAY
_THURSDAY = datetime(2026, 11, 26, 20, 43, tzinfo=timezone.utc)
_SUNDAY_JUST_AFTER_MIDNIGHT = datetime(2026, 11, 29, 0, 30, tzinfo=timezone.utc)
_LAST_WEEK_DAYS = [
    (date(2026, 11, 28), 0),
    (date(2026, 11, 27), 0),
    (date(2026, 11, 26), 91),
    (date(2026, 11, 25), 192),
    (date(2026, 11, 24), 205),
    (date(2026, 11, 23), 232),
    (date(2026, 11, 22), 288),
]


class _FakeResponse:
    def __init__(self, json_data):
        self._json = json_data

    def raise_for_status(self):
        pass

    def json(self):
        return self._json


class _FakeClient:
    def __init__(self, json_data, failing=()):
        self._json = json_data
        self._failing = failing
        self.calls = []

    async def get(self, url, params=None, headers=None):
        self.calls.append(url)
        if any(f"/repos/{slug}/" in url for slug in self._failing):
            raise RuntimeError("HTTP 502")
        return _FakeResponse(self._json)


def _live_shaped():
    """Newest first, ``days`` Sun..Sat, future days of the current week zero."""
    return [
        {"week": _WEEK, "total": 1008, "days": [288, 232, 205, 192, 91, 0, 0]},
        {
            "week": _PREV_WEEK,
            "total": 2867,
            "days": [690, 500, 450, 400, 350, 277, 200],
        },
    ]


async def test_mid_week_sums_last_seven_complete_days_across_the_week_boundary():
    """Thursday evening: Thursday (91) is in progress and Friday/Saturday are
    future zeros, so the window is Wed, Tue, Mon, Sun of this week plus Sat,
    Fri, Thu of last week."""
    result = await fetch_stars_this_week(
        "owner/repo", now=_THURSDAY, client=_FakeClient(_live_shaped())
    )

    assert result["starsToday"] == 192
    assert result["starsThisWeek"] == 192 + 205 + 232 + 288 + 200 + 277 + 350
    assert result["days"] == [
        (date(2026, 11, 25), 192),
        (date(2026, 11, 24), 205),
        (date(2026, 11, 23), 232),
        (date(2026, 11, 22), 288),
        (date(2026, 11, 21), 200),
        (date(2026, 11, 20), 277),
        (date(2026, 11, 19), 350),
    ]


async def test_just_after_the_week_starts_counts_the_whole_previous_week():
    """Sunday 00:30: the new week's bucket holds only Sunday (in progress), so
    the window is exactly last week Sun..Sat and stars today is Saturday."""
    now = _SUNDAY_JUST_AFTER_MIDNIGHT
    entries = [
        {"week": _NEXT_WEEK, "total": 4, "days": [4, 0, 0, 0, 0, 0, 0]},
        {"week": _WEEK, "total": 1008, "days": [288, 232, 205, 192, 91, 0, 0]},
    ]
    result = await fetch_stars_this_week(
        "owner/repo", now=now, client=_FakeClient(entries)
    )

    assert result["starsToday"] == 0
    assert result["starsThisWeek"] == 1008
    assert result["days"] == _LAST_WEEK_DAYS


async def test_just_after_the_week_starts_without_the_new_bucket_yet():
    """GitHub may not have opened the new week's bucket yet: the newest bucket
    is last week, and the answer must be the same."""
    now = _SUNDAY_JUST_AFTER_MIDNIGHT
    entries = [{"week": _WEEK, "total": 1008, "days": [288, 232, 205, 192, 91, 0, 0]}]
    result = await fetch_stars_this_week(
        "owner/repo", now=now, client=_FakeClient(entries)
    )

    assert result["starsThisWeek"] == 1008
    assert result["days"] == _LAST_WEEK_DAYS


async def test_a_day_counts_only_once_it_has_ended():
    """One second before Thursday ends it is still in progress; at Friday
    00:00 it becomes stars today."""
    before = datetime(2026, 11, 26, 23, 59, 59, tzinfo=timezone.utc)
    after = datetime(2026, 11, 27, 0, 0, tzinfo=timezone.utc)

    still_open = await fetch_stars_this_week(
        "o/r", now=before, client=_FakeClient(_live_shaped())
    )
    closed = await fetch_stars_this_week(
        "o/r", now=after, client=_FakeClient(_live_shaped())
    )

    assert still_open["starsToday"] == 192
    assert closed["starsToday"] == 91
    assert closed["starsThisWeek"] == 91 + 192 + 205 + 232 + 288 + 200 + 277


async def test_batch_with_over_half_failing_raises():
    client = _FakeClient(_live_shaped(), failing=("a/fail", "b/fail"))
    with pytest.raises(StarHistoryBatchFailed):
        await fetch_stars_this_week_batch(
            ["a/fail", "b/fail", "c/ok"], now=_THURSDAY, client=client
        )


async def test_batch_with_half_failing_returns_results_and_logged_failures(caplog):
    client = _FakeClient(_live_shaped(), failing=("a/fail", "b/fail"))
    with caplog.at_level(logging.WARNING):
        results, failures = await fetch_stars_this_week_batch(
            ["a/fail", "b/fail", "c/ok", "d/ok", "c/ok"], now=_THURSDAY, client=client
        )

    assert set(results) == {"c/ok", "d/ok"}
    assert results["c/ok"]["starsThisWeek"] == 192 + 205 + 232 + 288 + 200 + 277 + 350
    assert set(failures) == {"a/fail", "b/fail"}
    assert "a/fail" in caplog.text and "b/fail" in caplog.text
    assert len(client.calls) == 4, "one call per distinct repo"


@pytest.mark.parametrize(
    ("printed", "history", "warns"),
    [
        (301, 100, True),
        (100, 301, True),
        (300, 100, False),
        (100, 300, False),
        (50, 0, True),
        (0, 0, False),
    ],
)
def test_trending_disagreement_over_three_times_warns(caplog, printed, history, warns):
    with caplog.at_level(logging.WARNING):
        check_trending_agreement("owner/repo", printed, history)

    assert ("owner/repo" in caplog.text) is warns


async def test_an_empty_history_raises_instead_of_reading_as_a_quiet_week():
    with pytest.raises(ValueError):
        await fetch_stars_this_week("owner/repo", now=_THURSDAY, client=_FakeClient([]))


async def test_a_week_off_the_day_grid_raises_instead_of_counting_zero():
    entries = _live_shaped()
    entries[1]["week"] += 3600
    with pytest.raises(ValueError):
        await fetch_stars_this_week(
            "owner/repo", now=_THURSDAY, client=_FakeClient(entries)
        )
