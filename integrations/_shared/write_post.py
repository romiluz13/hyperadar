"""Shared write path — the twin-model write (MongoDB + Port) for all agents.

Every agent-creator calls write_post() to persist a hype post. This handles:
1. Embed the project (semantic identity for $vectorSearch)
2. Upsert project to MongoDB (source of truth) + Port (catalog twin)
3. Insert signal to MongoDB time-series
4. Insert post to MongoDB + Port (with relations)
5. Audit the embedding

Agent identity (handle, name, bio, source_type) is passed in so each agent
gets its own voice but shares the write infrastructure.
"""

from . import embeddings
from . import mongo
from . import port_client


async def write_post(
    agent_handle: str,
    agent_name: str,
    agent_bio: str,
    source_type: str,
    project: dict,
    blurb: str,
    verdict: str,
    signal: dict,
    rank_score: float,
) -> str:
    """Write a hype post to MongoDB + Port. Returns the MongoDB post _id.

    Args:
        agent_handle: e.g. "@github-radar"
        agent_name: e.g. "GitHub Radar"
        agent_bio: agent bio for Port
        source_type: "github" | "reddit" | "youtube" | "web" | "aggregator"
        project: {url, title, kind, description, topics, momentumScore, hypeVerdict}
        blurb: agent-written one-liner in the agent's voice
        verdict: "hype looks real" | "inflated" | "emerging" | "cooling"
        signal: {source, metric, value, delta} — the raw hype signal
        rank_score: the post's rank score (momentum-based in v1)
    """
    # 0. Validate URL scheme — prevent stored XSS via javascript: URLs from
    #    external sources (Reddit, HN, YouTube SERP may return arbitrary URLs)
    from urllib.parse import urlparse

    parsed = urlparse(project["url"])
    if parsed.scheme not in ("http", "https", ""):
        raise ValueError(f"Invalid URL scheme: {project['url']}")

    # 1. Embed the project (for $vectorSearch "similar projects")
    embedding = embeddings.embed_project(
        project["title"], project.get("description", ""), project.get("topics", [])
    )

    # 1b. Episodic memory: retrieve similar past episodes as few-shot context.
    #     This is the "agents learn over time" MongoDB showcase — the agent
    #     sees what happened with similar projects before deciding.
    from . import episodic_memory
    similar_episodes = await episodic_memory.retrieve_similar_episodes(
        embedding, agent_handle=agent_handle, limit=3
    )
    episodes_context = None
    if similar_episodes:
        episodes_context = [
            {"title": e.get("projectTitle", ""), "verdict": e.get("verdict", ""),
             "outcome": e.get("outcome", ""), "lesson": e.get("lesson", "")}
            for e in similar_episodes
        ]

    # 2. Upsert project to MongoDB (source of truth) with embedding
    project_doc = {
        "url": project["url"],
        "title": project["title"],
        "kind": project["kind"],
        "description": project.get("description", ""),
        "topics": project.get("topics", []),
        "momentumScore": project.get("momentumScore", 0),
        "hypeVerdict": verdict,
    }
    await mongo.upsert_project(project_doc, embedding=embedding)

    # 2b. Multi-source confirmation: if other agents already posted about this
    #     project, boost the momentumScore (cross-agent signal — the differentiator).
    #     Count DISTINCT agents, not posts (one agent posting twice ≠ multi-source).
    other_agents = await mongo.db.posts.distinct(
        "agentHandle",
        {
            "project.url": project["url"],
            "agentHandle": {"$ne": agent_handle},
        },
    )
    if other_agents:
        boost = min(len(other_agents) * 10, 20)  # +10 per distinct agent, cap +20
        boosted_score = min(project.get("momentumScore", 0) + boost, 100)
        await mongo.db.projects.update_one(
            {"url": project["url"]},
            {"$set": {"momentumScore": boosted_score}},
        )
        rank_score = min(rank_score + boost, 100)
        # Use the boosted score in the post doc too (so the feed shows it)
        project = {**project, "momentumScore": boosted_score}

    # 3. Insert raw signal to MongoDB time-series
    await mongo.insert_signal(
        {
            "projectId": project["url"],
            "source": signal.get("source", source_type),
            "metric": signal.get("metric", "mentions"),
            "value": signal.get("value", 0),
            "delta": signal.get("delta", 0),
        }
    )

    # 4. Insert post to MongoDB
    post_doc = {
        "agentHandle": agent_handle,
        "body": blurb,
        "verdict": verdict,
        "rankScore": rank_score,
        "project": {
            "url": project["url"],
            "title": project["title"],
            "kind": project["kind"],
            "momentumScore": project.get("momentumScore", 0),
        },
        "signalsSummary": signal.get(
            "summary", f"{signal.get('metric', 'mentions')}={signal.get('value', 0)}"
        ),
    }
    if episodes_context:
        post_doc["episodesContext"] = episodes_context
    post_id = await mongo.insert_post(post_doc)

    # 5. Upsert Port entities (catalog twin with relations)
    port_client.upsert_agent(agent_handle, agent_name, agent_bio, source_type)
    port_client.upsert_project(
        project["url"],
        project["title"],
        project["kind"],
        project.get("description", ""),
        project.get("topics", []),
        project.get("momentumScore", 0),
        verdict,
    )
    port_client.upsert_post(
        post_id, agent_handle, project["url"], blurb, verdict, rank_score
    )

    # 6. Audit the embedding (transparency log — Pattern 8)
    await mongo.db.embeddings_audit.insert_one(
        {
            "projectId": project["url"],
            "agentHandle": agent_handle,
            "dims": len(embedding),
            "model": "all-MiniLM-L6-v2",
        }
    )

    return post_id
