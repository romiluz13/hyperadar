"""Cross-source community engagement — HN attention measured on a repo.

GitHub stars are one source's view of hype. An HN story's points and comments
are the same hype measured where the conversation happens, by an independent
audience. Two consumers:

- ``engagement_boost`` adds HN engagement to the GitHub Momentum Score. It is
  applied AFTER the pool-scaled publish gate so the threshold distribution
  stays purely GitHub-native (a repo must clear the gate on its own GitHub
  signals; HN attention then raises the published score).
- The top community comment is surfaced verbatim so agents can quote real
  developer discourse in evidence copy instead of raw counts alone.
"""

import re
from datetime import datetime, timedelta, timezone

import httpx

_HN_ALGOLIA_SEARCH_URL = "https://hn.algolia.com/api/v1/search"
_HN_ALGOLIA_ITEM_URL = "https://hn.algolia.com/api/v1/items"
# Match window: a story older than this is stale corroboration, not current hype.
_ENGAGEMENT_WINDOW_DAYS = 30
_MAX_QUOTE_CHARS = 280

_POINTS_FULL_AT = 200  # HN points that earn the full points component
_COMMENTS_FULL_AT = 100  # HN comments that earn the full comments component
_POINTS_COMPONENT_MAX = 10
_COMMENTS_COMPONENT_MAX = 5
ENGAGEMENT_BOOST_MAX = _POINTS_COMPONENT_MAX + _COMMENTS_COMPONENT_MAX

_REPO_SLUG_RE = re.compile(
    r"github\.com/([A-Za-z0-9_.\-]+/[A-Za-z0-9_.\-]+)", re.IGNORECASE
)


def repo_slug(repo_url: str) -> str | None:
    """Extract ``owner/repo`` from a GitHub URL, or None for other URLs."""
    match = _REPO_SLUG_RE.search(repo_url or "")
    return match.group(1).rstrip("/") if match else None


def engagement_boost(points: int, comments: int) -> int:
    """HN engagement added to the Momentum Score after the publish gate.

    points: 0-10 (full marks at 200 points), comments: 0-5 (full at 100).
    A 30-point story adds 1-2; a front-page story adds up to 15.
    """
    points_component = min(
        _POINTS_COMPONENT_MAX, int(points / _POINTS_FULL_AT * _POINTS_COMPONENT_MAX)
    )
    comments_component = min(
        _COMMENTS_COMPONENT_MAX,
        int(comments / _COMMENTS_FULL_AT * _COMMENTS_COMPONENT_MAX),
    )
    return points_component + comments_component


def strip_html(text: str) -> str:
    """Collapse HN's HTML-ish comment markup (<p>, <a href=...>) to plain text."""
    plain = re.sub(r"<[^>]+>", " ", text or "")
    return re.sub(r"\s+", " ", plain).strip()


def pick_top_comment(children: list) -> dict | None:
    """Pick the first substantive comment from an HN item's children.

    Algolia returns children in discussion order (first reply first); there
    are no per-comment scores, so the first non-empty, non-deleted reply is
    the community's opening take.
    """
    for child in children or []:
        text = strip_html(child.get("text") or "")
        author = (child.get("author") or "").strip()
        # A deleted author means the comment is gone — its text is a husk.
        if text and author and author.lower() != "[deleted]":
            return {"author": author, "text": text[:_MAX_QUOTE_CHARS]}
    return None


def _story_matches(hit: dict, repo_url: str, slug: str) -> bool:
    story_url = (hit.get("url") or "").rstrip("/")
    if story_url and story_url == (repo_url or "").rstrip("/"):
        return True
    # Substring containment would steal stories about "owner/repo-utils" or
    # "owner/repo2" for this repo; require a slug boundary after the match.
    return bool(
        re.search(
            re.escape(slug) + r"(?![A-Za-z0-9_.\-])",
            hit.get("title") or "",
            re.IGNORECASE,
        )
    )


async def fetch_hn_engagement(
    repo_url: str,
    client: httpx.AsyncClient | None = None,
    max_age_days: int = _ENGAGEMENT_WINDOW_DAYS,
) -> dict | None:
    """Find the strongest recent HN story about a GitHub repo.

    Searches the Algolia HN API for the repo slug over the last
    ``max_age_days`` days and returns the best-matching story (URL match
    beats title match; then most points) with its top comment, or None when
    no story exists. Degrades to None on any HN API failure — an engagement
    check must never fail the run it is enriching.
    """
    slug = repo_slug(repo_url)
    if not slug:
        return None
    since = int(
        (
            datetime.now(timezone.utc) - timedelta(days=max_age_days)
        ).timestamp()
    )

    owns_client = client is None
    if client is None:
        client = httpx.AsyncClient(timeout=15)
    try:
        try:
            r = await client.get(
                _HN_ALGOLIA_SEARCH_URL,
                params={
                    "query": f'"{slug}"',
                    "tags": "story",
                    "numericFilters": f"created_at_i>{since}",
                    "hitsPerPage": 20,
                },
            )
            r.raise_for_status()
            hits = r.json().get("hits", [])
        except (httpx.HTTPError, ValueError):
            return None  # HN being down means "no story measured", not a crash

        best = None
        for hit in hits:
            if not _story_matches(hit, repo_url, slug):
                continue
            if best is None or (hit.get("points") or 0) > (best.get("points") or 0):
                best = hit
        if best is None:
            return None

        top_comment = None
        try:
            item = await client.get(f"{_HN_ALGOLIA_ITEM_URL}/{best['objectID']}")
            if item.status_code == 200:
                top_comment = pick_top_comment(item.json().get("children", []))
        except httpx.HTTPError:
            top_comment = None  # the quote is enrichment, never a blocker

        return {
            "points": best.get("points") or 0,
            "comments": best.get("num_comments") or 0,
            "story_url": (
                f"https://news.ycombinator.com/item?id={best['objectID']}"
            ),
            "story_title": (best.get("title") or "")[:200],
            "top_comment": top_comment,
        }
    finally:
        if owns_client:
            await client.aclose()
