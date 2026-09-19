"""Daily star-snapshot tracker for hidden-gems candidates.

Discovers recently created low-star GitHub repos and stores a daily
star/fork snapshot in the signals time-series collection. These
snapshots feed the Momentum Score (see ``_shared.momentum``) which
identifies repos accelerating toward breakout.

Each snapshot is a pre-publication signal: ``postId`` is empty because
no post has been written yet — the snapshot exists purely so future
momentum calculations have historical data to work with.
"""

import logging
import os
from datetime import datetime, timedelta, timezone

import httpx

from _shared.star_history import backfill_star_history

_github_token = os.environ.get("GITHUB_TOKEN", "")
_headers = (
    {"Authorization": f"token {_github_token}", "Accept": "application/vnd.github+json"}
    if _github_token
    else {}
)

# Discovery bands — rotated daily so the tracked pool keeps refreshing.
# A single fixed query (stars:10..500 created:<90d sort:updated) re-tracked
# the same ~100 recently-updated repos forever: a closed discovery loop. Each
# day the tracker samples a different slice of the low-star candidate space.
# (star_range, created_within_days, sort, order)
_DISCOVERY_BANDS = [
    ("10..500", 90, "updated", "desc"),  # legacy band: recently updated
    ("10..100", 30, "updated", "desc"),  # very fresh, tiny repos
    ("100..500", 30, "stars", "desc"),  # fresh mid-star repos
    ("10..500", 90, "stars", "asc"),  # least-starred first
]
_MAX_RESULTS = 100
_TOPICS = ["ai", "llm", "agent"]


def _band_for_date(now: datetime) -> tuple:
    """Deterministic daily rotation: one band per day, cycling through all."""
    return _DISCOVERY_BANDS[now.toordinal() % len(_DISCOVERY_BANDS)]


async def _search_candidates(client: httpx.AsyncClient) -> list[dict]:
    """Search GitHub for recently created low-star repos across AI topics,
    using today's rotating discovery band.

    Returns a deduplicated list of candidate repos with star/fork counts.
    """
    now = datetime.now(timezone.utc)
    star_range, created_days, sort, order = _band_for_date(now)
    since = (now - timedelta(days=created_days)).strftime("%Y-%m-%d")
    all_candidates: list[dict] = []
    for topic in _TOPICS:
        if len(all_candidates) >= _MAX_RESULTS:
            break
        params = {
            "q": (
                f"created:>{since} stars:{star_range} topic:{topic} sort:{sort}"
            ),
            "sort": sort,
            "order": order,
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
        except Exception as e:
            logging.warning("GitHub search failed for topic '%s': %s", topic, e)
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

    Discovers candidates via GitHub Search API using today's rotating
    discovery band (star range, creation window, and sort order rotate daily
    across ``_DISCOVERY_BANDS``; topic:ai/llm/agent, up to 100 repos). For
    each candidate, stores a signal document in the signals time-series
    collection with:
    - capturedAt: current UTC timestamp
    - projectId: the repo URL (meta field)
    - postId: empty string (no post yet — this is a pre-publication snapshot)
    - github_stars: current star count
    - github_forks: current fork count

    Idempotent: if a snapshot already exists for this repo+day, skip it.
    Cold-start backfill: a repo whose snapshot history is too sparse for the
    momentum gate (see ``_shared.star_history``) gets its real GitHub star
    history seeded from the official star-history API, so the repo is
    scoreable the day it is first discovered instead of after 7+ days.
    Returns count of new snapshots stored (today's + backfilled).
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
        if not existing:
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

        # Cold-start backfill runs even when today's snapshot already exists
        # (a repo tracked for 3 days still has gaps to fill); it no-ops once
        # the repo's history is dense enough.
        new_count += await backfill_star_history(
            db, project_id, candidate["github_stars"], candidate["github_forks"]
        )

    return new_count
