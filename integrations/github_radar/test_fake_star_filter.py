"""Unit tests for fake-star filtering in fetch_trending_candidates.

Tests that the fake-star filter (fork/star ratio < 0.02 is suspicious) is
applied to both OSSInsight and Search API fallback paths, and that
candidates without fork data are NOT falsely rejected.
"""

import pytest


class _FakeResponse:
    def __init__(self, data, status=200):
        self._data = data
        self.status_code = status

    def json(self):
        return self._data

    def raise_for_status(self):
        if self.status_code != 200:
            raise RuntimeError(f"HTTP {self.status_code}")


class _FakeClient:
    """Reusable httpx mock. Override `.responses` per-test."""

    def __init__(self, **_kw):
        pass

    async def __aenter__(self):
        return self

    async def __aexit__(self, *a):
        pass

    async def get(self, url, **kw):
        raise NotImplementedError


@pytest.mark.asyncio
async def test_search_api_rejects_suspicious_fork_star_ratio(monkeypatch):
    """Search API fallback filters out repos with fork/star ratio < 0.02."""
    import github_source

    monkeypatch.setattr(github_source, "_token", "fake-token")
    monkeypatch.setattr(
        github_source, "_headers", {"Authorization": "token fake-token"}
    )

    class SearchClient(_FakeClient):
        async def get(self, url, **kw):
            if "ossinsight" in url:
                # OSSInsight fails so we hit the Search API fallback
                raise RuntimeError("OSSInsight down")
            # GitHub Search API
            return _FakeResponse(
                {
                    "items": [
                        {
                            "html_url": "https://github.com/fake/suspicious",
                            "full_name": "fake/suspicious",
                            "description": "An AI agent framework",
                            "topics": ["ai"],
                            "stargazers_count": 10_000,
                            "forks_count": 10,  # ratio 0.001 < 0.02 → suspicious
                            "created_at": "2026-06-01",
                            "pushed_at": "2026-07-19",
                            "language": "Python",
                            "owner": {"login": "fake"},
                            "name": "suspicious",
                        },
                        {
                            "html_url": "https://github.com/fake/healthy",
                            "full_name": "fake/healthy",
                            "description": "An AI agent framework",
                            "topics": ["ai"],
                            "stargazers_count": 1000,
                            "forks_count": 500,  # ratio 0.5 → healthy
                            "created_at": "2026-06-01",
                            "pushed_at": "2026-07-19",
                            "language": "Python",
                            "owner": {"login": "fake"},
                            "name": "healthy",
                        },
                    ]
                }
            )

    monkeypatch.setattr(github_source.httpx, "AsyncClient", SearchClient)
    candidates = await github_source.fetch_trending_candidates(max_results=10)

    titles = [c["title"] for c in candidates]
    assert "fake/healthy" in titles
    assert "fake/suspicious" not in titles


@pytest.mark.asyncio
async def test_ossinsight_passes_through_missing_forks(monkeypatch):
    """OSSInsight candidates without fork data are NOT rejected."""
    import github_source

    monkeypatch.setattr(github_source, "_token", "fake-token")
    monkeypatch.setattr(
        github_source, "_headers", {"Authorization": "token fake-token"}
    )

    class OssinsightClient(_FakeClient):
        async def get(self, url, **kw):
            if "ossinsight" in url:
                return _FakeResponse(
                    {
                        "data": {
                            "rows": [
                                {
                                    "repo_name": "aiuser/airepo",
                                    "description": "AI agent",
                                    "stars": 6,
                                    "primary_language": "Python",
                                }
                            ]
                        }
                    }
                )
            # GitHub API for enrichment — no forks_count in response
            if "aiuser/airepo" in url:
                return _FakeResponse(
                    {
                        "topics": ["ai"],
                        "description": "AI agent",
                        "stargazers_count": 500,
                        "created_at": "2026-06-01",
                        "pushed_at": "2026-07-19",
                        # forks_count intentionally absent
                    }
                )
            return _FakeResponse({}, status=404)

    monkeypatch.setattr(github_source.httpx, "AsyncClient", OssinsightClient)
    candidates = await github_source.fetch_trending_candidates(max_results=5)
    titles = [c["title"] for c in candidates]
    assert "aiuser/airepo" in titles  # not rejected despite no fork data


@pytest.mark.asyncio
async def test_ossinsight_rejects_suspicious_fork_star_ratio(monkeypatch):
    """OSSInsight candidates with bad fork/star ratio are filtered out."""
    import github_source

    monkeypatch.setattr(github_source, "_token", "fake-token")
    monkeypatch.setattr(
        github_source, "_headers", {"Authorization": "token fake-token"}
    )

    class OssinsightClient(_FakeClient):
        async def get(self, url, **kw):
            if "ossinsight" in url:
                return _FakeResponse(
                    {
                        "data": {
                            "rows": [
                                {
                                    "repo_name": "aiuser/suspicious",
                                    "description": "AI agent",
                                    "stars": 6,
                                    "primary_language": "Python",
                                },
                                {
                                    "repo_name": "aiuser/healthy",
                                    "description": "AI tool",
                                    "stars": 5,
                                    "primary_language": "Python",
                                },
                            ]
                        }
                    }
                )
            if "aiuser/suspicious" in url:
                return _FakeResponse(
                    {
                        "topics": ["ai"],
                        "description": "AI agent",
                        "stargazers_count": 10_000,
                        "forks_count": 10,  # ratio 0.001 → suspicious
                        "created_at": "2026-06-01",
                        "pushed_at": "2026-07-19",
                    }
                )
            if "aiuser/healthy" in url:
                return _FakeResponse(
                    {
                        "topics": ["ai"],
                        "description": "AI tool",
                        "stargazers_count": 1000,
                        "forks_count": 500,  # ratio 0.5 → healthy
                        "created_at": "2026-06-01",
                        "pushed_at": "2026-07-19",
                    }
                )
            return _FakeResponse({}, status=404)

    monkeypatch.setattr(github_source.httpx, "AsyncClient", OssinsightClient)
    candidates = await github_source.fetch_trending_candidates(max_results=5)
    titles = [c["title"] for c in candidates]
    assert "aiuser/healthy" in titles
    assert "aiuser/suspicious" not in titles
