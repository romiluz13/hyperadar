"""Cross-source corroboration gate — publish only what 2+ sources confirm.

@github-radar tracks consensus: a repo is only post-worthy when GitHub
discovery AND an independent community source (an HN story, a Reddit thread)
noticed it in the same window. One source alone — however fast its stars
move — is exactly the single-source hype the gate exists to filter.

@hidden-gems is exempt by design: its thesis is finding repos BEFORE
consensus exists, so it keeps the momentum gate only.

Reddit's public search API is best-effort: datacenter IPs (GitHub Actions
runners) are frequently blocked with 403. A blocked or failed check returns
None (unknown), which simply does not count as corroboration — it never
fails the run.
"""

import httpx
from datetime import datetime, timezone

from _shared.engagement import fetch_hn_engagement, repo_slug

# A story/thread older than this is not corroboration of today's trending.
_CORROBORATION_WINDOW_DAYS = 14
_REDDIT_SEARCH_URL = "https://www.reddit.com/search.json"
# Reddit rejects requests without a descriptive User-Agent.
_USER_AGENT = "hyperadar-source-doctor/1.0"


async def _hn_within_window(
    repo_url: str, client: httpx.AsyncClient | None = None
) -> dict | None:
    """HN leg of the gate: engagement measured over the corroboration window,
    not the longer 30-day engagement window used for score boosting."""
    return await fetch_hn_engagement(
        repo_url, client, max_age_days=_CORROBORATION_WINDOW_DAYS
    )


async def fetch_reddit_mention(
    repo_url: str, client: httpx.AsyncClient | None = None
) -> bool | None:
    """True when a recent Reddit thread mentions the repo; None when unknown.

    Unknown covers HTTP errors, 403 bot-blocking, and Reddit's JSON error
    payloads — the check degrades to "not corroborated", never to a crash.
    """
    slug = repo_slug(repo_url)
    if not slug:
        return None
    owns_client = client is None
    if client is None:
        client = httpx.AsyncClient(timeout=15, headers={"User-Agent": _USER_AGENT})
    try:
        r = await client.get(
            _REDDIT_SEARCH_URL,
            params={
                "q": f'"{slug}"',
                "sort": "new",
                "t": "month",
                "limit": 10,
            },
        )
        if r.status_code != 200:
            return None
        payload = r.json()
        if not isinstance(payload, dict) or "data" not in payload:
            return None  # Reddit error shape: {"error": 403}
        # Reddit's finest time filter is "month"; apply the corroboration
        # window ourselves via each thread's creation timestamp.
        cutoff = (
            datetime.now(timezone.utc).timestamp()
            - _CORROBORATION_WINDOW_DAYS * 86400
        )
        children = payload.get("data", {}).get("children", [])
        for child in children:
            data = child.get("data", {})
            if (data.get("created_utc") or 0) < cutoff:
                continue  # a month-old thread is not today's corroboration
            haystack = (
                f"{data.get('title', '')} {data.get('url', '')} "
                f"{data.get('selftext', '')}"
            ).lower()
            if slug.lower() in haystack:
                return True
        return False
    except (httpx.HTTPError, ValueError):
        return None
    finally:
        if owns_client:
            await client.aclose()


async def corroborated_candidates(
    candidates: list[dict],
    *,
    hn_fetch=_hn_within_window,
    reddit_fetch=fetch_reddit_mention,
) -> list[dict]:
    """Keep only candidates confirmed by 2+ independent sources.

    GitHub discovery always counts as one source. Each surviving candidate
    gains ``_hn_engagement`` (for the engagement boost + evidence quote) and
    ``corroborated_by`` (the source list, for evidence copy).
    """
    kept: list[dict] = []
    for c in candidates:
        repo_url = c.get("url") or ""
        sources = ["github"]
        engagement = await hn_fetch(repo_url)
        if engagement is not None:
            sources.append("hacker_news")
            c["_hn_engagement"] = engagement
        if await reddit_fetch(repo_url):
            sources.append("reddit")
        if len(sources) >= 2:
            c["corroborated_by"] = sources
            kept.append(c)
    return kept
