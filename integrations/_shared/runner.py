"""Shared runner for all HypeRadar agent-creators.

Each agent's main.py calls run_agent() with its build_agent function.
Handles: Port agent upsert, Deep Agents invocation, MongoDBSaver checkpointing,
and post-count reporting.
"""

import os
import sys
from datetime import datetime, timezone

from dotenv import load_dotenv

load_dotenv()  # load repo-root .env

# Add parent dir to path so we can import _shared and the agent package
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from langgraph.checkpoint.mongodb import MongoDBSaver  # noqa: E402

from _shared import mongo  # noqa: E402
from _shared import port_client  # noqa: E402


async def run_agent(agent_handle, agent_name, agent_bio, source_type, build_agent_fn):
    """Run one agent cycle. Returns a summary dict."""
    # 1. Ensure the agent exists in the Port catalog
    port_client.require_success(
        port_client.upsert_agent(agent_handle, agent_name, agent_bio, source_type),
        f"agent sync for {agent_handle}",
    )

    # 2. MongoDBSaver checkpoint (durable, resumable)
    thread_id = f"{agent_handle}:{datetime.now(timezone.utc).isoformat()}"
    config = {"configurable": {"thread_id": thread_id}}
    start_of_day = datetime.now(timezone.utc).replace(
        hour=0, minute=0, second=0, microsecond=0
    )

    with MongoDBSaver.from_conn_string(os.environ["MONGODB_URI"]) as checkpointer:
        agent = build_agent_fn(checkpointer=checkpointer)
        await agent.ainvoke(
            {"messages": f"Run today's {agent_handle} scan."}, config=config
        )

    # 3. Count posts created today by this agent
    posts_today = await mongo.db.posts.count_documents(
        {"agentHandle": agent_handle, "postedAt": {"$gte": start_of_day}}
    )
    ok = posts_today > 0
    if not ok:
        print(
            f"WARNING: {agent_handle} produced 0 posts — possible source failure",
            file=sys.stderr,
        )
    return {"thread_id": thread_id, "posts_today": posts_today, "ok": ok}
