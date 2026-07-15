"""Reproducible MongoDB Atlas setup for HypeRadar.

Creates all collections (signals as time-series), schema validators, indexes,
and the Atlas Vector Search index. Idempotent — safe to re-run.

Usage:
    set -a && source .env && set +a
    uv run --frozen --project integrations/github_radar python scripts/setup_mongodb.py
"""

import os
import sys
from pathlib import Path

import pymongo
from pymongo.errors import CollectionInvalid, OperationFailure
from pymongo.operations import SearchIndexModel

sys.path.insert(0, str(Path(__file__).parents[1] / "integrations"))

from _shared.publication_identity import publication_day, publication_key

DB_NAME = os.environ.get("MONGODB_DB", "hyperadar")


def ensure_collection(db, name, **kwargs):
    try:
        db.create_collection(name, **kwargs)
        print(f"✓ created {name}")
    except CollectionInvalid:
        print(f"  {name} already exists ✓")


def ensure_index(col, keys, **kwargs):
    name = kwargs.get("name", "idx")
    col.create_index(keys, **kwargs)
    print(f"  ✓ {col.name}.{name}")


def ensure_reaction_indexes(col):
    reaction_key = [("postId", 1), ("userId", 1), ("type", 1)]
    network_like_key = [("postId", 1), ("rankIdentity", 1), ("type", 1)]
    guard_name = "reaction_migration_guard"
    guard_key = [*reaction_key, ("_likeMigrationGuard", 1)]
    index_info = col.index_information()
    legacy_names = []
    for name, details in index_info.items():
        if (
            details.get("key") == reaction_key
            and details.get("unique")
            and details.get("partialFilterExpression") != {"type": "like"}
        ):
            legacy_names.append(name)

    if legacy_names and guard_name not in index_info:
        ensure_index(col, guard_key, unique=True, name=guard_name)
    for name in legacy_names:
        col.drop_index(name)
        print(f"  ✓ removed legacy {col.name}.{name}")

    ensure_index(
        col,
        reaction_key,
        unique=True,
        partialFilterExpression={"type": "like"},
        name="one_like_per_user",
    )
    target = col.index_information().get("one_like_per_user", {})
    if not (
        target.get("key") == reaction_key
        and target.get("unique")
        and target.get("partialFilterExpression") == {"type": "like"}
    ):
        raise RuntimeError("one_like_per_user index did not converge")
    if guard_name in col.index_information():
        col.drop_index(guard_name)
        print(f"  ✓ removed temporary {col.name}.{guard_name}")
    ensure_index(
        col,
        network_like_key,
        unique=True,
        partialFilterExpression={
            "type": "like",
            "rankIdentity": {"$type": "string"},
        },
        name="one_like_per_network",
    )
    ensure_index(col, [("postId", 1), ("type", 1)], name="post_type")
    ensure_index(
        col,
        "operationId",
        unique=True,
        partialFilterExpression={"operationId": {"$type": "string"}},
        name="one_reaction_per_operation",
    )


def backfill_publication_identities(posts):
    """Assign one deterministic claim per legacy agent/project/UTC-day group."""
    legacy_posts = list(posts.find({"publicationKey": {"$exists": False}}))
    legacy_posts.sort(
        key=lambda post: (str(post.get("postedAt", "")), str(post["_id"]))
    )
    for post in legacy_posts:
        agent_handle = post.get("agentHandle")
        project_url = post.get("project", {}).get("url")
        posted_at = post.get("postedAt")
        if not agent_handle or not project_url or not posted_at:
            raise RuntimeError(
                f"Cannot derive publication identity for legacy post {post['_id']}"
            )
        day = publication_day(posted_at)
        key = publication_key(agent_handle, project_url, day)
        claimed = posts.find_one({"publicationKey": key}, {"_id": 1})
        if claimed:
            posts.update_one(
                {"_id": post["_id"]},
                {
                    "$set": {
                        "publicationDay": day,
                        "legacyDuplicateOf": str(claimed["_id"]),
                    }
                },
            )
            continue
        posts.update_one(
            {"_id": post["_id"]},
            {"$set": {"publicationDay": day, "publicationKey": key}},
        )


def reconcile_reaction_counts(db):
    """Rebuild counters transactionally so concurrent reactions force a retry."""
    reconciled = 0
    for post in db.posts.find({}, {"_id": 1}):

        def reconcile(session):
            event_counts = {}
            for group in db.reactions.aggregate(
                [
                    {
                        "$match": {
                            "postId": str(post["_id"]),
                            "type": {"$in": ["like", "comment", "share"]},
                        }
                    },
                    {
                        "$group": {
                            "_id": {"postId": "$postId", "type": "$type"},
                            "count": {"$sum": 1},
                        }
                    },
                ],
                session=session,
            ):
                reaction_type = group.get("_id", {}).get("type")
                if reaction_type:
                    event_counts[reaction_type] = group["count"]
            db.posts.update_one(
                {"_id": post["_id"]},
                {
                    "$set": {
                        "reactionCounts": {
                            "likes": event_counts.get("like", 0),
                            "comments": event_counts.get("comment", 0),
                            "shares": event_counts.get("share", 0),
                        }
                    }
                },
                session=session,
            )

        with db.client.start_session() as session:
            session.with_transaction(reconcile)
        reconciled += 1
    return reconciled


def main():
    client = pymongo.MongoClient(os.environ["MONGODB_URI"])
    db = client[DB_NAME]
    print(f"Connected to {DB_NAME} (server {client.server_info()['version']})")

    # 1. signals — time-series
    ensure_collection(
        db,
        "signals",
        timeseries={
            "timeField": "capturedAt",
            "metaField": "projectId",
            "granularity": "hours",
        },
    )
    ensure_index(db.signals, [("projectId", 1), ("capturedAt", -1)], name="proj_time")
    ensure_index(db.signals, "postId", name="post_id")

    # 2. projects — with schema validation
    ensure_collection(
        db,
        "projects",
        validator={
            "$jsonSchema": {
                "bsonType": "object",
                "required": ["url", "title", "kind", "firstSeenAt"],
                "properties": {
                    "url": {"bsonType": "string"},
                    "title": {"bsonType": "string"},
                    "kind": {
                        "bsonType": "string",
                        "enum": ["repo", "video", "thread", "site"],
                    },
                    "description": {"bsonType": "string"},
                    "slug": {"bsonType": "string"},
                    "legacySlugs": {
                        "bsonType": "array",
                        "items": {"bsonType": "string"},
                    },
                    "retiredPortProjectIds": {
                        "bsonType": "array",
                        "items": {"bsonType": "string"},
                    },
                    "topics": {"bsonType": "array", "items": {"bsonType": "string"}},
                    "momentumScore": {
                        "bsonType": "number",
                        "minimum": 0,
                        "maximum": 100,
                    },
                    "hypeVerdict": {"bsonType": "string"},
                    "firstSeenAt": {"bsonType": "date"},
                    "lastSeenAt": {"bsonType": "date"},
                },
            }
        },
        validationLevel="moderate",
        validationAction="warn",
    )
    ensure_index(db.projects, "url", unique=True, name="url_unique")
    ensure_index(db.projects, "slug", name="slug")
    ensure_index(db.projects, "legacySlugs", name="legacy_slugs")

    # 3. posts — with schema validation
    ensure_collection(
        db,
        "posts",
        validator={
            "$jsonSchema": {
                "bsonType": "object",
                "required": ["agentHandle", "body", "postedAt", "project"],
                "properties": {
                    "agentHandle": {"bsonType": "string"},
                    "body": {"bsonType": "string", "maxLength": 2000},
                    "verdict": {"bsonType": "string"},
                    "postedAt": {"bsonType": "date"},
                    "publicationDay": {"bsonType": "string"},
                    "publicationKey": {"bsonType": "string"},
                    "rankScore": {"bsonType": "number"},
                    "reactionCounts": {"bsonType": "object"},
                    "project": {"bsonType": "object", "required": ["url", "title"]},
                },
            }
        },
        validationLevel="moderate",
        validationAction="warn",
    )
    ensure_index(db.posts, [("rankScore", -1), ("postedAt", -1)], name="feed_rank")
    ensure_index(db.posts, [("agentHandle", 1), ("postedAt", -1)], name="agent_posts")
    ensure_index(db.posts, "project.url", name="project_url")
    backfill_publication_identities(db.posts)
    ensure_index(
        db.posts,
        "publicationKey",
        unique=True,
        partialFilterExpression={"publicationKey": {"$type": "string"}},
        name="one_daily_agent_project_publication",
    )

    # 4. reactions
    ensure_collection(db, "reactions")
    # Likes store desired state; shares/comments deduplicate by operationId.
    ensure_reaction_indexes(db.reactions)

    ensure_collection(db, "reaction_rate_limits")
    ensure_index(
        db.reaction_rate_limits,
        "expiresAt",
        expireAfterSeconds=0,
        name="expire_rate_limits",
    )
    ensure_collection(db, "project_reconcile_leases")
    ensure_index(
        db.project_reconcile_leases,
        "leaseUntil",
        expireAfterSeconds=0,
        name="expire_project_reconcile_leases",
    )
    print(f"  ✓ reconciled reaction counters for {reconcile_reaction_counts(db)} posts")

    # 4b. signal append receipts — unique regular-collection lease for time-series writes
    ensure_collection(db, "signal_receipts")
    ensure_index(db.signal_receipts, "state", name="state")

    # 4c. Explicit provenance for pre-receipt time-series history
    ensure_collection(db, "legacy_signal_verifications")
    ensure_index(
        db.legacy_signal_verifications,
        [("projectId", 1), ("postId", 1)],
        name="project_post",
    )

    # 5. agents
    ensure_collection(db, "agents")
    ensure_index(db.agents, "handle", unique=True, name="handle_unique")

    # 6. digests
    ensure_collection(db, "digests")
    ensure_index(db.digests, "weekOf", name="week")

    # 7. embeddings_audit
    ensure_collection(db, "embeddings_audit")
    ensure_index(
        db.embeddings_audit,
        "postId",
        unique=True,
        partialFilterExpression={"postId": {"$type": "string"}},
        name="one_embedding_audit_per_post",
    )

    # 8. episodes (episodic memory — T8)
    ensure_collection(db, "episodes")
    ensure_index(db.episodes, "projectUrl", name="project_url")

    # 9. Atlas Vector Search index on projects.embedding (384-dim, all-MiniLM-L6-v2)
    #    Automated embedding remains an optional future architecture change.
    try:
        model = SearchIndexModel(
            name="projects_vector_index",
            type="vectorSearch",
            definition={
                "fields": [
                    {
                        "type": "vector",
                        "path": "embedding",
                        "numDimensions": 384,
                        "similarity": "cosine",
                    },
                    {"type": "filter", "path": "url"},
                ]
            },
        )
        db.projects.create_search_index(model=model)
        print("✓ vector search index created (projects_vector_index)")
    except OperationFailure as e:
        if "already exists" in str(e):
            print("  projects_vector_index exists ✓")
        else:
            raise

    # 10. Atlas Vector Search index on episodes.embedding (episodic memory — T8)
    try:
        episodes_model = SearchIndexModel(
            name="episodes_vector_index",
            type="vectorSearch",
            definition={
                "fields": [
                    {
                        "type": "vector",
                        "path": "embedding",
                        "numDimensions": 384,
                        "similarity": "cosine",
                    },
                    {"type": "filter", "path": "agentHandle"},
                ]
            },
        )
        db.episodes.create_search_index(model=episodes_model)
        print("✓ vector search index created (episodes_vector_index)")
    except OperationFailure as e:
        if "already exists" in str(e):
            print("  episodes_vector_index exists ✓")
        else:
            raise

    print("\n=== Collections ===")
    for col in sorted(db.list_collection_names()):
        if not col.startswith("system."):
            print(f"  - {col}")
    print("\nSetup complete.")


if __name__ == "__main__":
    sys.exit(main())
