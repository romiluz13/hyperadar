"""Tests for the github-radar daily snapshot tracker."""

import logging
from datetime import date, datetime, timezone

import pytest

from _shared import mongo


async def _no_backfill(db, project_id, current_stars, current_forks):
    """Hermetic stand-in for the star-history backfill (no network in tests)."""
    return 0


def test_discovery_bands_rotate_daily():
    """Four consecutive days use four different discovery bands, then cycle.

    Locks the fix for the closed discovery loop: a single fixed query
    re-tracked the same ~100 recently-updated repos forever.
    """
    from github_radar import tracker

    days = [date(2026, 9, 15), date(2026, 9, 16), date(2026, 9, 17),
            date(2026, 9, 18), date(2026, 9, 19)]
    bands = [
        tracker._band_for_date(datetime(d.year, d.month, d.day, tzinfo=timezone.utc))
        for d in days
    ]
    assert len(set(bands[:4])) == 4, "four consecutive days should use four different bands"
    assert bands[4] == bands[0], "the rotation should cycle back to the first band"
    assert all(b in tracker._DISCOVERY_BANDS for b in bands)


def test_discovery_bands_are_distinct_queries():
    """Every band must differ in star range, window, or sort — no duplicates."""
    from github_radar import tracker

    assert len(set(tracker._DISCOVERY_BANDS)) == len(tracker._DISCOVERY_BANDS)
    # At least one band must not be the legacy sort:updated band, or the
    # loop isn't actually broken.
    sorts = {b[2] for b in tracker._DISCOVERY_BANDS}
    assert sorts != {"updated"}, "bands must vary sort order, not just star ranges"


@pytest.mark.asyncio
async def test_search_candidates_logs_warning_on_exception(caplog):
    """A failed GitHub search logs a warning, not a silent swallow."""
    from github_radar import tracker

    class _BoomClient:
        async def get(self, *a, **kw):
            raise RuntimeError("network down")

    with caplog.at_level(logging.WARNING, logger="root"):
        result = await tracker._search_candidates(_BoomClient())

    assert result == [], "should return empty list on exception"
    assert any("GitHub search failed" in r.message for r in caplog.records), (
        "must log a warning with context when the search raises"
    )


@pytest.mark.asyncio
async def test_track_daily_snapshots_inserts_new_signals(db, monkeypatch):
    """New candidates get a signal snapshot with empty postId."""
    from github_radar import tracker

    fake_candidates = [
        {
            "url": "https://github.com/test/mock-ai-repo",
            "github_stars": 300,
            "github_forks": 50,
        },
        {
            "url": "https://github.com/test/mock-llm-tool",
            "github_stars": 500,
            "github_forks": 80,
        },
    ]

    async def fake_search(client):
        return fake_candidates

    monkeypatch.setattr(tracker, "_search_candidates", fake_search)

    async def no_trending(client):
        return []

    monkeypatch.setattr(tracker, "_trending_candidates", no_trending)
    monkeypatch.setattr(tracker, "backfill_star_history", _no_backfill)

    async_db = mongo._get_db()
    # Clean up any pre-existing data for our test repos
    async_db_sync = db
    async_db_sync.signals.delete_many(
        {"projectId": {"$in": [c["url"] for c in fake_candidates]}}
    )

    try:
        count = await tracker.track_daily_snapshots(async_db)
        assert count == 2

        stored = list(
            async_db_sync.signals.find(
                {"projectId": {"$in": [c["url"] for c in fake_candidates]}}
            )
        )
        assert len(stored) == 2
        for doc in stored:
            assert doc["postId"] == ""
            assert "github_stars" in doc
            assert "github_forks" in doc
            assert "capturedAt" in doc
    finally:
        async_db_sync.signals.delete_many(
            {"projectId": {"$in": [c["url"] for c in fake_candidates]}}
        )


@pytest.mark.asyncio
async def test_track_daily_snapshots_idempotent(db, monkeypatch):
    """Running twice on the same day does not duplicate snapshots."""
    from github_radar import tracker

    fake_candidates = [
        {
            "url": "https://github.com/test/idempotent-repo",
            "github_stars": 400,
            "github_forks": 60,
        },
    ]

    async def fake_search(client):
        return fake_candidates

    monkeypatch.setattr(tracker, "_search_candidates", fake_search)

    async def no_trending(client):
        return []

    monkeypatch.setattr(tracker, "_trending_candidates", no_trending)
    monkeypatch.setattr(tracker, "backfill_star_history", _no_backfill)

    async_db = mongo._get_db()
    async_db_sync = db
    async_db_sync.signals.delete_many(
        {"projectId": {"$in": [c["url"] for c in fake_candidates]}}
    )

    try:
        first_count = await tracker.track_daily_snapshots(async_db)
        assert first_count == 1

        second_count = await tracker.track_daily_snapshots(async_db)
        assert second_count == 0

        stored = list(
            async_db_sync.signals.find(
                {"projectId": "https://github.com/test/idempotent-repo"}
            )
        )
        assert len(stored) == 1
    finally:
        async_db_sync.signals.delete_many(
            {"projectId": {"$in": [c["url"] for c in fake_candidates]}}
        )


@pytest.mark.asyncio
async def test_track_daily_snapshots_empty_candidates(db, monkeypatch):
    """No candidates means zero snapshots and no errors."""
    from github_radar import tracker

    async def fake_search(client):
        return []

    monkeypatch.setattr(tracker, "_search_candidates", fake_search)

    async def no_trending(client):
        return []

    monkeypatch.setattr(tracker, "_trending_candidates", no_trending)
    monkeypatch.setattr(tracker, "backfill_star_history", _no_backfill)

    async_db = mongo._get_db()
    count = await tracker.track_daily_snapshots(async_db)
    assert count == 0


# ─── GitHub trending source ───


class _FakeResponse:
    def __init__(self, text="", json_data=None):
        self.text = text
        self._json = json_data

    def raise_for_status(self):
        pass

    def json(self):
        return self._json


class _FakeTrendingClient:
    """Serves the trending pages and repo details for tests."""

    def __init__(self, html, repos):
        self._html = html
        self._repos = repos

    async def get(self, url, headers=None):
        if "/trending" in url:
            return _FakeResponse(text=self._html)
        full_name = url.rsplit("/repos/", 1)[-1]
        return _FakeResponse(json_data=self._repos.get(full_name))


@pytest.mark.asyncio
async def test_trending_candidates_parses_stargazers_links():
    """Trending repos are extracted from stargazers links, deduped, and
    enriched via the REST repo endpoint."""
    from github_radar import tracker

    html = (
        '<article><a href="/owner/one/stargazers">stars</a></article>'
        '<article><a href="/owner/two/stargazers">stars</a></article>'
        '<article><a href="/owner/one/stargazers">stars</a></article>'
    )
    repos = {
        "owner/one": {
            "html_url": "https://github.com/owner/one",
            "stargazers_count": 100,
            "forks_count": 10,
        },
        "owner/two": {
            "html_url": "https://github.com/owner/two",
            "stargazers_count": 200,
            "forks_count": 20,
        },
    }
    result = await tracker._trending_candidates(_FakeTrendingClient(html, repos))

    assert [c["url"] for c in result] == [
        "https://github.com/owner/one",
        "https://github.com/owner/two",
    ]
    assert result[0]["github_stars"] == 100
    assert result[0]["github_forks"] == 10


@pytest.mark.asyncio
async def test_trending_candidates_survives_page_failure(caplog):
    """A failed trending fetch logs a warning and returns what it can."""
    from github_radar import tracker

    class _BoomClient:
        async def get(self, *a, **kw):
            raise RuntimeError("network down")

    with caplog.at_level(logging.WARNING, logger="root"):
        result = await tracker._trending_candidates(_BoomClient())

    assert result == []
    assert any("trending fetch failed" in r.message for r in caplog.records)


@pytest.mark.asyncio
async def test_track_daily_snapshots_merges_trending_after_search(db, monkeypatch):
    """Trending repos join the tracked pool after the search band, deduped."""
    from github_radar import tracker

    search_repo = {
        "url": "https://github.com/test/search-repo",
        "github_stars": 300,
        "github_forks": 30,
    }
    overlap = dict(search_repo)  # found by both paths — must be tracked once
    trending_only = {
        "url": "https://github.com/test/trending-only",
        "github_stars": 900,
        "github_forks": 90,
    }

    async def fake_search(client):
        return [search_repo]

    monkeypatch.setattr(tracker, "_search_candidates", fake_search)

    async def fake_trending(client):
        return [overlap, trending_only]

    monkeypatch.setattr(tracker, "_trending_candidates", fake_trending)
    monkeypatch.setattr(tracker, "backfill_star_history", _no_backfill)

    async_db = mongo._get_db()
    urls = [search_repo["url"], trending_only["url"]]
    db.signals.delete_many({"projectId": {"$in": urls}})
    try:
        count = await tracker.track_daily_snapshots(async_db)
        assert count == 2, "search repo + trending-only repo; overlap deduped"
        stored = list(db.signals.find({"projectId": {"$in": urls}}))
        assert len(stored) == 2
    finally:
        db.signals.delete_many({"projectId": {"$in": urls}})


@pytest.mark.asyncio
async def test_track_daily_snapshots_backfills_cold_start_repo(db, monkeypatch):
    """A repo seen for the first time gets today's snapshot PLUS a
    star-history backfill — scoreable from day one instead of after 7+ days
    of our own snapshots."""
    from github_radar import tracker

    candidate = {
        "url": "https://github.com/test/cold-start-repo",
        "github_stars": 250,
        "github_forks": 25,
    }

    async def fake_search(client):
        return [candidate]

    monkeypatch.setattr(tracker, "_search_candidates", fake_search)

    async def no_trending(client):
        return []

    monkeypatch.setattr(tracker, "_trending_candidates", no_trending)

    backfill_calls = []

    async def fake_backfill(db_, project_id, current_stars, current_forks):
        backfill_calls.append((project_id, current_stars, current_forks))
        return 5  # pretend GitHub returned five weeks of history

    monkeypatch.setattr(tracker, "backfill_star_history", fake_backfill)

    async_db = mongo._get_db()
    db.signals.delete_many({"projectId": candidate["url"]})
    try:
        count = await tracker.track_daily_snapshots(async_db)
        assert count == 6, "1 snapshot for today + 5 backfilled days"
        assert backfill_calls == [
            (candidate["url"], candidate["github_stars"], candidate["github_forks"])
        ]
    finally:
        db.signals.delete_many({"projectId": candidate["url"]})
