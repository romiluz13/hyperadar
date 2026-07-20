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


def main() -> None:
    uri = os.environ.get("MONGODB_URI")
    db_name = os.environ.get("MONGODB_DB", "hyperadar")
    if not uri:
        print("MONGODB_URI not set")
        sys.exit(1)

    client = pymongo.MongoClient(uri)
    db = client[db_name]

    projects = list(
        db.projects.find({}, {"url": 1, "title": 1, "description": 1, "topics": 1})
    )
    total = len(projects)
    print(f"Migrating {total} projects to Voyage 4 Large (1024-dim)...")

    migrated = 0
    skipped = 0
    for i, project in enumerate(projects, 1):
        title = project.get("title", "")
        description = project.get("description", "")
        topics = project.get("topics", [])
        url = project.get("url", "")

        if not title:
            print(f"  [{i}/{total}] SKIP (no title): {url}")
            skipped += 1
            continue

        try:
            embedding = embed_project(title, description, topics)
            db.projects.update_one(
                {"url": url},
                {"$set": {"embedding": embedding}},
            )
            migrated += 1
            if i % 10 == 0 or i == total:
                print(f"  [{i}/{total}] Migrated: {title[:50]}")
        except Exception as e:
            print(f"  [{i}/{total}] ERROR: {title[:40]} — {e}")
            skipped += 1

    print(f"\nDone: {migrated} migrated, {skipped} skipped, {total} total")

    # Verify
    count_1024 = db.projects.count_documents({"embedding": {"$size": 1024}})
    count_384 = db.projects.count_documents({"embedding": {"$size": 384}})
    print(
        f"Verification: {count_1024} projects with 1024-dim, {count_384} with 384-dim"
    )

    client.close()


if __name__ == "__main__":
    main()
