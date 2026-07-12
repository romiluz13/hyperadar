"""@github-radar runner — executes one agent run end-to-end.

Usage (from the integration folder):
    uv run python main.py

The agent fetches trending GitHub AI repos, scores them (Deep Agents + Grove),
and for each breakout writes signals + project + post to MongoDB and upserts the
agent/project/post entities in Port. MongoDBSaver checkpoints the run.

T2 tracer bullet: proves the whole spine
(Port catalog + Deep Agents + Grove + MongoDB memory + feed data) with one agent.
"""

import asyncio
import os
import sys
from datetime import datetime, timezone

from dotenv import load_dotenv

load_dotenv()  # load repo-root .env

import mongo  # noqa: E402
import port_client  # noqa: E402
from agent import (  # noqa: E402
    AGENT_BIO,
    AGENT_HANDLE,
    AGENT_NAME,
    SOURCE_TYPE,
    build_agent,
)
from langgraph.checkpoint.mongodb import MongoDBSaver  # noqa: E402


async def run_once() -> dict:
    """Run one @github-radar cycle. Returns a summary dict."""
    # 1. Ensure the agent exists in the Port catalog before it posts.
    port_client.upsert_agent(AGENT_HANDLE, AGENT_NAME, AGENT_BIO, SOURCE_TYPE)

    # 2. MongoDBSaver checkpoints the run (durable, resumable) — MongoDB as agent memory.
    #    from_conn_string is a context manager; .setup() creates checkpoint collections.
    thread_id = f"github-radar:{datetime.now(timezone.utc).isoformat()}"
    config = {"configurable": {"thread_id": thread_id}}
    start_of_day = datetime.now(timezone.utc).replace(
        hour=0, minute=0, second=0, microsecond=0
    )

    with MongoDBSaver.from_conn_string(os.environ["MONGODB_URI"]) as checkpointer:
        agent = build_agent(checkpointer=checkpointer)
        await agent.ainvoke(
            {"messages": "Run today's GitHub radar scan."}, config=config
        )

    # 3. Count posts created today by this agent.
    posts_today = await mongo.db.posts.count_documents(
        {"agentHandle": AGENT_HANDLE, "postedAt": {"$gte": start_of_day}}
    )
    ok = posts_today > 0
    if not ok:
        print(f"WARNING: {AGENT_HANDLE} produced 0 posts — possible source failure", file=sys.stderr)
    return {"thread_id": thread_id, "posts_today": posts_today, "ok": ok}


def main():
    summary = asyncio.run(run_once())
    print(f"@github-radar run complete: {summary}")
    if not summary["ok"]:
        sys.exit(1)


if __name__ == "__main__":
    main()
