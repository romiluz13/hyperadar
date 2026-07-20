"""Update Atlas search indexes for Voyage 4 Large (1024-dim) hybrid search.

Replaces the projects_vector_index (384-dim MiniLM) with 1024-dim Voyage,
and creates the posts_search_index (Atlas Search, dynamic mappings) for the
$text leg of $rankFusion.

Usage:
    set -a && source .env && set +a
    uv run --frozen --project integrations/github_radar python scripts/update_search_indexes.py
"""

import os
import sys

import pymongo
from pymongo.errors import OperationFailure
from pymongo.operations import SearchIndexModel


def main() -> None:
    uri = os.environ.get("MONGODB_URI")
    db_name = os.environ.get("MONGODB_DB", "hyperadar")
    if not uri:
        print("MONGODB_URI not set")
        sys.exit(1)

    client = pymongo.MongoClient(uri)
    db = client[db_name]
    print(f"Connected to {db_name} (server {client.server_info()['version']})")

    # 1. Drop the old 384-dim projects_vector_index
    print("\n=== Dropping old 384-dim projects_vector_index ===")
    try:
        db.projects.drop_search_index("projects_vector_index")
        print("✓ dropped old projects_vector_index")
    except OperationFailure as e:
        if "not found" in str(e).lower() or "does not exist" in str(e).lower():
            print("  projects_vector_index not found (already dropped)")
        else:
            print(f"  note: {e}")

    # 2. Create new 1024-dim projects_vector_index (Voyage 4 Large)
    print("\n=== Creating 1024-dim projects_vector_index (Voyage 4 Large) ===")
    try:
        model = SearchIndexModel(
            name="projects_vector_index",
            type="vectorSearch",
            definition={
                "fields": [
                    {
                        "type": "vector",
                        "path": "embedding",
                        "numDimensions": 1024,
                        "similarity": "cosine",
                    },
                    {"type": "filter", "path": "url"},
                ]
            },
        )
        db.projects.create_search_index(model=model)
        print("✓ created projects_vector_index (1024-dim)")
    except OperationFailure as e:
        if "already exists" in str(e):
            print("  projects_vector_index already exists ✓")
        else:
            raise

    # 3. Create posts_search_index (Atlas Search, dynamic mappings)
    print("\n=== Creating posts_search_index (Atlas Search) ===")
    try:
        posts_model = SearchIndexModel(
            name="posts_search_index",
            type="search",
            definition={"mappings": {"dynamic": True}},
        )
        db.posts.create_search_index(model=posts_model)
        print("✓ created posts_search_index")
    except OperationFailure as e:
        if "already exists" in str(e):
            print("  posts_search_index already exists ✓")
        else:
            raise

    # 4. Update episodes_vector_index to 1024-dim
    print("\n=== Updating episodes_vector_index to 1024-dim ===")
    try:
        db.episodes.drop_search_index("episodes_vector_index")
        print("✓ dropped old episodes_vector_index")
    except OperationFailure as e:
        if "not found" in str(e).lower() or "does not exist" in str(e).lower():
            print("  episodes_vector_index not found (already dropped)")
        else:
            print(f"  note: {e}")

    try:
        episodes_model = SearchIndexModel(
            name="episodes_vector_index",
            type="vectorSearch",
            definition={
                "fields": [
                    {
                        "type": "vector",
                        "path": "embedding",
                        "numDimensions": 1024,
                        "similarity": "cosine",
                    },
                    {"type": "filter", "path": "agentHandle"},
                ]
            },
        )
        db.episodes.create_search_index(model=episodes_model)
        print("✓ created episodes_vector_index (1024-dim)")
    except OperationFailure as e:
        if "already exists" in str(e):
            print("  episodes_vector_index already exists ✓")
        else:
            raise

    print("\n=== Current search indexes ===")
    for idx in db.projects.list_search_indexes():
        print(f"  projects: {idx['name']} ({idx['type']})")
    for idx in db.posts.list_search_indexes():
        print(f"  posts: {idx['name']} ({idx['type']})")
    try:
        for idx in db.episodes.list_search_indexes():
            print(f"  episodes: {idx['name']} ({idx['type']})")
    except Exception:
        pass

    print("\nIndex update complete. Indexes may take 1-5 minutes to build.")
    client.close()


if __name__ == "__main__":
    main()
