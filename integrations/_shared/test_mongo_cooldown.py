"""Regression tests for the cross-agent republish cooldown lookup.

The original bug: ``find_one`` without a sort returns an arbitrary post in
natural order — usually the OLDEST. A repo posted 45 days ago and again
yesterday therefore looked "fresh" and was re-published daily by every agent.
``get_last_published_days`` must sort by ``postedAt`` descending so the newest
publication decides the cooldown.
"""

from datetime import datetime, timedelta, timezone

import pytest

from _shared import mongo
from _shared.momentum import _REPUBLISH_COOLDOWN_DAYS

_REPO = "https://github.com/test/cooldown-repo"


def _post(days_ago: int, agent="@github-radar") -> dict:
    return {
        "agentHandle": agent,
        "body": "test post",
        "postedAt": datetime.now(timezone.utc) - timedelta(days=days_ago),
        "project": {"url": _REPO, "title": "Cooldown Repo"},
        "portSyncStatus": "synced",
    }


@pytest.mark.asyncio
async def test_never_posted_returns_999(db):
    db.posts.delete_many({"project.url": _REPO})
    async_db = mongo._get_db()
    try:
        assert await mongo.get_last_published_days(async_db, _REPO) == 999
    finally:
        db.posts.delete_many({"project.url": _REPO})


@pytest.mark.asyncio
async def test_single_post_counts_from_that_post(db):
    db.posts.delete_many({"project.url": _REPO})
    db.posts.insert_one(_post(20))
    async_db = mongo._get_db()
    try:
        days = await mongo.get_last_published_days(async_db, _REPO)
        assert days >= _REPUBLISH_COOLDOWN_DAYS
    finally:
        db.posts.delete_many({"project.url": _REPO})


@pytest.mark.asyncio
async def test_multiple_posts_uses_newest_not_oldest(db):
    """THE regression: an old post AND a recent post → the recent one wins.

    With the old unsorted ``find_one`` this returned ~45 (the August post),
    silently passing the 14-day cooldown for a repo posted yesterday.
    """
    db.posts.delete_many({"project.url": _REPO})
    db.posts.insert_many([_post(45), _post(1)])
    async_db = mongo._get_db()
    try:
        days = await mongo.get_last_published_days(async_db, _REPO)
        assert days == 1, (
            f"Cooldown must count from the NEWEST post (expected 1, got {days})"
        )
    finally:
        db.posts.delete_many({"project.url": _REPO})


@pytest.mark.asyncio
async def test_cooldown_counts_posts_from_all_agents(db):
    """A post by @github-radar must cool down @hidden-gems too (cross-agent)."""
    db.posts.delete_many({"project.url": _REPO})
    db.posts.insert_one(_post(3, agent="@github-radar"))
    async_db = mongo._get_db()
    try:
        days = await mongo.get_last_published_days(async_db, _REPO)
        assert days < _REPUBLISH_COOLDOWN_DAYS
    finally:
        db.posts.delete_many({"project.url": _REPO})
