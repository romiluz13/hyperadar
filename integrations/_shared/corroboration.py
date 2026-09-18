"""Cross-source corroboration gate — publish only what 2+ sources confirm.

@github-radar tracks consensus: a repo is only post-worthy when GitHub
discovery AND an independent community source (an HN story, a Reddit thread)
noticed it in the same window. One source alone — however fast its stars
move — is exactly the single-source hype the gate exists to filter.

@hidden-gems is exempt by design: its thesis is finding repos BEFORE
consensus exists, so it keeps the momentum gate only.

The Reddit leg corroborates against the corpus that @reddit-pulse already
collects every run (the ``reddit_post_snapshots`` time-series, 16 curated AI
subreddits, gathered through Bright Data). Reddit's public search API is the
fallback only: it blocks unauthenticated clients with 403 — including
residential IPs — so a blocked fallback logs one warning and returns None
(unknown), which simply does not count as corroboration. Neither leg ever
fails the run.
"""

import logging
from datetime import datetime, timedelta, timezone

import httpx

from _shared.engagement import fetch_hn_engagement, repo_slug, slug_mentioned

# A story/thread older than this is not corroboration of today's trending.
_CORROBORATION_WINDOW_DAYS = 14
_REDDIT_SEARCH_URL = "https://www.reddit.com/search.json"
# Reddit rejects requests without a descriptive User-Agent.
_USER_AGENT = "hyperadar-source-doctor/1.0"

# Warn once per process that the live Reddit fallback is blocked — the gate
# still works via the corpus leg, but the operator should know it's blind
# outside the curated subreddit universe.
_live_reddit_warned = False


async def _hn_within_window(
    repo_url: str, client: httpx.AsyncClient | None = None
) -> dict | None:
    """HN leg of the gate: engagement measured over the corroboration window,
    not the longer 30-day engagement window used for score boosting."""
    return await fetch_hn_engagement(
        repo_url, client, max_age_days=_CORROBORATION_WINDOW_DAYS
    )


async def load_reddit_corpus(db) -> list[str]:
    """Searchable text for every Reddit thread @reddit-pulse saw in the window.

    One snapshot row per (thread, run); the matcher only needs the combined
    title + description text, capped to the corroboration window so stale
    threads cannot corroborate today's trending. Degrades to [] on any DB
    error — an unreadable corpus means "use the live fallback", not a crash.
    """
    since = datetime.now(timezone.utc) - timedelta(days=_CORROBORATION_WINDOW_DAYS)
    try:
        cursor = db.reddit_post_snapshots.find(
            {"capturedAt": {"$gte": since}},
            {"title": 1, "description": 1, "_id": 0},
        )
        docs = await cursor.to_list(length=None)
    except Exception as e:
        logging.warning("Reddit corpus load failed (falling back to live): %s", e)
        return []
    return [f"{d.get('title', '')} {d.get('description', '')}" for d in docs]


def _corpus_mention(repo_url: str, corpus: list[str] | None) -> bool | None:
    """True/False from the corpus; None when the corpus cannot answer.

    None (no corpus / empty corpus) routes the caller to the live fallback.
    A match requires a slug boundary, same as the HN title leg.
    """
    if not corpus:
        return None
    slug = repo_slug(repo_url)
    if not slug:
        return None
    return any(slug_mentioned(slug, text) for text in corpus)


async def _search_reddit_live(
    repo_url: str, client: httpx.AsyncClient | None = None
) -> bool | None:
    """Best-effort live search of Reddit's public API (usually 403-blocked).

    True when a thread in the corroboration window mentions the repo; None
    when unknown (HTTP errors, 403 bot-blocking, Reddit's JSON error
    payloads) — unknown degrades to "not corroborated", never to a crash.
    """
    global _live_reddit_warned
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
            if not _live_reddit_warned:
                _live_reddit_warned = True
                logging.warning(
                    "Reddit public search returned HTTP %s — Reddit blocks "
                    "unauthenticated clients, so corroboration relies on the "
                    "@reddit-pulse corpus (curated subreddits only). This "
                    "warning logs once per run.",
                    r.status_code,
                )
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
            )
            if slug_mentioned(slug, haystack):
                return True
        return False
    except (httpx.HTTPError, ValueError):
        return None
    finally:
        if owns_client:
            await client.aclose()


async def fetch_reddit_mention(
    repo_url: str,
    client: httpx.AsyncClient | None = None,
    corpus: list[str] | None = None,
) -> bool | None:
    """True when a recent Reddit thread mentions the repo; None when unknown.

    Corpus-first: the preloaded @reddit-pulse snapshot corpus answers
    authoritatively when it has data (it covers the curated subreddit
    universe, including threads that never became posts). The live public
    search runs only when the corpus is empty or unavailable.
    """
    result = _corpus_mention(repo_url, corpus)
    if result is not None:
        return result
    return await _search_reddit_live(repo_url, client)


async def corroborated_candidates(
    candidates: list[dict],
    *,
    db=None,
    hn_fetch=_hn_within_window,
    reddit_fetch=fetch_reddit_mention,
) -> list[dict]:
    """Keep only candidates confirmed by 2+ independent sources.

    GitHub discovery always counts as one source. Each surviving candidate
    gains ``_hn_engagement`` (for the engagement boost + evidence quote) and
    ``corroborated_by`` (the source list, for evidence copy). When *db* is
    given, the Reddit corpus is loaded once for the whole pool instead of
    once per candidate.
    """
    corpus = await load_reddit_corpus(db) if db is not None else None
    kept: list[dict] = []
    for c in candidates:
        repo_url = c.get("url") or ""
        sources = ["github"]
        engagement = await hn_fetch(repo_url)
        if engagement is not None:
            sources.append("hacker_news")
            c["_hn_engagement"] = engagement
        if await reddit_fetch(repo_url, corpus=corpus):
            sources.append("reddit")
        if len(sources) >= 2:
            c["corroborated_by"] = sources
            kept.append(c)
    return kept
