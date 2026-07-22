"""Tests for the hidden-gems daily snapshot tracker."""

import logging

import pytest

from _shared import mongo


@pytest.mark.asyncio
async def test_search_candidates_logs_warning_on_exception(caplog):
    """A failed GitHub search logs a warning, not a silent swallow."""
    from hidden_gems import tracker

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
    from hidden_gems import tracker

    fake_candidates = [
        {
            "url": "https://github.com/test/mock-ai-repo",
            "github_stars": 42,
            "github_forks": 5,
        },
        {
            "url": "https://github.com/test/mock-llm-tool",
            "github_stars": 100,
            "github_forks": 12,
        },
    ]

    async def fake_search(client):
        return fake_candidates

    monkeypatch.setattr(tracker, "_search_candidates", fake_search)

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
    from hidden_gems import tracker

    fake_candidates = [
        {
            "url": "https://github.com/test/idempotent-repo",
            "github_stars": 33,
            "github_forks": 3,
        },
    ]

    async def fake_search(client):
        return fake_candidates

    monkeypatch.setattr(tracker, "_search_candidates", fake_search)

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
    from hidden_gems import tracker

    async def fake_search(client):
        return []

    monkeypatch.setattr(tracker, "_search_candidates", fake_search)

    async_db = mongo._get_db()
    count = await tracker.track_daily_snapshots(async_db)
    assert count == 0
