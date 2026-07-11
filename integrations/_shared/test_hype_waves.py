"""T6 tests: hype wave clustering.

Tests the clustering seam: projects with similar embeddings group together,
Grove labels clusters, results store in digests.
"""

import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from _shared.hype_waves import cluster_projects, _cosine_sim  # noqa: E402


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
