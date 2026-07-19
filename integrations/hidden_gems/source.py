"""Hidden gems source — HN API + GitHub Search for recent low-star repos.

@hidden-gems finds things BEFORE they blow up: HN Show HN posts linking to
novel repos, and recently created low-star repos sorted by recent updates.
"""

import os
from datetime import datetime, timedelta, timezone

import httpx

_github_token = os.environ.get("GITHUB_TOKEN", "")
_headers = (
    {"Authorization": f"token {_github_token}", "Accept": "application/vnd.github+json"}
    if _github_token
    else {}
)


def normalize_hn_story(story: dict, story_id: int) -> dict:
    """Keep Hacker News evidence labeled as Hacker News evidence."""
    url = story.get("url", "")
    title = story.get("title", "")
    return {
        "url": url or f"https://news.ycombinator.com/item?id={story_id}",
        "title": title[:200],
        "kind": "repo" if "github.com" in url else "thread",
        "description": title,
        "topics": ["hn", "hidden-gem", "ai"],
        "discovery_source": "hacker_news",
        "evidence_url": f"https://news.ycombinator.com/item?id={story_id}",
        "hn_points": story.get("score", 0),
        "hn_comments": story.get("descendants", 0),
    }


async def fetch_hn_candidates(max_results: int = 5) -> list[dict]:
    """Fetch Show HN posts with traction via the Algolia HN API.

    Algolia returns full JSON objects with searchable tags (show_hn) and
    numeric filters (points), unlike the Firebase API which only returns IDs.
    Show HN posts surface GitHub repos 24-48h before they trend.
    """
    candidates: list[dict] = []
    async with httpx.AsyncClient(timeout=30) as client:
        r = await client.get(
            "https://hn.algolia.com/api/v1/search",
            params={
                "tags": "show_hn",
                "numericFilters": "points>50",
                "hitsPerPage": 20,
            },
        )
        r.raise_for_status()
        hits = r.json().get("hits", [])
    for hit in hits:
        if len(candidates) >= max_results:
            break
        url = hit.get("url") or ""
        title = hit.get("title") or ""
        story_id = hit.get("objectID") or ""
        if not title:
            continue
        candidates.append(
            {
                "url": url or f"https://news.ycombinator.com/item?id={story_id}",
                "title": title[:200],
                "kind": "repo" if "github.com" in url else "thread",
                "description": title,
                "topics": ["hn", "hidden-gem", "ai"],
                "discovery_source": "hacker_news",
                "evidence_url": f"https://news.ycombinator.com/item?id={story_id}",
                "hn_points": hit.get("points", 0),
                "hn_comments": hit.get("num_comments", 0),
            }
        )
    return candidates


async def fetch_low_star_github_candidates(max_results: int = 15) -> list[dict]:
    """Recently created GitHub repos with 10–200 stars (true hidden gems).

    Searches across ai, llm, and agent topics to find niche tools.
    """
    since = (datetime.now(timezone.utc) - timedelta(days=90)).strftime("%Y-%m-%d")
    all_candidates = []
    async with httpx.AsyncClient(timeout=30) as client:
        for topic in ["ai", "llm", "agent"]:
            if len(all_candidates) >= max_results:
                break
            params = {
                "q": f"created:>{since} stars:10..200 topic:{topic} sort:updated",
                "sort": "updated",
                "order": "desc",
                "per_page": min(max_results - len(all_candidates), 30),
            }
            try:
                r = await client.get(
                    "https://api.github.com/search/repositories",
                    params=params,
                    headers=_headers,
                )
                r.raise_for_status()
                items = r.json().get("items", [])
            except Exception:
                continue
            for it in items:
                stars = it.get("stargazers_count", 0)
                if stars == 0:
                    continue
                all_candidates.append(
                    {
                        "url": it["html_url"],
                        "title": it["full_name"],
                        "kind": "repo",
                        "description": it.get("description") or "",
                        "topics": it.get("topics") or [],
                        "discovery_source": "github",
                        "evidence_url": it["html_url"],
                        "github_stars": stars,
                        "created_at": it.get("created_at"),
                        "owner": it["owner"]["login"],
                        "repo": it["name"],
                    }
                )
    seen = set()
    unique = []
    for c in all_candidates:
        if c["url"] not in seen:
            seen.add(c["url"])
            unique.append(c)
    return unique[:max_results]


async def fetch_hidden_gems(max_results: int = 8) -> list[dict]:
    """Combine HN + low-star GitHub to find hidden gems before they blow up."""
    hn = await fetch_hn_candidates(max_results=10)
    gh = await fetch_low_star_github_candidates(max_results=15)
    return (hn + gh)[:max_results]
