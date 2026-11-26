"""GitHub's REST API for @github-radar: repo enrichment, README excerpts and the
search fallback used when the trending pages fail (ADR 0007, decision Q5).
"""

import asyncio
import base64
import binascii
import logging
import os
from dataclasses import dataclass
from datetime import datetime, timedelta

import httpx

_token = os.environ.get("GITHUB_TOKEN", "")
_headers = (
    {"Authorization": f"token {_token}", "Accept": "application/vnd.github+json"}
    if _token
    else {}
)

_RETRY_MAX_ATTEMPTS = 3
_README_EXCERPT_CHARS = 2_000
_SEARCH_URL = "https://api.github.com/search/repositories"
FALLBACK_TOPICS = ("ai", "llm", "agent")
FALLBACK_MAX_AGE = timedelta(days=90)
FALLBACK_MIN_STARS = 100
_FALLBACK_PER_QUERY = 30


class EnrichmentFailed(RuntimeError):
    """More than half of a run's repo lookups failed."""


@dataclass(frozen=True)
class Repo:
    slug: str
    url: str
    description: str
    topics: list[str]
    stars: int
    forks: int
    created_at: datetime


def _repo(data: dict) -> Repo:
    """A repo from a ``/repos/{o}/{r}`` answer or a search item (same fields)."""
    return Repo(
        slug=data["full_name"],
        url=data["html_url"],
        description=(data.get("description") or "").strip(),
        topics=data.get("topics") or [],
        stars=data["stargazers_count"],
        forks=data["forks_count"],
        created_at=datetime.fromisoformat(data["created_at"].replace("Z", "+00:00")),
    )


async def _get_with_retry(client: httpx.AsyncClient, url: str) -> httpx.Response | None:
    """GET a GitHub API URL with simple retry on 429/5xx (exponential backoff).

    Returns the response on success, or None if all retries are exhausted.
    """
    for attempt in range(_RETRY_MAX_ATTEMPTS):
        try:
            r = await client.get(url, headers=_headers)
        except httpx.HTTPError as e:
            logging.warning(
                "GitHub API request failed (attempt %d/%d): %s",
                attempt + 1,
                _RETRY_MAX_ATTEMPTS,
                e,
            )
            if attempt < _RETRY_MAX_ATTEMPTS - 1:
                await asyncio.sleep(2**attempt)
            continue
        if r.status_code == 429 or r.status_code >= 500:
            logging.warning(
                "GitHub API returned %d (attempt %d/%d) for %s",
                r.status_code,
                attempt + 1,
                _RETRY_MAX_ATTEMPTS,
                url,
            )
            if attempt < _RETRY_MAX_ATTEMPTS - 1:
                retry_after = r.headers.get("Retry-After")
                if retry_after:
                    try:
                        await asyncio.sleep(float(retry_after))
                    except (TypeError, ValueError):
                        await asyncio.sleep(2**attempt)
                else:
                    await asyncio.sleep(2**attempt)
            continue
        return r
    return None


async def enrich(client: httpx.AsyncClient, slugs: list[str]) -> list[Repo]:
    """One ``GET /repos/{o}/{r}`` per slug, in order.

    A failed lookup is logged and skipped. More than half failing raises
    ``EnrichmentFailed``, so a GitHub outage is never mistaken for a quiet day.
    The client must follow redirects: a renamed repo answers with a 301.
    """
    repos: list[Repo] = []
    for slug in slugs:
        response = await _get_with_retry(client, f"https://api.github.com/repos/{slug}")
        try:
            if response is None or response.status_code != 200:
                raise ValueError(
                    "no answer" if response is None else f"HTTP {response.status_code}"
                )
            repos.append(_repo(response.json()))
        except (KeyError, TypeError, ValueError) as e:
            logging.warning("Repo lookup failed for '%s': %s", slug, e)
    failed = len(slugs) - len(repos)
    if failed * 2 > len(slugs):
        raise EnrichmentFailed(f"repo lookup failed for {failed} of {len(slugs)} repos")
    return repos


async def readme_excerpt(client: httpx.AsyncClient, slug: str) -> str:
    """The start of a repo's README as text; "" when it has none or GitHub cannot say."""
    response = await _get_with_retry(
        client, f"https://api.github.com/repos/{slug}/readme"
    )
    if response is None or response.status_code != 200:
        return ""
    try:
        content = base64.b64decode(response.json()["content"])
    except (KeyError, TypeError, ValueError, binascii.Error) as e:
        logging.warning("README for '%s' could not be read: %s", slug, e)
        return ""
    return content.decode("utf-8", errors="replace")[:_README_EXCERPT_CHARS]


async def search_fallback(client: httpx.AsyncClient, now: datetime) -> list[Repo]:
    """Young AI repos by stars, one search per topic in ``FALLBACK_TOPICS``.

    Each topic is its own query, so the search needs no OR syntax. A failed
    search raises: with trending already down, an empty pool would be silent.
    """
    since = (now - FALLBACK_MAX_AGE).date().isoformat()
    repos: dict[str, Repo] = {}
    for topic in FALLBACK_TOPICS:
        response = await client.get(
            _SEARCH_URL,
            params={
                "q": f"topic:{topic} created:>{since} stars:>{FALLBACK_MIN_STARS}",
                "sort": "stars",
                "order": "desc",
                "per_page": _FALLBACK_PER_QUERY,
            },
            headers=_headers,
        )
        response.raise_for_status()
        for item in response.json().get("items", []):
            repo = _repo(item)
            repos.setdefault(repo.slug, repo)
    return list(repos.values())
