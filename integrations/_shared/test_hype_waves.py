"""T6 tests: hype wave clustering.

Tests the clustering seam: projects with similar embeddings group together,
Grove labels clusters, results store in digests.
"""

import os
import sys
from datetime import datetime, timezone

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from _shared import hype_waves  # noqa: E402
from _shared.hype_waves import (  # noqa: E402
    _cosine_sim,
    _distinct_agent_handles,
    _recent_source_post_filter,
    cluster_projects,
)


class TestCosineSimilarity:
    """The math behind clustering."""

    def test_identical_vectors_have_similarity_1(self):
        v = [1.0, 0.5, 0.3]
        assert _cosine_sim(v, v) == pytest.approx(1.0, abs=0.01)

    def test_orthogonal_vectors_have_similarity_0(self):
        a = [1.0, 0.0]
        b = [0.0, 1.0]
        assert _cosine_sim(a, b) == pytest.approx(0.0, abs=0.01)

    def test_zero_vector_returns_0(self):
        assert _cosine_sim([0, 0, 0], [1, 2, 3]) == 0.0


class TestClusterProjects:
    """The greedy clustering by embedding similarity."""

    def test_similar_projects_group_together(self):
        """Two projects with near-identical embeddings should be in one cluster."""
        emb = [0.9, 0.1, 0.0] * 128  # 384-dim
        projects = [
            {
                "title": "agent-a",
                "url": "https://github.com/a/agent-a",
                "embedding": emb,
            },
            {
                "title": "agent-b",
                "url": "https://github.com/b/agent-b",
                "embedding": emb,
            },
        ]
        clusters = cluster_projects(projects, threshold=0.7)
        assert len(clusters) == 1, "similar projects should be in one cluster"
        assert len(clusters[0]) == 2

    def test_dissimilar_projects_separate(self):
        """Two projects with orthogonal embeddings should be in separate clusters."""
        projects = [
            {
                "title": "agent",
                "url": "https://github.com/a/agent",
                "embedding": [1.0] + [0.0] * 383,
            },
            {
                "title": "database",
                "url": "https://github.com/b/db",
                "embedding": [0.0] * 383 + [1.0],
            },
        ]
        clusters = cluster_projects(projects, threshold=0.7)
        assert len(clusters) == 2, "dissimilar projects should be in separate clusters"

    def test_empty_projects_returns_empty(self):
        assert cluster_projects([], threshold=0.7) == []

    def test_projects_without_embeddings_skipped(self):
        projects = [
            {"title": "no-emb", "url": "https://github.com/a/no-emb"},  # no embedding
            {
                "title": "has-emb",
                "url": "https://github.com/b/has-emb",
                "embedding": [0.5] * 384,
            },
        ]
        clusters = cluster_projects(projects, threshold=0.7)
        assert len(clusters) == 1
        assert clusters[0][0]["title"] == "has-emb"

    def test_confirmation_counts_distinct_agents_not_projects(self):
        cluster = [
            {"url": "https://example.com/a"},
            {"url": "https://example.com/b"},
            {"url": "https://example.com/c"},
        ]
        agents_by_project = {
            "https://example.com/a": {"@youtube-trends"},
            "https://example.com/b": {"@youtube-trends"},
            "https://example.com/c": {"@reddit-pulse"},
        }

        assert _distinct_agent_handles(cluster, agents_by_project) == [
            "@reddit-pulse",
            "@youtube-trends",
        ]

    def test_wave_membership_uses_only_recent_source_agents(self):
        from _shared.agent_catalog import AGENT_CATALOG

        since = datetime(2026, 7, 6, tzinfo=timezone.utc)
        query = _recent_source_post_filter(since)

        expected_source_agents = [
            agent["handle"]
            for agent in AGENT_CATALOG
            if agent["source_type"] != "aggregator"
        ]
        assert query["postedAt"] == {"$gte": since}
        assert query["agentHandle"] == {"$in": expected_source_agents}
        assert "@weekly-digest" not in query["agentHandle"]["$in"]
        assert query["portSyncStatus"] == "synced"
        assert query["evidenceContractVersion"] == 2


class TestHypeWavesStorage:
    """compute_hype_waves stores results in the digests collection."""

    def test_waves_stored_in_digests(self, db):
        """After computing waves, a digest doc with waves should exist."""
        digest = db.digests.find_one({"waves": {"$exists": True}})
        if not digest:
            pytest.skip("no digest with waves — run compute_hype_waves first")
        assert "waves" in digest
        assert isinstance(digest["waves"], list)
        assert "weekId" in digest, "digest should have weekId for /digest/[week] lookup"
        for wave in digest["waves"]:
            assert "label" in wave
            assert "projects" in wave
            assert "avgMomentum" in wave


@pytest.mark.asyncio
async def test_compute_hype_waves_reuses_the_shared_client_without_closing_it(
    monkeypatch,
):
    """compute_hype_waves goes through the shared async client and never closes it."""
    from _shared import mongo as _mongo

    class ProjectsCursor:
        def sort(self, *_args):
            return self

        def limit(self, *_args):
            return self

        async def to_list(self, *_args, **_kwargs):
            return []

    class Posts:
        async def distinct(self, *_args, **_kwargs):
            return []

    class Projects:
        def find(self, *_args, **_kwargs):
            return ProjectsCursor()

    class Database:
        posts = Posts()
        projects = Projects()

    closed = []

    async def record_close():
        closed.append(True)

    monkeypatch.setattr(_mongo, "_get_db", lambda: Database())
    monkeypatch.setattr(_mongo, "close_client", record_close)

    assert await hype_waves.compute_hype_waves() == []
    assert closed == []


@pytest.mark.asyncio
async def test_computed_waves_are_private_until_the_digest_port_twin_is_synced(
    monkeypatch,
):
    project_url = "https://example.com/agent-memory"
    updates = []

    class PostsCursor:
        def __init__(self, docs):
            self._docs = docs

        def __aiter__(self):
            self._iterator = iter(self._docs)
            return self

        async def __anext__(self):
            try:
                return next(self._iterator)
            except StopIteration:
                raise StopAsyncIteration from None

    class Posts:
        async def distinct(self, *_args, **_kwargs):
            return [project_url]

        def find(self, *_args, **_kwargs):
            return PostsCursor(
                [
                    {
                        "agentHandle": "@github-radar",
                        "project": {"url": project_url},
                    }
                ]
            )

    class ProjectsCursor:
        def __init__(self, docs):
            self._docs = docs

        def sort(self, *_args):
            return self

        def limit(self, *_args):
            return self

        async def to_list(self, *_args, **_kwargs):
            return self._docs

    class Projects:
        def find(self, *_args, **_kwargs):
            return ProjectsCursor(
                [
                    {
                        "title": "Agent Memory",
                        "url": project_url,
                        "embedding": [1.0, 0.0],
                        "momentumScore": 72.5,
                    }
                ]
            )

    class Digests:
        async def update_one(self, *_args, **kwargs):
            updates.append(kwargs.get("update") or _args[1])

    class Database:
        posts = Posts()
        projects = Projects()
        digests = Digests()

    async def fake_label(_cluster):
        return "agent memory"

    monkeypatch.setattr(hype_waves, "label_cluster", fake_label)

    waves = await hype_waves._compute_hype_waves(
        Database(), datetime(2026, 7, 13, tzinfo=timezone.utc)
    )

    assert waves[0]["label"] == "agent memory"
    assert updates[-1]["$set"]["publicationSyncStatus"] == "pending"
