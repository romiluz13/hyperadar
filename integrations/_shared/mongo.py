"""MongoDB async client + upsert helpers for all HypeRadar agent-creators.

Serverless/long-running pool config per docs/reference/mongodb-connection.md.
Source of truth for data + intelligence; Port entities are the catalog twin.
"""

import asyncio
import os
import uuid
from datetime import datetime, timedelta, timezone

from bson import ObjectId
from pymongo import AsyncMongoClient, ReturnDocument
from pymongo.errors import DuplicateKeyError

MONGO_CONNECT_TIMEOUT_SECONDS = 10
MONGO_SERVER_SELECTION_TIMEOUT_SECONDS = 10
MONGO_SOCKET_TIMEOUT_SECONDS = 30
MONGO_IO_BUDGET_SECONDS = (
    MONGO_CONNECT_TIMEOUT_SECONDS
    + MONGO_SERVER_SELECTION_TIMEOUT_SECONDS
    + MONGO_SOCKET_TIMEOUT_SECONDS
)
SIGNAL_LEASE_SECONDS = 120
SIGNAL_RECEIPT_WAIT_SECONDS = 125
SIGNAL_RECEIPT_POLL_SECONDS = 0.1

_uri = os.environ["MONGODB_URI"]
_db_name = os.environ.get("MONGODB_DB", "hyperadar")

# Async clients are single-event-loop objects. Production uses one loop; the
# per-loop cache also keeps isolated pytest loops safe without rebuilding a pool
# for every database operation.
_clients: dict[asyncio.AbstractEventLoop, AsyncMongoClient] = {}


def _get_db():
    loop = asyncio.get_running_loop()
    client = _clients.get(loop)
    if client is None:
        client = AsyncMongoClient(
            _uri,
            maxPoolSize=10,
            minPoolSize=0,
            maxIdleTimeMS=300_000,
            connectTimeoutMS=MONGO_CONNECT_TIMEOUT_SECONDS * 1000,
            serverSelectionTimeoutMS=MONGO_SERVER_SELECTION_TIMEOUT_SECONDS * 1000,
            socketTimeoutMS=MONGO_SOCKET_TIMEOUT_SECONDS * 1000,
        )
        _clients[loop] = client
    return client[_db_name]


async def close_client() -> None:
    """Close the client owned by the current event loop."""
    client = _clients.pop(asyncio.get_running_loop(), None)
    if client is not None:
        await client.close()


def __getattr__(name):
    """Module-level lazy access: `mongo.db` returns the database on first use."""
    if name == "db":
        return _get_db()
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")


def _now() -> datetime:
    return datetime.now(timezone.utc)


async def upsert_project(
    project: dict, embedding: list[float] | None = None, session=None
) -> dict:
    """Upsert a project doc (source of truth for rich data + vector). Returns it."""
    from .slug import project_slug_for_url

    now = _now()
    url = project["url"]
    doc = {
        **project,
        "slug": project_slug_for_url(url),
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
        session=session,
    )
    return doc


async def ensure_signal(post_id: str, signal: dict) -> None:
    """Append one raw time-series signal through a recoverable receipt lease."""
    database = _get_db()
    receipt_on_insert = {
        "_id": post_id,
        "state": "pending",
        "signal": signal,
        "createdAt": _now(),
    }
    try:
        receipt = await database.signal_receipts.find_one_and_update(
            {"_id": post_id},
            {"$setOnInsert": receipt_on_insert},
            upsert=True,
            return_document=ReturnDocument.AFTER,
        )
    except DuplicateKeyError:
        receipt = await database.signal_receipts.find_one({"_id": post_id})
    if not receipt or receipt.get("signal") != signal:
        raise RuntimeError(f"Signal receipt conflict for post {post_id}")

    owner = str(uuid.uuid4())
    attempts = int(SIGNAL_RECEIPT_WAIT_SECONDS / SIGNAL_RECEIPT_POLL_SECONDS)
    for _ in range(attempts):
        receipt = await database.signal_receipts.find_one({"_id": post_id})
        if receipt and receipt.get("state") == "complete":
            if not receipt.get("signalId"):
                raise RuntimeError(
                    f"Completed signal receipt has no signal for {post_id}"
                )
            return

        now = _now()
        leased = await database.signal_receipts.find_one_and_update(
            {
                "_id": post_id,
                "state": {"$ne": "complete"},
                "$or": [
                    {"leaseUntil": {"$lte": now}},
                    {"leaseUntil": {"$exists": False}},
                    {"leaseOwner": owner},
                ],
            },
            {
                "$set": {
                    "leaseOwner": owner,
                    "leaseUntil": now + timedelta(seconds=SIGNAL_LEASE_SECONDS),
                },
                "$inc": {"leaseEpoch": 1},
            },
            return_document=ReturnDocument.AFTER,
        )
        if leased and leased.get("leaseOwner") == owner:
            lease_epoch = leased["leaseEpoch"]
            renewed_at = _now()
            renewed = await database.signal_receipts.update_one(
                {
                    "_id": post_id,
                    "state": {"$ne": "complete"},
                    "leaseOwner": owner,
                    "leaseEpoch": lease_epoch,
                    "leaseUntil": {"$gt": renewed_at},
                },
                {
                    "$set": {
                        "leaseUntil": renewed_at
                        + timedelta(seconds=SIGNAL_LEASE_SECONDS)
                    }
                },
            )
            if renewed.matched_count != 1:
                continue
            stored = await database.signals.find_one({"postId": post_id}, {"_id": 1})
            if stored is None:
                inserted = await database.signals.insert_one(
                    {"capturedAt": _now(), **signal, "postId": post_id}
                )
                signal_id = inserted.inserted_id
            else:
                signal_id = stored["_id"]
            completed = await database.signal_receipts.update_one(
                {
                    "_id": post_id,
                    "state": {"$ne": "complete"},
                    "leaseOwner": owner,
                    "leaseEpoch": lease_epoch,
                    "leaseUntil": {"$gt": _now()},
                },
                {
                    "$set": {
                        "state": "complete",
                        "signalId": signal_id,
                        "completedAt": _now(),
                    },
                    "$unset": {"leaseOwner": "", "leaseUntil": ""},
                },
            )
            if completed.matched_count != 1:
                winner = await database.signal_receipts.find_one({"_id": post_id})
                if (
                    winner
                    and winner.get("state") == "complete"
                    and winner.get("signal") == signal
                    and winner.get("signalId")
                ):
                    return
                raise RuntimeError(f"Signal receipt lease lost for post {post_id}")
            return
        await asyncio.sleep(SIGNAL_RECEIPT_POLL_SECONDS)
    raise RuntimeError(f"Signal receipt lease timed out for post {post_id}")


async def claim_post(publication_key: str, post: dict) -> tuple[str, bool]:
    """Atomically claim one agent/project/day publication identity."""
    candidate_id = ObjectId()
    post_on_insert = {
        "_id": candidate_id,
        **post,
        "publicationKey": publication_key,
        "postedAt": _now(),
        "reactionCounts": {"likes": 0, "comments": 0, "shares": 0},
    }
    try:
        claimed = await _get_db().posts.find_one_and_update(
            {"publicationKey": publication_key},
            {"$setOnInsert": post_on_insert},
            upsert=True,
            return_document=ReturnDocument.AFTER,
        )
    except DuplicateKeyError:
        claimed = await _get_db().posts.find_one({"publicationKey": publication_key})
    if not claimed:
        raise RuntimeError(f"Publication claim {publication_key} did not converge")
    return str(claimed["_id"]), claimed["_id"] == candidate_id


async def attach_publication_identity(
    post_id: ObjectId, publication_key: str, publication_day: str
) -> dict:
    """Backfill one legacy post identity, converging on an existing claim if raced."""
    try:
        await _get_db().posts.update_one(
            {"_id": post_id, "publicationKey": {"$exists": False}},
            {
                "$set": {
                    "publicationKey": publication_key,
                    "publicationDay": publication_day,
                }
            },
        )
    except DuplicateKeyError:
        pass
    claimed = await _get_db().posts.find_one({"publicationKey": publication_key})
    if claimed:
        return claimed
    legacy = await _get_db().posts.find_one({"_id": post_id})
    if not legacy:
        raise RuntimeError(f"Legacy publication {post_id} disappeared during claim")
    return legacy


async def get_momentum_history(
    project_id: str,
    source: str,
    metric: str,
    limit: int = 20,
) -> list[dict]:
    """Read one comparable source/metric series for a project."""
    database = _get_db()
    published_posts = await database.posts.find(
        {
            "project.url": project_id,
            "portSyncStatus": "synced",
            "evidenceContractVersion": 2,
            "legacyDuplicateOf": {"$exists": False},
        },
        {"_id": 1},
    ).to_list(length=None)
    post_ids = [str(post["_id"]) for post in published_posts]
    if not post_ids:
        return []
    receipts = await database.signal_receipts.find(
        {
            "_id": {"$in": post_ids},
            "state": "complete",
            "signal.projectId": project_id,
            "signal.source": source,
            "signal.metric": metric,
        },
        {"signalId": 1},
    ).to_list(length=None)
    signal_ids = [
        receipt["signalId"] for receipt in receipts if receipt.get("signalId")
    ]
    if not signal_ids:
        return []
    cursor = (
        database.signals.find(
            {
                "_id": {"$in": signal_ids},
                "projectId": project_id,
                "source": source,
                "metric": metric,
            }
        )
        .sort("capturedAt", -1)
        .limit(limit)
    )
    return await cursor.to_list(length=limit)


async def get_prior_post_count(project_id: str) -> int:
    """How many posts already exist for this project (dedup + sustainedness hint)."""
    return await _get_db().posts.count_documents({"project.url": project_id})
