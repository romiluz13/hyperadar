"""One-time migration: re-embed all projects with Voyage 4 Large (1024-dim).

Replaces the previous all-MiniLM-L6-v2 (384-dim) embeddings. Idempotent —
safe to re-run.

Usage:
    set -a && source .env && set +a
    uv run --frozen --project integrations/github_radar python scripts/migrate_embeddings.py
"""

import os
import sys
from pathlib import Path

import pymongo

sys.path.insert(0, str(Path(__file__).parents[1] / "integrations"))

from _shared.embeddings import embed_project  # noqa: E402


def main() -> int:
    uri = os.environ.get("MONGODB_URI")
    db_name = os.environ.get("MONGODB_DB", "hyperadar")
    if not uri:
        print("MONGODB_URI not set")
        return 1

    client = pymongo.MongoClient(uri)
    db = client[db_name]

    projects = list(
        db.projects.find({}, {"url": 1, "title": 1, "description": 1, "topics": 1})
    )
    total = len(projects)
    print(f"Migrating {total} projects to Voyage 4 Large (1024-dim)...")

    migrated = 0
    errors = 0
    for i, project in enumerate(projects, 1):
        title = project.get("title", "")
        description = project.get("description", "")
        topics = project.get("topics", [])
        url = project.get("url", "")

        if not title:
            print(f"  [{i}/{total}] SKIP (no title): {url}")
            continue

        try:
            embedding = embed_project(title, description, topics)
            result = db.projects.update_one(
                {"url": url},
                {"$set": {"embedding": embedding}},
            )
            if result.matched_count > 0:
                migrated += 1
            else:
                print(f"  [{i}/{total}] WARN: no project matched URL {url}")
                errors += 1
            if i % 10 == 0 or i == total:
                print(f"  [{i}/{total}] Migrated: {title[:50]}")
        except Exception as e:
            print(f"  [{i}/{total}] ERROR: {title[:40]} — {e}")
            errors += 1

    print(f"\nDone: {migrated} migrated, {errors} errors, {total} total")

    # Verify
    count_1024 = db.projects.count_documents({"embedding": {"$size": 1024}})
    count_384 = db.projects.count_documents({"embedding": {"$size": 384}})
    print(
        f"Verification: {count_1024} projects with 1024-dim, {count_384} with 384-dim"
    )

    client.close()

    # Exit non-zero if any errors occurred — prevents CI from masking failures
    if errors > 0:
        print(f"FAILED: {errors} errors during migration")
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
