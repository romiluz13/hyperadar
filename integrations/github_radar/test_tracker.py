"""Tests for the github-radar daily snapshot tracker."""

import pytest

from _shared import mongo


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

    async_db = mongo._get_db()
    count = await tracker.track_daily_snapshots(async_db)
    assert count == 0
