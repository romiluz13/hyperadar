"""Daily digest — picks the top 5 most hyped items from the last 24h.

Queries MongoDB for external agent posts, calls Grove LLM to rank and
blurb the top 5 (max 2 per source), stores the digest in the digests
collection.
"""

import html
import json
import logging
import os
import sys
from datetime import datetime, timedelta, timezone

import httpx

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from _shared.agent_catalog import AGENT_CATALOG  # noqa: E402
from _shared.grove import grove_api_key  # noqa: E402
from _shared.momentum import _raw_velocity  # noqa: E402

logger = logging.getLogger(__name__)

DAILY_DIGEST_AGENT_HANDLES = [
    agent["handle"]
    for agent in AGENT_CATALOG
    if agent["handle"] not in ("@community-radar", "@weekly-digest")
]

DAILY_DIGEST_MAX_ITEMS = 5
DAILY_DIGEST_MAX_PER_AGENT = 2
DAILY_DIGEST_WHAT_MAX_CHARS = 140
STAR_SNAPSHOTS_READ = 8


def _clean_what(text: object) -> str | None:
    """Decode HTML entities, collapse whitespace, cut at a word boundary to 140."""
    if not isinstance(text, str):
        return None
    collapsed = " ".join(html.unescape(text).split())
    if not collapsed:
        return None
    head = collapsed[: DAILY_DIGEST_WHAT_MAX_CHARS + 1]
    if len(head) <= DAILY_DIGEST_WHAT_MAX_CHARS:
        return head
    if " " not in head:
        return head[:DAILY_DIGEST_WHAT_MAX_CHARS]
    return head.rsplit(" ", 1)[0]


def _item_what(project: dict, grove_what: object) -> str | None:
    """The project's own description wins; Grove's line is only a fallback.

    A video's stored description is a byline, "By <channel> · N views"
    (youtube_trends/source.py), not what the video is, so videos carry no `what`.
    Grove writes its line from the post body, which carries star counts and the
    blurb's growth claims, so a Grove line with any digit in it is dropped: the
    only numbers RomBot may print come from the digest's own numeric fields.
    """
    if project.get("kind") == "video":
        return None
    description = _clean_what(project.get("description"))
    if description:
        return description
    fallback = _clean_what(grove_what)
    if fallback and not any(char.isdigit() for char in fallback):
        return fallback
    return None


async def _weekly_star_gain(db, project_url: str) -> tuple[int | None, int | None]:
    """Raw star gain over the last daily snapshots and the days they really span.

    Reads the tracker's snapshots the way github_radar does (projectId plus an
    empty postId, oldest first). Both values are None below two snapshots:
    unknown, not zero. The window comes from the timestamps because backfilled
    snapshots are stamped 12:00Z and organic ones at run time. This is
    enrichment: any failure leaves the gain unknown instead of losing the digest.
    """
    try:
        docs = (
            await db.signals.find(
                {"projectId": project_url, "postId": ""},
                {"capturedAt": 1, "github_stars": 1, "_id": 0},
            )
            .sort("capturedAt", 1)
            .to_list(None)
        )
        history = [doc for doc in docs if "github_stars" in doc][-STAR_SNAPSHOTS_READ:]
        if len(history) < 2:
            return None, None
        span = history[-1]["capturedAt"] - history[0]["capturedAt"]
        return _raw_velocity(history, 7), round(span.total_seconds() / 86400)
    except Exception as e:
        logger.warning("Weekly star gain skipped for '%s': %s", project_url, e)
        return None, None


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

    needs_what = [
        i
        for i, post in enumerate(posts)
        if post.get("project", {}).get("kind") != "video"
        and not _clean_what(post.get("project", {}).get("description"))
    ]
    what_request = (
        'For posts with these ids, also return a key "what": a plain line of '
        "at most 12 words saying what the project is, based only on its title "
        "and body, with no numbers, no star counts and no hype words; if the "
        "title and body do not say what it is, return an empty string: "
        f"{json.dumps(needs_what)}. "
        if needs_what
        else ""
    )

    return (
        "You are the HypeRadar editor. "
        "Pick the top 5 most interesting/hyped items from the following posts. "
        f"Max 2 from the same source (agentHandle). "
        "For each, write a one-line English blurb explaining why it's hyped. "
        "Return as a JSON array of objects with keys: id, blurb. "
        f"{what_request}"
        f"Posts: {json.dumps(items)}"
    )


async def _call_grove(posts: list[dict]) -> list[dict]:
    """Call Grove LLM to pick + blurb the top 5 items. Returns list of {id, blurb}."""
    prompt = _build_grove_prompt(posts)
    async with httpx.AsyncClient(timeout=120) as client:
        r = await client.post(
            f"{os.environ['GROVE_BASE_URL']}/chat/completions",
            headers={
                "Content-Type": "application/json",
                "api-key": grove_api_key(),
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
        signal = post.get("signal", {})
        weekly_gain, window_days = await _weekly_star_gain(db, project.get("url", ""))
        items.append(
            {
                "rank": len(items) + 1,
                "agentHandle": agent,
                "title": html.unescape(project.get("title", "")),
                "url": project.get("url", ""),
                "kind": project.get("kind", "repo"),
                "blurb": pick.get("blurb", ""),
                "what": _item_what(project, pick.get("what")),
                "verdict": post.get("verdict") or None,
                "weeklyGain": weekly_gain,
                "weeklyGainWindowDays": window_days,
                "score": post.get("rankScore", 0),
                "signalSource": signal.get("source"),
                "signalMetric": signal.get("metric"),
                "signalValue": signal.get("value"),
                "signalDelta": signal.get("delta"),
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
            signal = post.get("signal", {})
            weekly_gain, window_days = await _weekly_star_gain(
                db, project.get("url", "")
            )
            items.append(
                {
                    "rank": len(items) + 1,
                    "agentHandle": agent,
                    "title": html.unescape(project.get("title", "")),
                    "url": project.get("url", ""),
                    "kind": project.get("kind", "repo"),
                    "blurb": post.get("body", "")[:100],
                    "what": _item_what(project, None),
                    "verdict": post.get("verdict") or None,
                    "weeklyGain": weekly_gain,
                    "weeklyGainWindowDays": window_days,
                    "score": post.get("rankScore", 0),
                    "signalSource": signal.get("source"),
                    "signalMetric": signal.get("metric"),
                    "signalValue": signal.get("value"),
                    "signalDelta": signal.get("delta"),
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
