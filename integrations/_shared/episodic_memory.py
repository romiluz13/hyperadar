"""MongoDB-backed episodic memory store for HypeRadar agents.

Stores distilled "episodes" of successful trend detections so agents can
learn over time. When scoring a new candidate, the agent retrieves similar
past episodes as few-shot examples — "last time a repo with this velocity
and these topics spiked, the verdict was correct."

Uses MongoDB Atlas Vector Search for semantic episode retrieval (the same
projects_vector_index pattern, on a dedicated episodes collection).
This is the "agents learn" MongoDB showcase.
"""
import logging
import os
from datetime import datetime, timezone

from motor.motor_asyncio import AsyncIOMotorClient
from pymongo.operations import SearchIndexModel

_uri = os.environ["MONGODB_URI"]
_db_name = os.environ.get("MONGODB_DB", "hyperadar")


def _get_db():
    client = AsyncIOMotorClient(
        _uri, maxPoolSize=10, minPoolSize=0, maxIdleTimeMS=300_000,
        connectTimeoutMS=10_000, socketTimeoutMS=30_000,
    )
    return client[_db_name]


async def store_episode(
    agent_handle: str,
    project_url: str,
    project_title: str,
    signals_preceding: dict,
    verdict: str,
    outcome: str,
    lesson: str,
    embedding: list[float] | None = None,
) -> str:
    """Store a distilled episode of a trend detection decision.

    Called after a trend is confirmed (the project actually blew up later).
    NOT called on every run — only on verified-true outcomes.

    Returns the episode _id.
    """
    db = _get_db()
    doc = {
        "agentHandle": agent_handle,
        "projectUrl": project_url,
        "projectTitle": project_title,
        "signalsPreceding": signals_preceding,
        "verdict": verdict,
        "outcome": outcome,
        "lesson": lesson,
        "storedAt": datetime.now(timezone.utc),
    }
    if embedding:
        doc["embedding"] = embedding
    result = await db.episodes.insert_one(doc)
    return str(result.inserted_id)


async def retrieve_similar_episodes(
    query_embedding: list[float],
    agent_handle: str | None = None,
    limit: int = 3,
) -> list[dict]:
    """Retrieve similar past episodes as few-shot examples.

    Uses $vectorSearch on the episodes collection. Falls back to recent
    episodes if the vector index isn't ready yet.
    """
    db = _get_db()
    filter_query = {}
    if agent_handle:
        filter_query["agentHandle"] = agent_handle

    try:
        pipeline = [
            {
                "$vectorSearch": {
                    "index": "episodes_vector_index",
                    "path": "embedding",
                    "queryVector": query_embedding,
                    "numCandidates": 20,
                    "limit": limit,
                    "filter": filter_query,
                }
            },
            {
                "$project": {
                    "_id": 0,
                    "projectTitle": 1,
                    "verdict": 1,
                    "outcome": 1,
                    "lesson": 1,
                    "signalsPreceding": 1,
                    "score": {"$meta": "vectorSearchScore"},
                }
            },
        ]
        results = await db.episodes.aggregate(pipeline).to_list(length=limit)
        if results:
            return results
    except Exception as e:
        logging.warning("episodes vector search failed, falling back to recent: %s", e)

    # Fallback: return most recent episodes (no semantic search)
    cursor = db.episodes.find(
        filter_query,
        {"_id": 0, "projectTitle": 1, "verdict": 1, "outcome": 1, "lesson": 1, "signalsPreceding": 1},
    ).sort("storedAt", -1).limit(limit)
    return await cursor.to_list(length=limit)


async def get_episode_count() -> int:
    """Total episodes stored — for the showcase dashboard."""
    db = _get_db()
    return await db.episodes.count_documents({})


def setup_episodes_collection():
    """Create the episodes collection + vector search index. Run once."""
    import pymongo
    from pymongo.errors import CollectionInvalid

    client = pymongo.MongoClient(_uri)
    db = client[_db_name]

    # Create collection
    try:
        db.create_collection("episodes")
        print("✓ created episodes collection")
    except CollectionInvalid:
        print("  episodes already exists ✓")

    # Create vector index
    try:
        model = SearchIndexModel(
            name="episodes_vector_index",
            type="vectorSearch",
            definition={"fields": [
                {"type": "vector", "path": "embedding", "numDimensions": 384, "similarity": "cosine"},
                {"type": "filter", "path": "agentHandle"},
            ]},
        )
        db.episodes.create_search_index(model=model)
        print("✓ episodes vector index created")
    except Exception as e:
        if "already exists" in str(e):
            print("  episodes_vector_index exists ✓")
        else:
            print(f"  ⚠ episodes index: {e}")
