"""Tests for rate limiting and retry in _fetch_ossinsight_trending."""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest


def _make_ossinsight_response(rows: list[dict]) -> MagicMock:
    resp = MagicMock()
    resp.raise_for_status.return_value = None
    resp.json.return_value = {"data": {"rows": rows}}
    return resp


def _make_github_repo_response(
    topics: list[str] | None = None,
    stars: int = 500,
    forks: int = 50,
) -> MagicMock:
    resp = MagicMock()
    resp.status_code = 200
    resp.json.return_value = {
        "topics": topics or ["ai"],
        "description": "AI tool",
        "stargazers_count": stars,
        "forks_count": forks,
        "created_at": "2025-01-01T00:00:00Z",
        "pushed_at": "2025-07-01T00:00:00Z",
    }
    return resp


@pytest.mark.asyncio
async def test_ossinsight_sleeps_between_per_repo_api_calls():
    """Rate limiting: asyncio.sleep is called between per-repo GitHub API calls."""
    from github_radar import github_source

    ossinsight_rows = [
        {
            "repo_name": f"owner{i}/repo{i}",
            "description": "AI tool",
            "stars": 100 + i,
            "primary_language": "Python",
        }
        for i in range(2)
    ]

    def mock_get(url, **kwargs):
        if "api.ossinsight.io" in url:
            return _make_ossinsight_response(ossinsight_rows)
        return _make_github_repo_response()

    mock_client = AsyncMock()
    mock_client.__aenter__.return_value = mock_client
    mock_client.get = AsyncMock(side_effect=mock_get)

    with (
        patch("httpx.AsyncClient", return_value=mock_client),
        patch(
            "github_radar.github_source.asyncio.sleep",
            new_callable=AsyncMock,
        ) as mock_sleep,
    ):
        await github_source._fetch_ossinsight_trending(10)

    # sleep should be called at least once between per-repo calls
    assert mock_sleep.call_count >= 1, (
        "asyncio.sleep must be called between per-repo GitHub API calls for rate limiting"
    )


@pytest.mark.asyncio
async def test_ossinsight_retries_on_429():
    """Retry: a 429 response is retried, not silently dropped."""
    from github_radar import github_source

    call_count = 0

    def mock_get(url, **kwargs):
        nonlocal call_count
        if "api.ossinsight.io" in url:
            return _make_ossinsight_response(
                [
                    {
                        "repo_name": "owner1/repo1",
                        "description": "AI tool",
                        "stars": 100,
                        "primary_language": "Python",
                    }
                ]
            )
        call_count += 1
        if call_count == 1:
            resp = MagicMock()
            resp.status_code = 429
            return resp
        return _make_github_repo_response()

    mock_client = AsyncMock()
    mock_client.__aenter__.return_value = mock_client
    mock_client.get = AsyncMock(side_effect=mock_get)

    with (
        patch("httpx.AsyncClient", return_value=mock_client),
        patch(
            "github_radar.github_source.asyncio.sleep",
            new_callable=AsyncMock,
        ),
    ):
        results = await github_source._fetch_ossinsight_trending(10)

    # The retry should succeed on the second attempt and return a result.
    assert len(results) == 1, "Should retry on 429 and eventually succeed"
    assert call_count == 2, "Should make 2 per-repo calls (first 429, second success)"
