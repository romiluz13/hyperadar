"""MongoDB async client + upsert helpers for all HypeRadar agent-creators.

Serverless/long-running pool config per docs/reference/mongodb-connection.md.
Source of truth for data + intelligence; Port entities are the catalog twin.
"""

import os
from datetime import datetime, timezone

from motor.motor_asyncio import AsyncIOMotorClient

_uri = os.environ["MONGODB_URI"]
_db_name = os.environ.get("MONGODB_DB", "hyperadar")

# Lazy client — created on first use so it binds to the current event loop
# (critical for tests where each test gets a fresh loop).
_client: AsyncIOMotorClient | None = None


def _get_db():
    # Create a fresh client each call — Motor clients are cheap to construct
    # and bind to the current event loop. Caching breaks across test loops.
    # In production, the agent runs in a single loop so this is called once.
    client = AsyncIOMotorClient(
        _uri,
        maxPoolSize=10,
        minPoolSize=2,
        maxIdleTimeMS=300_000,
        connectTimeoutMS=10_000,
        socketTimeoutMS=30_000,
    )
    return client[_db_name]


def __getattr__(name):
    """Module-level lazy access: `mongo.db` returns the database on first use."""
    if name == "db":
        return _get_db()
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")


def _now() -> datetime:
    return datetime.now(timezone.utc)


async def upsert_project(project: dict, embedding: list[float] | None = None) -> dict:
    """Upsert a project doc (source of truth for rich data + vector). Returns it."""
    from .slug import slug_for_url

    now = _now()
    url = project["url"]
    doc = {
        **project,
        "slug": slug_for_url(url),
        "lastSeenAt": now,
    }
    if embedding is not None:
        doc["embedding"] = embedding
    # firstSeenAt only set on insert
    await _get_db().projects.update_one(
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
    await _get_db().signals.insert_one({"capturedAt": _now(), **signal})


async def insert_post(post: dict) -> str:
    """Insert an agent-authored post. Returns inserted _id as string."""
    post = {
        **post,
        "postedAt": _now(),
        "reactionCounts": {"likes": 0, "comments": 0, "shares": 0},
    }
    res = await _get_db().posts.insert_one(post)
    return str(res.inserted_id)


async def get_momentum_history(project_id: str, limit: int = 20) -> list[dict]:
    """Read recent signals for a project from the time-series collection."""
    cursor = (
        _get_db()
        .signals.find({"projectId": project_id})
        .sort("capturedAt", -1)
        .limit(limit)
    )
    return await cursor.to_list(length=limit)


async def get_prior_post_count(project_id: str) -> int:
    """How many posts already exist for this project (dedup + sustainedness hint)."""
    return await _get_db().posts.count_documents({"project.url": project_id})
