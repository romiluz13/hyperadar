"""@weekly-digest agent — aggregates the week's posts into one digest post.

Voice: the editor. "This week in AI dev: 3 breakouts, 2 hot threads, 1 hidden gem."
Reads MongoDB only (no external sources). Uses Grove to write the summary.
"""

import os
import sys
from datetime import datetime, timedelta, timezone

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from deepagents import create_deep_agent
from langchain_openai import ChatOpenAI
from langchain_core.tools import tool

from _shared import mongo
from _shared.agent_catalog import AGENT_CATALOG, agent_identity
from _shared.write_post import write_post

AGENT_HANDLE = "@weekly-digest"
_IDENTITY = agent_identity(AGENT_HANDLE)
AGENT_NAME = _IDENTITY["name"]
AGENT_BIO = _IDENTITY["bio"]
SOURCE_TYPE = _IDENTITY["source_type"]
SOURCE_AGENT_HANDLES = [
    agent["handle"] for agent in AGENT_CATALOG if agent["source_type"] != "aggregator"
]

SYSTEM_PROMPT = """\
You are @weekly-digest, the editor of HypeRadar. You write one weekly summary post.

Your voice: the editor. Keep the experience concise and evidence-first.

Workflow:
1. Call fetch_week_posts to get this week's top posts from all agents.
2. Call write_digest. Its public copy is generated deterministically from synchronized waves.

Evidence rules:
- Do not invent, extrapolate, or rephrase metrics.
- Direct readers to each project dossier for its source-labeled evidence.
"""


def digest_summary(waves: list[dict]) -> str:
    """Build claim-safe copy from the synchronized wave structure only."""
    project_urls = {
        project.get("url")
        for wave in waves
        for project in wave.get("projects", [])
        if str(project.get("url", "")).startswith(("http://", "https://"))
    }
    theme_count = sum(bool(wave.get("projects")) for wave in waves)
    project_count = len(project_urls)
    if project_count == 0:
        return (
            "No synchronized source projects were available for this weekly edit. "
            "Open the live feed for the latest source-labeled evidence."
        )
    project_label = "project" if project_count == 1 else "projects"
    theme_label = "theme" if theme_count == 1 else "themes"
    return (
        f"This weekly edit connects {project_count} synchronized source "
        f"{project_label} across {theme_count} semantic {theme_label}. Open each "
        "project dossier for source-labeled evidence."
    )


def digest_rank_score(waves: list[dict]) -> float:
    """Average the source-project scores without letting the digest rank itself."""
    scores = [
        project.get("momentumScore", 0)
        for wave in waves
        for project in wave.get("projects", [])
        if not str(project.get("url", "")).startswith("hyperadar://")
    ]
    return round(sum(scores) / len(scores), 1) if scores else 0


def weekly_post_pipeline(since: datetime) -> list[dict]:
    return [
        {
            "$match": {
                "postedAt": {"$gte": since},
                "agentHandle": {"$in": SOURCE_AGENT_HANDLES},
                "portSyncStatus": "synced",
                "evidenceContractVersion": 2,
                "legacyDuplicateOf": {"$exists": False},
            }
        },
        {"$sort": {"rankScore": -1, "postedAt": -1}},
        {"$group": {"_id": "$project.url", "post": {"$first": "$$ROOT"}}},
        {"$replaceRoot": {"newRoot": "$post"}},
        {"$sort": {"rankScore": -1, "postedAt": -1}},
        {"$limit": 15},
    ]


@tool
async def fetch_week_posts() -> str:
    """Fetch this week's top posts from all HypeRadar agents."""
    since = datetime.now(timezone.utc) - timedelta(days=7)
    cursor = mongo.db.posts.aggregate(weekly_post_pipeline(since))
    posts = await cursor.to_list(length=15)
    if not posts:
        return "No posts this week."
    lines = []
    for p in posts:
        lines.append(
            f"- [{p['agentHandle']}] {p['project']['title']} | verdict={p['verdict']} | rank={p['rankScore']}\n"
            f"  {p['body'][:100]}"
        )
    return f"This week's top {len(posts)} posts:\n" + "\n".join(lines)


@tool
async def write_digest() -> str:
    """Write one claim-safe weekly digest from synchronized source waves."""
    from _shared.hype_waves import compute_hype_waves

    now = datetime.now(timezone.utc)
    week_id = now.strftime("%Y-W%W")
    existing_digest = await mongo.db.digests.find_one(
        {
            "weekId": week_id,
            "publicationSyncStatus": {"$in": ["pending", "synced"]},
            "evidenceContractVersion": 2,
        }
    )
    reusable_snapshot = existing_digest and all(
        field in existing_digest for field in ("summary", "waves", "rankScore")
    )
    if reusable_snapshot:
        waves = existing_digest["waves"]
        summary = existing_digest["summary"]
        rank_score = existing_digest["rankScore"]
        week_of = existing_digest.get("weekOf", now)
        item_count = existing_digest.get("itemCount", 0)
    else:
        waves = compute_hype_waves()
        summary = digest_summary(waves)
        rank_score = digest_rank_score(waves)
        week_of = now
        item_count = 0

    # Create a special "digest" project entity
    project = {
        "url": f"hyperadar://digest/{week_id}",
        "title": f"Weekly Digest — {week_id}",
        "kind": "site",
        "description": summary,
        "topics": ["weekly-digest", "ai-dev"],
        "momentumScore": rank_score,
        "hypeVerdict": "emerging",
    }
    signal = {
        "source": "aggregator",
        "metric": "clustered_themes",
        "value": len(waves),
        "delta": 0,
        "summary": f"weekly digest for {week_id}",
    }
    # Include a link to the digest page in the post body
    body_with_link = f"{summary[:400]}\n\n📖 Full digest: /digest/{week_id}"
    await mongo.db.digests.update_one(
        {"weekId": week_id},
        {
            "$set": {
                "weekId": week_id,
                "weekOf": week_of,
                "summary": summary,
                "agentHandle": AGENT_HANDLE,
                "itemCount": item_count,
                "waves": waves,
                "rankScore": rank_score,
                "publicationSyncStatus": "pending",
                "evidenceContractVersion": 2,
            },
            "$unset": {"publicationSyncedAt": ""},
        },
        upsert=True,
    )
    post_id = await write_post(
        AGENT_HANDLE,
        AGENT_NAME,
        AGENT_BIO,
        SOURCE_TYPE,
        project,
        body_with_link,
        "emerging",
        signal,
        rank_score,
    )
    # Publish the digest only after its MongoDB post and Port twins have converged.
    await mongo.db.digests.update_one(
        {"weekId": week_id},
        {
            "$set": {
                "weekId": week_id,
                "weekOf": week_of,
                "summary": summary,
                "agentHandle": AGENT_HANDLE,
                "itemCount": item_count,
                "waves": waves,
                "rankScore": rank_score,
                "publicationPostId": post_id,
                "publicationSyncStatus": "synced",
                "publicationSyncedAt": datetime.now(timezone.utc),
                "evidenceContractVersion": 2,
            },
        },
        upsert=True,
    )
    return f"Digest posted for {week_id} ({len(waves)} waves) -> post {post_id}"


def build_agent(checkpointer=None):
    """Create the Deep Agents brain wired to Grove."""
    model = ChatOpenAI(
        model=os.environ["GROVE_MODEL"],
        api_key=os.environ["GROVE_API_KEY"],
        base_url=os.environ["GROVE_BASE_URL"],
        default_headers={"api-key": os.environ["GROVE_API_KEY"]},
        temperature=0.7,
    )
    kwargs = {
        "model": model,
        "tools": [fetch_week_posts, write_digest],
        "system_prompt": SYSTEM_PROMPT,
    }
    if checkpointer is not None:
        kwargs["checkpointer"] = checkpointer
    return create_deep_agent(**kwargs)
