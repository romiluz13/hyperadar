"""MongoDB async client + upsert helpers for the @github-radar agent.

Serverless/long-running pool config per docs/reference/mongodb-connection.md.
Source of truth for data + intelligence; Port entities are the catalog twin.
"""
import os
from datetime import datetime, timezone

from motor.motor_asyncio import AsyncIOMotorClient

_uri = os.environ["MONGODB_URI"]
_db_name = os.environ.get("MONGODB_DB", "hyperadar")

# Long-running process pattern: small pre-warmed pool, 5min idle.
_client = AsyncIOMotorClient(
    _uri,
    maxPoolSize=10,
    minPoolSize=2,
    maxIdleTimeMS=300_000,
    connectTimeoutMS=10_000,
    socketTimeoutMS=30_000,
)
db = _client[_db_name]


def _now() -> datetime:
    return datetime.now(timezone.utc)


async def upsert_project(project: dict) -> dict:
    """Upsert a project doc (source of truth for rich data + vector). Returns it."""
    now = _now()
    url = project["url"]
    doc = {
        **project,
        "lastSeenAt": now,
    }
    # firstSeenAt only set on insert
    await db.projects.update_one(
        {"url": url},
        {
            "$set": doc,
            "$setOnInsert": {"firstSeenAt": now},
        },
        upsert=True,
    )
    return doc


async def insert_signal(signal: dict) -> None:
    """Insert a raw hype signal into the time-series collection."""
    await db.signals.insert_one({"capturedAt": _now(), **signal})


async def insert_post(post: dict) -> str:
    """Insert an agent-authored post. Returns inserted _id as string."""
    post = {**post, "postedAt": _now(), "reactionCounts": {"likes": 0, "comments": 0, "shares": 0}}
    res = await db.posts.insert_one(post)
    return str(res.inserted_id)


async def get_momentum_history(project_id: str, limit: int = 20) -> list[dict]:
    """Read recent signals for a project from the time-series collection."""
    cursor = db.signals.find({"projectId": project_id}).sort("capturedAt", -1).limit(limit)
    return await cursor.to_list(length=limit)


async def get_prior_post_count(project_id: str) -> int:
    """How many posts already exist for this project (dedup + sustainedness hint)."""
    return await db.posts.count_documents({"project.url": project_id})
