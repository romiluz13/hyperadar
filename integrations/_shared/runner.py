"""Shared runner for all HypeRadar agent-creators.

Each agent's main.py calls run_agent() with its build_agent function.
Handles: Port agent upsert, Deep Agents invocation, MongoDBSaver checkpointing,
and post-count reporting.
"""

import asyncio
import os
import sys
from datetime import datetime, timezone

from dotenv import load_dotenv

load_dotenv()  # load repo-root .env

# Add parent dir to path so we can import _shared and the agent package
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from langgraph.checkpoint.mongodb import MongoDBSaver  # noqa: E402

from _shared import mongo  # noqa: E402
from _shared import port_client, write_post  # noqa: E402

AGENT_INVOCATION_TIMEOUT_SECONDS = 20 * 60


async def summarize_run(agent_handle: str, thread_id: str, start_of_day: datetime):
    posts_today = await mongo.db.posts.count_documents(
        {"agentHandle": agent_handle, "postedAt": {"$gte": start_of_day}}
    )
    posts_written = await mongo.db.posts.count_documents(
        {"agentHandle": agent_handle, "runId": thread_id}
    )
    synced_this_run = await mongo.db.posts.count_documents(
        {"agentHandle": agent_handle, "portSyncedByRunId": thread_id}
    )
    pending_port_syncs = await mongo.db.posts.count_documents(
        {"agentHandle": agent_handle, "portSyncStatus": "pending"}
    )
    return {
        "thread_id": thread_id,
        "posts_today": posts_today,
        "posts_written": posts_written,
        "synced_this_run": synced_this_run,
        "pending_port_syncs": pending_port_syncs,
        "ok": synced_this_run > 0 and pending_port_syncs == 0,
    }


async def run_agent(agent_handle, agent_name, agent_bio, source_type, build_agent_fn):
    """Run one agent cycle. Returns a summary dict."""
    try:
        return await _run_agent_cycle(
            agent_handle, agent_name, agent_bio, source_type, build_agent_fn
        )
    finally:
        await mongo.close_client()


async def _run_agent_cycle(
    agent_handle, agent_name, agent_bio, source_type, build_agent_fn
):
    """Run one cycle while its MongoDB client remains owned by this loop."""
    # 1. Ensure the agent exists in the Port catalog
    port_client.require_success(
        port_client.upsert_agent(agent_handle, agent_name, agent_bio, source_type),
        f"agent sync for {agent_handle}",
    )

    # 2. MongoDBSaver checkpoint (durable and inspectable for this run)
    thread_id = f"{agent_handle}:{datetime.now(timezone.utc).isoformat()}"
    config = {"configurable": {"thread_id": thread_id}}
    start_of_day = datetime.now(timezone.utc).replace(
        hour=0, minute=0, second=0, microsecond=0
    )

    token = write_post.current_run_id.set(thread_id)
    try:
        await write_post.repair_pending_posts(
            agent_handle, agent_name, agent_bio, source_type
        )
        with MongoDBSaver.from_conn_string(os.environ["MONGODB_URI"]) as checkpointer:
            agent = build_agent_fn(checkpointer=checkpointer)
            try:
                await asyncio.wait_for(
                    agent.ainvoke(
                        {"messages": f"Run today's {agent_handle} scan."}, config=config
                    ),
                    timeout=AGENT_INVOCATION_TIMEOUT_SECONDS,
                )
            except TimeoutError:
                raise RuntimeError(
                    f"{agent_handle} invocation timed out after "
                    f"{AGENT_INVOCATION_TIMEOUT_SECONDS} seconds"
                ) from None
    finally:
        write_post.current_run_id.reset(token)

    # 3. Count posts created today by this agent
    summary = await summarize_run(agent_handle, thread_id, start_of_day)
    if summary["ok"]:
        port_client.require_success(
            port_client.record_agent_success(agent_handle),
            f"successful run record for {agent_handle}",
        )
    else:
        print(
            f"WARNING: {agent_handle} produced {summary['posts_written']} posts with "
            f"{summary['synced_this_run']} synced this run and "
            f"{summary['pending_port_syncs']} pending Port syncs",
            file=sys.stderr,
        )
    return summary
