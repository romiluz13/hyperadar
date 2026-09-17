"""Tests for the write-time cross-agent cooldown guard in @hidden-gems.

``write_hidden_gem`` is the last choke point before a post is claimed: even if
a repo or HN story re-entered the candidate cache, it must be refused inside
the republish window — regardless of which agent posted it.
"""

import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import MagicMock

import pytest

# hidden_gems/agent.py imports its siblings as top-level modules
# (``from source import ...``), matching how the runner launches it.
sys.path.insert(0, str(Path(__file__).parent))

from hidden_gems import agent  # noqa: E402


@pytest.mark.asyncio
async def test_write_hidden_gem_guard_blocks_recently_posted_url(monkeypatch):
    mock_db = MagicMock()

    async def mock_find_one(query, *args, **kwargs):
        return {"postedAt": datetime.now(timezone.utc) - timedelta(days=2)}

    mock_db.posts.find_one = mock_find_one
    monkeypatch.setattr(agent, "_get_db", lambda: mock_db)

    gem_url = "https://github.com/test/guard-gem"
    agent._CANDIDATE_CACHE[gem_url] = {
        "url": gem_url,
        "title": "guard-gem",
        "kind": "repo",
        "description": "",
        "topics": ["ai", "hidden-gem"],
        "discovery_source": "breakout",
        "evidence_url": gem_url,
        "github_stars": 100,
        "github_forks": 10,
        "momentumScore": 70,
        "velocity": 8,
        "acceleration": 2,
    }

    try:
        result = await agent.write_hidden_gem.ainvoke(
            {"gem_url": gem_url, "verdict": "emerging"}
        )
        assert result.startswith("SKIP")
        assert "cooldown" in result.lower()
    finally:
        agent._CANDIDATE_CACHE.pop(gem_url, None)


@pytest.mark.asyncio
async def test_write_hidden_gem_guard_blocks_hn_story_posted_by_other_agent(
    monkeypatch,
):
    """Cross-agent: an HN story posted 3 days ago by @github-radar is refused."""

    mock_db = MagicMock()

    async def mock_find_one(query, *args, **kwargs):
        return {"postedAt": datetime.now(timezone.utc) - timedelta(days=3)}

    mock_db.posts.find_one = mock_find_one
    monkeypatch.setattr(agent, "_get_db", lambda: mock_db)

    hn_url = "https://news.ycombinator.com/item?id=12345678"
    agent._CANDIDATE_CACHE[hn_url] = {
        "url": hn_url,
        "title": "Show HN: Guard Test",
        "kind": "thread",
        "description": "",
        "topics": ["ai", "hidden-gem"],
        "discovery_source": "hacker_news",
        "evidence_url": hn_url,
        "hn_points": 120,
        "hn_comments": 30,
    }

    try:
        result = await agent.write_hidden_gem.ainvoke(
            {"gem_url": hn_url, "verdict": "emerging"}
        )
        assert result.startswith("SKIP")
    finally:
        agent._CANDIDATE_CACHE.pop(hn_url, None)


@pytest.mark.asyncio
async def test_write_hidden_gem_guard_allows_old_post(monkeypatch):
    """A post older than the cooldown window does not block the write: the
    guard passes through to the normal publish path."""

    mock_db = MagicMock()

    async def mock_find_one(query, *args, **kwargs):
        return {"postedAt": datetime.now(timezone.utc) - timedelta(days=30)}

    mock_db.posts.find_one = mock_find_one
    monkeypatch.setattr(agent, "_get_db", lambda: mock_db)

    async def mock_write_post(*args, **kwargs):
        return "post-123"

    monkeypatch.setattr(agent, "write_post", mock_write_post)

    gem_url = "https://github.com/test/guard-old-gem"
    agent._CANDIDATE_CACHE[gem_url] = {
        "url": gem_url,
        "title": "guard-old-gem",
        "kind": "repo",
        "description": "",
        "topics": ["ai", "hidden-gem"],
        "discovery_source": "breakout",
        "evidence_url": gem_url,
        "github_stars": 100,
        "github_forks": 10,
        "momentumScore": 70,
        "velocity": 8,
        "acceleration": 2,
    }

    try:
        result = await agent.write_hidden_gem.ainvoke(
            {"gem_url": gem_url, "verdict": "emerging"}
        )
        assert result.startswith("Posted"), (
            "A 30-day-old post is outside the cooldown window; the guard must "
            f"pass through to publish (got: {result!r})"
        )
    finally:
        agent._CANDIDATE_CACHE.pop(gem_url, None)
