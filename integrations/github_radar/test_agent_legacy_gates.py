"""Tests for the legacy fallback path in fetch_trending_repos — fake-star filter and 7-day cooldown.

When the momentum path returns no candidates (e.g. tracker just deployed, no
7-day history yet), the legacy fallback in agent.py must still apply:
1. passes_fake_star_filter — reject repos with suspicious fork/star ratios
2. A 7-day cooldown — skip repos posted < 7 days ago
"""

from datetime import datetime, timedelta, timezone
from unittest.mock import MagicMock

import agent
import pytest


def _candidate(url: str, stars: int, forks: int) -> dict:
    return {
        "url": url,
        "title": url.rsplit("/", 1)[-1],
        "kind": "repo",
        "description": "A test repo",
        "topics": ["ai"],
        "stars": stars,
        "forks": forks,
    }


@pytest.mark.asyncio
async def test_legacy_path_filters_fake_stars(monkeypatch):
    """Legacy fallback applies passes_fake_star_filter to candidates."""

    async def empty_momentum(db):
        return []

    monkeypatch.setattr(
        agent, "fetch_trending_candidates_with_momentum", empty_momentum
    )

    healthy = _candidate("https://github.com/test/healthy", 100, 10)  # ratio 0.1
    fake = _candidate("https://github.com/test/fake", 1000, 1)  # ratio 0.001

    async def mock_fetch(max_results=10):
        return [healthy, fake]

    monkeypatch.setattr(agent, "fetch_trending_candidates", mock_fetch)

    # No DB available — cooldown skipped, but fake-star filter still applies
    def raise_db():
        raise Exception("no db")

    monkeypatch.setattr(agent.mongo, "_get_db", raise_db)

    async def empty_history(*args, **kwargs):
        return []

    monkeypatch.setattr(agent.mongo, "get_momentum_history", empty_history)

    async def zero_posts(*args, **kwargs):
        return 0

    monkeypatch.setattr(agent.mongo, "get_prior_post_count", zero_posts)

    result = await agent.fetch_trending_repos.ainvoke({})

    assert "healthy" in result
    assert "fake" not in result


@pytest.mark.asyncio
async def test_legacy_path_applies_cooldown(monkeypatch):
    """Legacy fallback skips repos posted < 7 days ago."""

    mock_db = MagicMock()

    async def mock_find_one(query, *args, **kwargs):
        url = query.get("project.url", "")
        if "repo-a" in url:
            return {"postedAt": datetime.now(timezone.utc) - timedelta(days=2)}
        return None

    mock_db.posts.find_one = mock_find_one
    monkeypatch.setattr(agent.mongo, "_get_db", lambda: mock_db)

    async def empty_momentum(db):
        return []

    monkeypatch.setattr(
        agent, "fetch_trending_candidates_with_momentum", empty_momentum
    )

    repo_a = _candidate("https://github.com/test/repo-a", 100, 10)
    repo_b = _candidate("https://github.com/test/repo-b", 200, 20)

    async def mock_fetch(max_results=10):
        return [repo_a, repo_b]

    monkeypatch.setattr(agent, "fetch_trending_candidates", mock_fetch)

    async def empty_history(*args, **kwargs):
        return []

    monkeypatch.setattr(agent.mongo, "get_momentum_history", empty_history)

    async def zero_posts(*args, **kwargs):
        return 0

    monkeypatch.setattr(agent.mongo, "get_prior_post_count", zero_posts)

    result = await agent.fetch_trending_repos.ainvoke({})

    assert "repo-b" in result
    assert "repo-a" not in result
