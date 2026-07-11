"""Seed episodic memory from existing confirmed trends.

Takes the projects already posted by agents and creates episodes
representing "this trend was detected and posted about" — the starting
memory so new agent runs can retrieve similar past episodes.
"""
import asyncio
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pymongo  # noqa: E402
from _shared.episodic_memory import store_episode  # noqa: E402
from _shared.embeddings import embed_project  # noqa: E402


async def seed_episodes():
    client = pymongo.MongoClient(os.environ["MONGODB_URI"])
    db = client[os.environ.get("MONGODB_DB", "hyperadar")]

    # Get all projects that have been posted about
    pipeline = [
        {"$group": {
            "_id": "$project.url",
            "title": {"$first": "$project.title"},
            "verdict": {"$first": "$verdict"},
            "agentHandle": {"$first": "$agentHandle"},
            "momentumScore": {"$first": "$project.momentumScore"},
        }},
    ]
    posted = list(db.posts.aggregate(pipeline))
    print(f"Found {len(posted)} posted projects to seed as episodes")

    for p in posted:
        url = p["_id"]
        title = p["title"]
        verdict = p["verdict"]
        agent = p["agentHandle"]
        momentum = p.get("momentumScore", 0)

        # Get the project's embedding
        project = db.projects.find_one({"url": url})
        embedding = project.get("embedding") if project else None

        if not embedding:
            # Generate one from the title
            embedding = embed_project(title, project.get("description", "") if project else "", project.get("topics", []) if project else [])

        # Create the episode
        episode_id = await store_episode(
            agent_handle=agent,
            project_url=url,
            project_title=title,
            signals_preceding={"momentumScore": momentum, "source": agent},
            verdict=verdict,
            outcome="posted — trend detected and shared with the feed",
            lesson=f"{agent} flagged {title} as '{verdict}' with momentum {momentum}. "
                   f"This is a reference case for similar future candidates.",
            embedding=embedding,
        )
        print(f"  ✓ seeded episode: {title} ({episode_id})")

    count = await asyncio.get_event_loop().run_in_executor(
        None, lambda: db.episodes.count_documents({})
    )
    print(f"\nTotal episodes in store: {count}")


if __name__ == "__main__":
    asyncio.run(seed_episodes())
