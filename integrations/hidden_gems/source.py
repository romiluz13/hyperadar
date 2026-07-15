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
    """Fetch top HN stories, filter for Show HN / GitHub links (hidden gems)."""
    async with httpx.AsyncClient(timeout=30) as client:
        r = await client.get("https://hacker-news.firebaseio.com/v0/topstories.json")
        r.raise_for_status()
        story_ids = r.json()[:20]

    candidates = []
    async with httpx.AsyncClient(timeout=30) as client:
        for sid in story_ids:
            if len(candidates) >= max_results:
                break
            r = await client.get(
                f"https://hacker-news.firebaseio.com/v0/item/{sid}.json"
            )
            if r.status_code != 200:
                continue
            story = r.json()
            if not story or story.get("type") != "story":
                continue
            url = story.get("url", "")
            title = story.get("title", "")
            # Look for GitHub links or Show HN posts
            if "github.com" in url or title.startswith("Show HN"):
                candidates.append(normalize_hn_story(story, sid))
    return candidates


async def fetch_low_star_github_candidates(max_results: int = 5) -> list[dict]:
    """Recently created, recently updated GitHub repos with 50–500 stars."""
    since = (datetime.now(timezone.utc) - timedelta(days=14)).strftime("%Y-%m-%d")
    params = {
        "q": f"created:>{since} stars:50..500 topic:ai sort:updated",
        "sort": "updated",
        "order": "desc",
        "per_page": max_results,
    }
    async with httpx.AsyncClient(timeout=30) as client:
        r = await client.get(
            "https://api.github.com/search/repositories",
            params=params,
            headers=_headers,
        )
        r.raise_for_status()
        items = r.json().get("items", [])

    candidates = []
    for it in items:
        candidates.append(
            {
                "url": it["html_url"],
                "title": it["full_name"],
                "kind": "repo",
                "description": it.get("description") or "",
                "topics": it.get("topics") or [],
                "discovery_source": "github",
                "evidence_url": it["html_url"],
                "github_stars": it.get("stargazers_count", 0),
                "created_at": it.get("created_at"),
                "owner": it["owner"]["login"],
                "repo": it["name"],
            }
        )
    return candidates


async def fetch_hidden_gems(max_results: int = 8) -> list[dict]:
    """Combine HN + low-star GitHub to find hidden gems before they blow up."""
    hn = await fetch_hn_candidates(max_results=5)
    gh = await fetch_low_star_github_candidates(max_results=5)
    return (hn + gh)[:max_results]
