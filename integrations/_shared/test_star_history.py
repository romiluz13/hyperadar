"""Tests for the GitHub star-history backfill (see ``_shared.star_history``).

The momentum gate needs 7+ of our own daily snapshots before a repo is
scoreable; the backfill seeds real GitHub star history so repos are scoreable
on first sight. These tests lock the reconstruction math, the authority rule
(organic snapshots are never duplicated or overwritten), idempotency, and
graceful degradation — all hermetic (fake HTTP client, test-only MongoDB).
"""

import logging
from datetime import date, datetime, timedelta, timezone

from _shared import mongo
from _shared.star_history import (
    backfill_star_history,
    fetch_star_history,
    reconstruct_daily_counts,
)


def _week_ts(d: date) -> int:
    return int(datetime(d.year, d.month, d.day, tzinfo=timezone.utc).timestamp())


# ─── Reconstruction math ───


def test_reconstruct_daily_counts_math():
    """Absolute per-day counts = current stars minus the deltas after that day.

    Fixed calendar: today is Tuesday 2026-09-15, so the current week's Sunday
    is 2026-09-13. Entries are newest-first (API order); days are Sun..Sat.
    """
    entries = [
        # current week: Sun=3, Mon=1, Tue(=today, partial)=2
        {"week": _week_ts(date(2026, 9, 13)), "total": 6, "days": [3, 1, 2]},
        # previous week: +2/day
        {"week": _week_ts(date(2026, 9, 6)), "total": 14, "days": [2] * 7},
        # two weeks ago: +1..+7/day
        {"week": _week_ts(date(2026, 8, 30)), "total": 28, "days": [1, 2, 3, 4, 5, 6, 7]},
    ]
    points = reconstruct_daily_counts(entries, current_stars=100, today=date(2026, 9, 15))

    expected = [
        (date(2026, 8, 30), 53), (date(2026, 8, 31), 55), (date(2026, 9, 1), 58),
        (date(2026, 9, 2), 62), (date(2026, 9, 3), 67), (date(2026, 9, 4), 73),
        (date(2026, 9, 5), 80), (date(2026, 9, 6), 82), (date(2026, 9, 7), 84),
        (date(2026, 9, 8), 86), (date(2026, 9, 9), 88), (date(2026, 9, 10), 90),
        (date(2026, 9, 11), 92), (date(2026, 9, 12), 94), (date(2026, 9, 13), 97),
        (date(2026, 9, 14), 98),
    ]
    assert points == expected
    # The series must be non-decreasing: days are gains, never losses.
    assert all(b[1] >= a[1] for a, b in zip(points, points[1:]))


def test_reconstruct_skips_today_and_future_days():
    """Today's partial delta belongs to the tracker's own snapshot, never to
    the backfill; days at or after today are excluded entirely."""
    entries = [
        {"week": _week_ts(date(2026, 9, 13)), "total": 6, "days": [3, 1, 2]},
    ]
    points = reconstruct_daily_counts(entries, current_stars=100, today=date(2026, 9, 15))
    assert [d for d, _ in points] == [date(2026, 9, 13), date(2026, 9, 14)]


def test_reconstruct_clamps_negative_counts_to_zero():
    """More deltas than current stars (un-starred repos) clamps at 0 instead
    of fabricating negative star counts."""
    entries = [
        {"week": _week_ts(date(2026, 9, 6)), "total": 35, "days": [5] * 7},
        {"week": _week_ts(date(2026, 8, 30)), "total": 70, "days": [10] * 7},
    ]
    points = reconstruct_daily_counts(entries, current_stars=40, today=date(2026, 9, 15))
    assert points, "should still produce points for the complete days"
    assert all(stars >= 0 for _, stars in points)
    assert points[0][1] == 0, "oldest day is deep underwater — clamped to 0"


def test_reconstruct_empty_entries():
    """No history (or a repo with zero stars ever) yields no points."""
    assert reconstruct_daily_counts([], current_stars=0, today=date(2026, 9, 15)) == []
    assert reconstruct_daily_counts(None, current_stars=5, today=date(2026, 9, 15)) == []


# ─── HTTP client ───


class _FakeResponse:
    def __init__(self, json_data=None, status_code=200):
        self._json = json_data
        self.status_code = status_code

    def raise_for_status(self):
        if self.status_code >= 400:
            raise RuntimeError(f"HTTP {self.status_code}")

    def json(self):
        return self._json


class _FakeClient:
    """Records GET calls; serves canned JSON or raises."""

    def __init__(self, json_data=None, exc=None):
        self._json = json_data
        self._exc = exc
        self.calls = []

    async def get(self, url, params=None, headers=None):
        self.calls.append((url, params, headers))
        if self._exc:
            raise self._exc
        return _FakeResponse(json_data=self._json)


async def test_fetch_star_history_request_shape():
    """The endpoint, per_page, and pinned API version match the documented
    contract (X-GitHub-Api-Version: 2026-03-10)."""
    client = _FakeClient(json_data=[{"week": _week_ts(date(2026, 9, 13)), "total": 1, "days": [1]}])
    data = await fetch_star_history("owner/repo", client=client)

    url, params, headers = client.calls[0]
    assert url == "https://api.github.com/repos/owner/repo/stargazers/history"
    assert params == {"per_page": 6}
    assert headers["X-GitHub-Api-Version"] == "2026-03-10"
    assert headers["Accept"] == "application/vnd.github+json"
    assert data == client._json


async def test_fetch_star_history_rejects_non_list_body():
    """A non-list body (error page, changed contract) raises instead of
    silently producing garbage snapshots."""
    client = _FakeClient(json_data={"message": "Not Found"})
    try:
        await fetch_star_history("owner/repo", client=client)
        raise AssertionError("expected ValueError")
    except ValueError:
        pass


# ─── backfill_star_history (db-writing) ───


def _entries_relative_to_today():
    """Two weeks of entries anchored to the real current week, so the test is
    stable on any run date."""
    today = datetime.now(timezone.utc).date()
    sunday = today - timedelta(days=(today.weekday() + 1) % 7)
    return [
        {"week": _week_ts(sunday), "total": 4, "days": [3, 1]},
        {"week": _week_ts(sunday - timedelta(days=7)), "total": 14, "days": [2] * 7},
    ]


async def test_backfill_inserts_missing_days_and_preserves_organic(db):
    """Backfill seeds every missing complete day with the exact signal schema,
    skips days that already have an organic snapshot, and leaves the organic
    doc untouched (organic snapshots stay the authority)."""
    project_id = "https://github.com/test/backfill-repo"
    db.signals.delete_many({"projectId": project_id})
    try:
        entries = _entries_relative_to_today()
        points = reconstruct_daily_counts(entries, current_stars=100)
        assert len(points) >= 7  # at least one full week of complete days

        # Organic snapshot on the most recent complete day — must survive.
        organic_day = points[-1][0]
        db.signals.insert_one(
            {
                "capturedAt": datetime(
                    organic_day.year, organic_day.month, organic_day.day, 10, 0
                ),
                "projectId": project_id,
                "postId": "",
                "github_stars": 123,
                "github_forks": 7,
            }
        )

        async_db = mongo._get_db()
        client = _FakeClient(json_data=entries)
        inserted = await backfill_star_history(
            async_db, project_id, current_stars=100, current_forks=9, client=client
        )

        assert inserted == len(points) - 1, "every complete day except the organic one"
        expected_days = {d for d, _ in points} - {organic_day}
        stored = list(
            db.signals.find({"projectId": project_id}, {"capturedAt": 1})
        )
        assert len(stored) == len(points)
        stored_days = {
            doc["capturedAt"].date() if doc["capturedAt"].tzinfo is None
            else doc["capturedAt"].astimezone(timezone.utc).date()
            for doc in stored
        }
        assert stored_days == {d for d, _ in points}
        assert expected_days.issubset(stored_days)

        docs = list(db.signals.find({"projectId": project_id}))
        for doc in docs:
            if doc["capturedAt"].date() == organic_day and doc["capturedAt"].hour == 10:
                assert doc["github_stars"] == 123, "organic snapshot is untouched"
            else:
                # Backfilled docs: exact signal schema, mid-day UTC capture.
                assert doc["postId"] == ""
                assert doc["projectId"] == project_id
                assert doc["github_forks"] == 9
                assert 0 <= doc["github_stars"] <= 100
                assert doc["capturedAt"].hour == 12
    finally:
        db.signals.delete_many({"projectId": project_id})


async def test_backfill_is_idempotent(db):
    """A second run re-checks the API but inserts nothing new (every complete
    day already exists), so snapshots are never duplicated."""
    project_id = "https://github.com/test/backfill-idempotent"
    db.signals.delete_many({"projectId": project_id})
    try:
        async_db = mongo._get_db()
        client = _FakeClient(json_data=_entries_relative_to_today())

        first = await backfill_star_history(
            async_db, project_id, current_stars=100, current_forks=9, client=client
        )
        assert first > 0

        second = await backfill_star_history(
            async_db, project_id, current_stars=100, current_forks=9, client=client
        )
        assert second == 0, "all complete days already present"

        total = list(db.signals.find({"projectId": project_id}))
        assert len(total) == first, "no duplicates from the second run"
        assert len(client.calls) == 2, "history is still sparse, so the API is re-checked"
    finally:
        db.signals.delete_many({"projectId": project_id})


async def test_backfill_skips_dense_repo(db):
    """A repo with 31+ snapshots (full 30-day scoring coverage) never triggers
    an API call — the backfill self-extinguishes."""
    project_id = "https://github.com/test/dense-repo"
    db.signals.delete_many({"projectId": project_id})
    try:
        today = datetime.now(timezone.utc).date()
        for i in range(31):
            day = today - timedelta(days=30 - i)
            db.signals.insert_one(
                {
                    "capturedAt": datetime(day.year, day.month, day.day, 10, 0),
                    "projectId": project_id,
                    "postId": "",
                    "github_stars": 100 + i,
                    "github_forks": 9,
                }
            )

        async_db = mongo._get_db()
        client = _FakeClient(json_data=_entries_relative_to_today())
        inserted = await backfill_star_history(
            async_db, project_id, current_stars=131, current_forks=9, client=client
        )
        assert inserted == 0
        assert client.calls == [], "dense repos must not hit the API"
    finally:
        db.signals.delete_many({"projectId": project_id})


async def test_backfill_degrades_gracefully_on_api_failure(db, caplog):
    """Any API failure means 'no backfill available', not a crashed tracker
    run: warn, return 0, write nothing."""
    project_id = "https://github.com/test/backfill-broken"
    db.signals.delete_many({"projectId": project_id})
    try:
        async_db = mongo._get_db()
        client = _FakeClient(exc=RuntimeError("network down"))
        with caplog.at_level(logging.WARNING, logger="root"):
            inserted = await backfill_star_history(
                async_db, project_id, current_stars=100, current_forks=9, client=client
            )
        assert inserted == 0
        assert any("Star-history backfill skipped" in r.message for r in caplog.records)
        assert db.signals.count_documents({"projectId": project_id}) == 0
    finally:
        db.signals.delete_many({"projectId": project_id})


async def test_backfill_skips_non_github_projects():
    """A project id that isn't a GitHub repo URL has no star history to fetch."""
    client = _FakeClient(json_data=[])
    inserted = await backfill_star_history(
        None, "https://example.com/not-a-repo", current_stars=10, current_forks=1,
        client=client,
    )
    assert inserted == 0
    assert client.calls == []
