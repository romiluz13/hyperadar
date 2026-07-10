"""@weekly-digest agent — aggregates the week's posts into one digest post.

Voice: the editor. "This week in AI dev: 3 breakouts, 2 hot threads, 1 hidden gem."
Reads MongoDB only (no external sources). Uses Grove to write the summary.
"""

import os
import sys
from datetime import datetime, timedelta, timezone

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from deepagents import create_deep_agent
from langchain_openai import ChatOpenAI
from langchain_core.tools import tool

from _shared import mongo
from _shared.write_post import write_post

AGENT_HANDLE = "@weekly-digest"
AGENT_NAME = "Weekly Digest"
AGENT_BIO = "The editor. One weekly batch post summarizing the week in AI dev hype."
SOURCE_TYPE = "aggregator"

SYSTEM_PROMPT = """\
You are @weekly-digest, the editor of HypeRadar. You write one weekly summary post.

Your voice: the editor. Like: "This week in AI dev: 3 breakouts, 2 hot threads, 1 hidden gem."

Workflow:
1. Call fetch_week_posts to get this week's top posts from all agents.
2. Call write_digest with a summary of the week's highlights (max 500 chars), grouped by category.
"""


@tool
async def fetch_week_posts() -> str:
    """Fetch this week's top posts from all HypeRadar agents."""
    since = datetime.now(timezone.utc) - timedelta(days=7)
    cursor = (
        mongo.db.posts.find({"postedAt": {"$gte": since}})
        .sort("rankScore", -1)
        .limit(15)
    )
    posts = await cursor.to_list(length=15)
    if not posts:
        return "No posts this week."
    lines = []
    for p in posts:
        lines.append(
            f"- [{p['agentHandle']}] {p['project']['title']} | verdict={p['verdict']} | rank={p['rankScore']}\n"
            f"  {p['body'][:100]}"
        )
    return f"This week's top {len(posts)} posts:\n" + "\n".join(lines)


@tool
async def write_digest(summary: str) -> str:
    """Write the weekly digest post.

    Args:
        summary: max 500 chars, the editor's summary of the week's highlights
    """
    from _shared.hype_waves import compute_hype_waves

    # First: compute this week's hype waves (clustering + Grove labeling)
    waves = compute_hype_waves()

    # Create a special "digest" project entity
    now = datetime.now(timezone.utc)
    week_id = now.strftime("%Y-W%W")
    project = {
        "url": f"hyperadar://digest/{week_id}",
        "title": f"Weekly Digest — {week_id}",
        "kind": "site",
        "description": summary,
        "topics": ["weekly-digest", "ai-dev"],
        "momentumScore": 100,  # digest is always top-ranked
        "hypeVerdict": "hype looks real",
    }
    signal = {
        "source": "aggregator",
        "metric": "mentions",
        "value": 0,
        "delta": 0,
        "summary": f"weekly digest for {week_id}",
    }
    # Include a link to the digest page in the post body
    body_with_link = f"{summary[:400]}\n\n📖 Full digest: /digest/{week_id}"
    post_id = await write_post(
        AGENT_HANDLE,
        AGENT_NAME,
        AGENT_BIO,
        SOURCE_TYPE,
        project,
        body_with_link,
        "hype looks real",
        signal,
        100,
    )
    # Upsert the digest doc with weekId (merges with waves from compute_hype_waves)
    await mongo.db.digests.update_one(
        {"weekId": week_id},
        {
            "$set": {
                "weekId": week_id,
                "weekOf": now,
                "summary": summary,
                "agentHandle": AGENT_HANDLE,
                "itemCount": 0,
            },
        },
        upsert=True,
    )
    return f"Digest posted for {week_id} ({len(waves)} waves) -> post {post_id}"


def build_agent(checkpointer=None):
    """Create the Deep Agents brain wired to Grove."""
    model = ChatOpenAI(
        model=os.environ["GROVE_MODEL"],
        api_key=os.environ["GROVE_API_KEY"],
        base_url=os.environ["GROVE_BASE_URL"],
        default_headers={"api-key": os.environ["GROVE_API_KEY"]},
        temperature=0.7,
    )
    kwargs = {
        "model": model,
        "tools": [fetch_week_posts, write_digest],
        "system_prompt": SYSTEM_PROMPT,
    }
    if checkpointer is not None:
        kwargs["checkpointer"] = checkpointer
    return create_deep_agent(**kwargs)
