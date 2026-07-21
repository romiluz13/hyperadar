"""Daily star-snapshot tracker for hidden-gems candidates.

Discovers recently created low-star GitHub repos and stores a daily
star/fork snapshot in the signals time-series collection. These
snapshots feed the Momentum Score (see ``_shared.momentum``) which
identifies repos accelerating toward breakout.

Each snapshot is a pre-publication signal: ``postId`` is empty because
no post has been written yet — the snapshot exists purely so future
momentum calculations have historical data to work with.
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

# Search parameters
_STAR_RANGE = "10..500"
_MAX_RESULTS = 100
_TOPICS = ["ai", "llm", "agent"]


async def _search_candidates(client: httpx.AsyncClient) -> list[dict]:
    """Search GitHub for recently created low-star repos across AI topics.

    Returns a deduplicated list of candidate repos with star/fork counts.
    """
    since = (datetime.now(timezone.utc) - timedelta(days=90)).strftime("%Y-%m-%d")
    all_candidates: list[dict] = []
    for topic in _TOPICS:
        if len(all_candidates) >= _MAX_RESULTS:
            break
        params = {
            "q": (f"created:>{since} stars:{_STAR_RANGE} topic:{topic} sort:updated"),
            "sort": "updated",
            "order": "desc",
            "per_page": min(_MAX_RESULTS - len(all_candidates), 100),
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
            all_candidates.append(
                {
                    "url": it["html_url"],
                    "github_stars": it.get("stargazers_count", 0),
                    "github_forks": it.get("forks_count", 0),
                }
            )
            if len(all_candidates) >= _MAX_RESULTS:
                break

    seen: set[str] = set()
    unique: list[dict] = []
    for c in all_candidates:
        if c["url"] not in seen:
            seen.add(c["url"])
            unique.append(c)
    return unique[:_MAX_RESULTS]


async def track_daily_snapshots(db) -> int:
    """Discover candidate repos and store daily star/fork snapshots in signals time-series.

    Discovers candidates via GitHub Search API (stars:10..500, created:<90d,
    topic:ai/llm/agent, sort:updated, up to 100 repos). For each candidate,
    stores a signal document in the signals time-series collection with:
    - capturedAt: current UTC timestamp
    - projectId: the repo URL (meta field)
    - postId: empty string (no post yet — this is a pre-publication snapshot)
    - github_stars: current star count
    - github_forks: current fork count

    Idempotent: if a snapshot already exists for this repo+day, skip it.
    Returns count of new snapshots stored.
    """
    now = datetime.now(timezone.utc)
    day_start = now.replace(hour=0, minute=0, second=0, microsecond=0)
    day_end = day_start + timedelta(days=1)

    async with httpx.AsyncClient(timeout=30) as client:
        candidates = await _search_candidates(client)

    new_count = 0
    for candidate in candidates:
        project_id = candidate["url"]

        # Check if a snapshot already exists for this repo today
        existing = await db.signals.find_one(
            {
                "projectId": project_id,
                "capturedAt": {"$gte": day_start, "$lt": day_end},
            }
        )
        if existing:
            continue

        await db.signals.insert_one(
            {
                "capturedAt": now,
                "projectId": project_id,
                "postId": "",
                "github_stars": candidate["github_stars"],
                "github_forks": candidate["github_forks"],
            }
        )
        new_count += 1

    return new_count
