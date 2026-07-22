"""Hidden gems source — HN candidates + breakout prediction pipeline.

@hidden-gems finds things BEFORE they blow up: HN Show HN posts linking to
novel repos, and repos identified by the momentum-score breakout gate.
"""

import logging
import os
from datetime import datetime, timedelta, timezone

import httpx

from _shared.momentum import (
    _acceleration,
    _engagement_depth,
    _is_monotonic_growth,
    _velocity,
    compute_momentum_score,
    passes_fake_star_filter,
    should_publish_hidden_gem,
)
from hidden_gems.tracker import track_daily_snapshots

_github_token = os.environ.get("GITHUB_TOKEN", "")
_headers = (
    {"Authorization": f"token {_github_token}", "Accept": "application/vnd.github+json"}
    if _github_token
    else {}
)

_MIN_HISTORY_DAYS = 7


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
            except Exception as e:
                logging.warning("GitHub search failed for topic '%s': %s", topic, e)
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


async def _last_published_days(db, project_url: str) -> int:
    """Days since the most recent post for this project URL. 999 if never posted."""
    post = await db.posts.find_one(
        {"project.url": project_url},
        {"postedAt": 1},
    )
    if not post or not post.get("postedAt"):
        return 999
    posted = post["postedAt"]
    if posted.tzinfo is None:
        posted = posted.replace(tzinfo=timezone.utc)
    delta = datetime.now(timezone.utc) - posted
    return max(0, delta.days)


async def fetch_breakout_candidates(db) -> list[dict]:
    """Find repos that pass the breakout prediction gate.

    1. Store today's snapshots via track_daily_snapshots.
    2. Query the signals time-series for repos with >=7 days of history.
    3. For each, compute the Momentum Score and check the publishing gate.
    4. Return only repos that pass, with score and velocity included.
    """
    await track_daily_snapshots(db)

    # Find all projectIds with >=7 days of pre-publication snapshots.
    pipeline = [
        {"$match": {"postId": ""}},
        {
            "$group": {
                "_id": "$projectId",
                "count": {"$sum": 1},
            }
        },
        {"$match": {"count": {"$gte": _MIN_HISTORY_DAYS}}},
    ]
    candidates_with_history = await (await db.signals.aggregate(pipeline)).to_list(
        length=None
    )

    results: list[dict] = []
    for doc in candidates_with_history:
        project_url = doc["_id"]

        # Fetch the full history sorted by capturedAt ascending.
        cursor = db.signals.find(
            {"projectId": project_url, "postId": ""},
            {"capturedAt": 1, "github_stars": 1, "github_forks": 1, "_id": 0},
        ).sort("capturedAt", 1)
        history = await cursor.to_list(length=None)

        if len(history) < _MIN_HISTORY_DAYS:
            continue

        prior_count = await db.posts.count_documents({"project.url": project_url})
        score = compute_momentum_score(history, prior_post_count=prior_count)
        velocity = _velocity(history, 7)
        acceleration = _acceleration(history)
        stars = history[-1].get("github_stars", 0)
        forks = history[-1].get("github_forks", 0)
        fork_star_ratio = _engagement_depth(history)

        if not passes_fake_star_filter(stars, forks):
            continue

        last_pub_days = await _last_published_days(db, project_url)
        is_monotonic = _is_monotonic_growth(history)
        if not should_publish_hidden_gem(
            score,
            velocity,
            acceleration,
            fork_star_ratio,
            last_pub_days,
            is_monotonic,
        ):
            continue

        results.append(
            {
                "url": project_url,
                "title": project_url.rsplit("/", 1)[-1],
                "kind": "repo",
                "description": "",
                "topics": ["ai", "hidden-gem"],
                "discovery_source": "breakout",
                "evidence_url": project_url,
                "github_stars": stars,
                "github_forks": forks,
                "momentumScore": score,
                "velocity": velocity,
                "acceleration": acceleration,
            }
        )
    return results


async def fetch_hidden_gems(max_results: int = 8) -> list[dict]:
    """Combine HN + low-star GitHub to find hidden gems before they blow up.

    Kept for backward compatibility — prefer fetch_breakout_candidates.
    """
    hn = await fetch_hn_candidates(max_results=10)
    gh = await fetch_low_star_github_candidates(max_results=15)
    return (hn + gh)[:max_results]
