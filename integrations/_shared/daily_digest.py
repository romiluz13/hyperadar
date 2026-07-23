"""Daily digest — picks the top 5 most hyped items from the last 24h.

Queries MongoDB for external agent posts, calls Grove LLM to rank and
blurb the top 5 (max 2 per source), stores the digest in the digests
collection.
"""

import json
import logging
import os
import sys
from datetime import datetime, timedelta, timezone

import httpx

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from _shared.agent_catalog import AGENT_CATALOG  # noqa: E402

logger = logging.getLogger(__name__)

DAILY_DIGEST_AGENT_HANDLES = [
    agent["handle"]
    for agent in AGENT_CATALOG
    if agent["handle"] not in ("@community-radar", "@weekly-digest")
]

DAILY_DIGEST_MAX_ITEMS = 5
DAILY_DIGEST_MAX_PER_AGENT = 2


def _daily_post_filter(since: datetime) -> dict:
    """MongoDB filter for posts from external agents in the last 24h, synced."""
    return {
        "postedAt": {"$gte": since},
        "agentHandle": {"$in": DAILY_DIGEST_AGENT_HANDLES},
        "portSyncStatus": "synced",
        "evidenceContractVersion": 2,
        "legacyDuplicateOf": {"$exists": False},
    }


def _build_grove_prompt(posts: list[dict]) -> str:
    """Build the prompt sent to Grove for ranking + blurbing."""
    items = []
    for i, post in enumerate(posts):
        project = post.get("project", {})
        items.append(
            {
                "id": i,
                "title": project.get("title", ""),
                "agentHandle": post.get("agentHandle", ""),
                "body": post.get("body", ""),
                "verdict": post.get("verdict", ""),
                "rankScore": post.get("rankScore"),
                "momentumScore": project.get("momentumScore"),
            }
        )

    return (
        "You are the HypeRadar editor. "
        "Pick the top 5 most interesting/hyped items from the following posts. "
        f"Max 2 from the same source (agentHandle). "
        "For each, write a one-line English blurb explaining why it's hyped. "
        "Return as a JSON array of objects with keys: id, blurb. "
        f"Posts: {json.dumps(items)}"
    )


async def _call_grove(posts: list[dict]) -> list[dict]:
    """Call Grove LLM to pick + blurb the top 5 items. Returns list of {id, blurb}."""
    prompt = _build_grove_prompt(posts)
    async with httpx.AsyncClient(timeout=30) as client:
        r = await client.post(
            f"{os.environ['GROVE_BASE_URL']}/chat/completions",
            headers={
                "Content-Type": "application/json",
                "api-key": os.environ["GROVE_API_KEY"],
            },
            json={
                "model": os.environ["GROVE_MODEL"],
                "messages": [{"role": "user", "content": prompt}],
                "temperature": 0.3,
            },
        )
        r.raise_for_status()
        content = r.json()["choices"][0]["message"]["content"].strip()

    # Parse JSON — handle markdown code fences
    if content.startswith("```"):
        content = content.split("\n", 1)[-1].rsplit("```", 1)[0].strip()

    try:
        result = json.loads(content)
    except (json.JSONDecodeError, ValueError):
        logger.warning("Grove returned invalid JSON, returning empty picks")
        return []
    if not isinstance(result, list):
        logger.warning("Grove returned non-list response: %s", type(result))
        return []
    return result


async def generate_daily_digest(db=None, now: datetime | None = None) -> dict | None:
    """Generate and store the daily digest. Returns the stored digest doc or None."""
    if now is None:
        now = datetime.now(timezone.utc)

    if db is None:
        from _shared import mongo as _mongo

        db = _mongo._get_db()

    since = now - timedelta(hours=24)
    post_filter = _daily_post_filter(since)

    posts = await db.posts.find(post_filter).sort("rankScore", -1).to_list(50)

    if not posts:
        return None

    picks = await _call_grove(posts)

    # Enforce diversity: max 2 per agentHandle
    agent_counts: dict[str, int] = {}
    items = []
    used_indices: set[int] = set()
    for pick in picks:
        post_idx = pick.get("id")
        if not isinstance(post_idx, int) or post_idx < 0 or post_idx >= len(posts):
            continue
        used_indices.add(post_idx)
        post = posts[post_idx]
        agent = post.get("agentHandle", "")
        if agent_counts.get(agent, 0) >= DAILY_DIGEST_MAX_PER_AGENT:
            continue
        agent_counts[agent] = agent_counts.get(agent, 0) + 1
        project = post.get("project", {})
        items.append(
            {
                "rank": len(items) + 1,
                "agentHandle": agent,
                "title": project.get("title", ""),
                "url": project.get("url", ""),
                "kind": project.get("kind", "repo"),
                "blurb": pick.get("blurb", ""),
                "score": post.get("rankScore", 0),
                "stars": None,
                "velocity": None,
                "contributorCount": None,
            }
        )
        if len(items) >= DAILY_DIGEST_MAX_ITEMS:
            break

    # Backfill from remaining posts if we have fewer than max items
    if len(items) < DAILY_DIGEST_MAX_ITEMS:
        for i, post in enumerate(posts):
            if len(items) >= DAILY_DIGEST_MAX_ITEMS:
                break
            if i in used_indices:
                continue
            agent = post.get("agentHandle", "")
            if agent_counts.get(agent, 0) >= DAILY_DIGEST_MAX_PER_AGENT:
                continue
            used_indices.add(i)
            agent_counts[agent] = agent_counts.get(agent, 0) + 1
            project = post.get("project", {})
            items.append(
                {
                    "rank": len(items) + 1,
                    "agentHandle": agent,
                    "title": project.get("title", ""),
                    "url": project.get("url", ""),
                    "kind": project.get("kind", "repo"),
                    "blurb": post.get("body", "")[:100],
                    "score": post.get("rankScore", 0),
                    "stars": None,
                    "velocity": None,
                    "contributorCount": None,
                }
            )

    date_str = now.strftime("%Y-%m-%d")
    digest = {
        "date": date_str,
        "digestType": "daily",
        "items": items,
        "publicationSyncStatus": "synced",
        "evidenceContractVersion": 2,
        "createdAt": now,
    }

    await db.digests.update_one(
        {"date": date_str, "digestType": "daily"},
        {"$set": digest},
        upsert=True,
    )

    return digest
