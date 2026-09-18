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


def _bypass_corroboration(monkeypatch):
    """These tests exercise the legacy gates; the corroboration gate (which
    calls live HN/Reddit) has its own hermetic suite in _shared."""

    async def pass_through(candidates, **kwargs):
        return candidates

    monkeypatch.setattr(agent, "corroborated_candidates", pass_through)


@pytest.mark.asyncio
async def test_legacy_path_filters_fake_stars(monkeypatch):
    """Legacy fallback applies passes_fake_star_filter to candidates."""

    _bypass_corroboration(monkeypatch)

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

    _bypass_corroboration(monkeypatch)

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


@pytest.mark.asyncio
async def test_momentum_pool_emptied_by_corroboration_does_not_fall_back(monkeypatch):
    """A momentum pool the corroboration gate empties is reported as-is.

    Regression guard: the gate's "nothing reached consensus today" outcome
    must NOT fall through to the weaker legacy bar — the momentum gate has
    already rejected those repos once today.
    """
    mock_db = MagicMock()
    monkeypatch.setattr(agent.mongo, "_get_db", lambda: mock_db)

    candidate = _candidate("https://github.com/test/gated-repo", 100, 10)

    async def momentum_candidates(db):
        return [candidate]

    monkeypatch.setattr(
        agent, "fetch_trending_candidates_with_momentum", momentum_candidates
    )

    async def drop_everything(candidates, **kwargs):
        return []  # the gate empties the pool

    monkeypatch.setattr(agent, "corroborated_candidates", drop_everything)

    legacy_called = []

    async def legacy_fetch(max_results=10):
        legacy_called.append(max_results)
        return [candidate]

    monkeypatch.setattr(agent, "fetch_trending_candidates", legacy_fetch)

    result = await agent.fetch_trending_repos.ainvoke({})

    assert "cross-source corroboration" in result
    assert legacy_called == [], "legacy fallback must not run for a gated pool"


# ─── write_hype_post cross-agent cooldown guard ───


@pytest.mark.asyncio
async def test_write_hype_post_guard_blocks_recently_posted_repo(monkeypatch):
    """The write tool is the last choke point: a repo posted 2 days ago by ANY
    agent must be refused even if it re-entered the candidate cache."""

    mock_db = MagicMock()

    async def mock_find_one(query, *args, **kwargs):
        return {"postedAt": datetime.now(timezone.utc) - timedelta(days=2)}

    mock_db.posts.find_one = mock_find_one
    monkeypatch.setattr(agent.mongo, "_get_db", lambda: mock_db)

    repo_url = "https://github.com/test/guard-repo"
    agent._CANDIDATE_CACHE[repo_url] = _candidate(repo_url, 100, 10)

    try:
        result = await agent.write_hype_post.ainvoke(
            {"repo_url": repo_url, "verdict": "hype looks real"}
        )
        assert result.startswith("SKIP")
        assert "cooldown" in result.lower()
    finally:
        agent._CANDIDATE_CACHE.pop(repo_url, None)


@pytest.mark.asyncio
async def test_write_hype_post_guard_blocks_based_on_newest_post(monkeypatch):
    """Guard regression: with an old AND a recent post for the repo, the recent
    one decides (the pre-fix unsorted lookup would have let it through)."""

    mock_db = MagicMock()

    async def mock_find_one(query, *args, **kwargs):
        # Emulate the sorted lookup: newest first.
        return {"postedAt": datetime.now(timezone.utc) - timedelta(days=1)}

    mock_db.posts.find_one = mock_find_one
    monkeypatch.setattr(agent.mongo, "_get_db", lambda: mock_db)

    repo_url = "https://github.com/test/guard-newest-repo"
    agent._CANDIDATE_CACHE[repo_url] = _candidate(repo_url, 100, 10)

    try:
        result = await agent.write_hype_post.ainvoke(
            {"repo_url": repo_url, "verdict": "emerging"}
        )
        assert result.startswith("SKIP")
    finally:
        agent._CANDIDATE_CACHE.pop(repo_url, None)
