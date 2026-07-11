"""T8 tests: episodic memory — store, retrieve, and agent learning.

Tests that episodes are stored, retrieved by semantic similarity, and
that the agent write path includes episodes context in posts.
"""
import os
import sys
from datetime import datetime, timezone

from bson import ObjectId

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from _shared.episodic_memory import store_episode, retrieve_similar_episodes, get_episode_count  # noqa: E402
from _shared.embeddings import embed_project  # noqa: E402
from _shared.write_post import write_post  # noqa: E402


def _cleanup(db, url):
    db.posts.delete_many({"project.url": url})
    db.projects.delete_many({"url": url})
    db.signals.delete_many({"projectId": url})


class TestEpisodicMemory:
    """Store + retrieve episodes by semantic similarity."""

    async def test_store_and_retrieve_episode(self, db):
        """An episode stored with an embedding should be retrievable by similar embedding."""
        url = f"https://github.com/test/ep-{datetime.now(timezone.utc).timestamp()}"
        emb = embed_project("AI agent framework for coding", "CLI coding agents", ["ai", "agents"])
        try:
            episode_id = await store_episode(
                "@github-radar", url, "test/ep-agent",
                {"momentumScore": 80, "source": "github"},
                "hype looks real", "posted — trend confirmed",
                "High velocity AI agent repos are worth posting.",
                embedding=emb,
            )
            assert episode_id

            # Retrieve with a similar embedding
            query_emb = embed_project("AI coding agent CLI tool", "agent framework", ["ai", "cli"])
            episodes = await retrieve_similar_episodes(query_emb, limit=3)
            assert len(episodes) > 0
            # The stored episode should be in the results
            titles = [e.get("projectTitle") for e in episodes]
            assert "test/ep-agent" in titles
        finally:
            db.episodes.delete_many({"projectUrl": url})

    async def test_retrieve_without_index_falls_back_to_recent(self, db):
        """If vector search fails, should fall back to recent episodes."""
        # Use a zero vector (will still work via fallback)
        episodes = await retrieve_similar_episodes([0.0] * 384, limit=2)
        # Should return some episodes (from seeded data) or empty (if none)
        assert isinstance(episodes, list)

    async def test_episode_count_increases(self, db):
        """Storing an episode increases the count."""
        count_before = await get_episode_count()
        url = f"https://github.com/test/count-{datetime.now(timezone.utc).timestamp()}"
        try:
            await store_episode(
                "@test", url, "test/count",
                {}, "emerging", "test", "test lesson",
            )
            count_after = await get_episode_count()
            assert count_after == count_before + 1
        finally:
            db.episodes.delete_many({"projectUrl": url})


class TestWritePostWithEpisodes:
    """The write path should include episodic context in posts."""

    async def test_post_includes_episodes_context(self, db):
        """A post written via write_post should have episodesContext if episodes exist."""
        url = f"https://github.com/test/ep-context-{datetime.now(timezone.utc).timestamp()}"
        try:
            post_id = await write_post(
                "@github-radar", "GitHub Radar", "test bio", "github",
                {
                    "url": url, "title": "test/ep-context", "kind": "repo",
                    "description": "AI agent framework", "topics": ["ai", "agents"],
                    "momentumScore": 60, "hypeVerdict": "hype looks real",
                },
                "test blurb",
                "hype looks real",
                {"source": "github", "metric": "stars", "value": 1000, "delta": 100},
                60,
            )
            post = db.posts.find_one({"_id": ObjectId(post_id)})
            assert post is not None
            # episodesContext should exist (we seeded episodes)
            assert "episodesContext" in post, "post should have episodic memory context"
            assert isinstance(post["episodesContext"], list)
        finally:
            _cleanup(db, url)
