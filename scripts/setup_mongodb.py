"""Reproducible MongoDB Atlas setup for HypeRadar.

Creates all collections (signals as time-series), schema validators, indexes,
and the Atlas Vector Search index. Idempotent — safe to re-run.

Usage:
    set -a && source .env && set +a
    uv run --with pymongo --with 'pymongo[srv]' python scripts/setup_mongodb.py
"""

import os
import sys

import pymongo
from pymongo.errors import CollectionInvalid, OperationFailure
from pymongo.operations import SearchIndexModel

DB_NAME = os.environ.get("MONGODB_DB", "hyperadar")


def ensure_collection(db, name, **kwargs):
    try:
        db.create_collection(name, **kwargs)
        print(f"✓ created {name}")
    except CollectionInvalid:
        print(f"  {name} already exists ✓")


def ensure_index(col, keys, **kwargs):
    name = kwargs.get("name", "idx")
    try:
        col.create_index(keys, **kwargs)
        print(f"  ✓ {col.name}.{name}")
    except OperationFailure as e:
        if "already exists" in str(e):
            print(f"  {col.name}.{name} exists ✓")
        else:
            print(f"  {col.name}.{name} err: {e}")


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

    # 4. reactions
    ensure_collection(db, "reactions")
    # Unique: one reaction per type per user per post (like AND comment allowed)
    ensure_index(
        db.reactions,
        [("postId", 1), ("userId", 1), ("type", 1)],
        unique=True,
        name="one_reaction_per_type",
    )
    ensure_index(db.reactions, [("postId", 1), ("type", 1)], name="post_type")

    # 5. agents
    ensure_collection(db, "agents")
    ensure_index(db.agents, "handle", unique=True, name="handle_unique")

    # 6. digests
    ensure_collection(db, "digests")
    ensure_index(db.digests, "weekOf", name="week")

    # 7. embeddings_audit
    ensure_collection(db, "embeddings_audit")

    # 8. Atlas Vector Search index on projects.embedding (384-dim, all-MiniLM-L6-v2)
    #    Production swap: Atlas auto-embedding (Voyage AI) — same query, different generation.
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
            print(f"  ⚠ vector index: {e}")

    print("\n=== Collections ===")
    for col in sorted(db.list_collection_names()):
        if not col.startswith("system."):
            print(f"  - {col}")
    print("\nSetup complete.")


if __name__ == "__main__":
    sys.exit(main())
