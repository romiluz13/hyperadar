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
from _shared import doctor, port_client, write_post  # noqa: E402

AGENT_INVOCATION_TIMEOUT_SECONDS = 20 * 60


def _extract_tool_trace(result) -> list[str]:
    """Extract the ordered tool-call names from an agent.ainvoke result.

    LangGraph's ``ainvoke`` returns a STATE DICT (``{"messages": [...]}``), not
    an object — so the messages MUST be read with dict access
    (``result.get("messages", [])``), not ``getattr(result, "messages")``, which
    returns ``None`` for a dict and silently empties the trace. Each message's
    ``tool_calls`` may be a list of dicts (``{"name": ...}``) or of objects with
    a ``.name`` attribute; both shapes are handled.
    """
    tool_calls: list[str] = []
    if isinstance(result, dict):
        messages = result.get("messages", []) or []
    else:
        messages = getattr(result, "messages", None) or []
    for m in messages:
        tcs = (
            m.get("tool_calls", [])
            if isinstance(m, dict)
            else (getattr(m, "tool_calls", None) or [])
        )
        for tc in tcs:
            name = (
                tc.get("name") if isinstance(tc, dict) else getattr(tc, "name", str(tc))
            )
            tool_calls.append(name)
    return tool_calls


async def summarize_run(
    agent_handle: str,
    thread_id: str,
    start_of_day: datetime,
    *,
    agent_was_active: bool,
):
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
        "ok": _run_ok(
            posts_written, synced_this_run, pending_port_syncs, agent_was_active
        ),
    }


def _run_ok(posts_written, synced_this_run, pending_port_syncs, agent_was_active):
    """A run is healthy when Port sync is clean AND the agent actually worked.

    A quiet day — every candidate gated by the pool-scaled threshold or the
    republish cooldown, 0 posts — is the system working as designed, not a
    failure. A run where the agent never called a fetch_*/write_* tool (broken
    source or broken agent) or left Port syncs pending is a failure.
    """
    if pending_port_syncs != 0:
        return False
    if synced_this_run > 0:
        return True
    return posts_written == 0 and agent_was_active


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
    # 0. Source doctor: live credential/source health into the run log.
    #    Diagnostics only — a FAIL here must not kill the run before the
    #    runner's own health semantics (agent_was_active/_run_ok) can speak.
    try:
        await doctor.preflight(agent_handle)
    except Exception as e:
        print(f"[doctor] preflight skipped: {e}", file=sys.stderr, flush=True)

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
                result = await asyncio.wait_for(
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

    # Log the LLM tool-call trace so a "wrote 0" event is self-diagnosing:
    # fetch_calls>0 + write_calls=0 = gate/cooldown (healthy quiet day);
    # fetch_calls=0 + write_calls=0 = a broken source or agent worth chasing.
    tool_calls = _extract_tool_trace(result)
    write_call_count = sum(1 for n in tool_calls if n.startswith("write_"))
    fetch_call_count = sum(1 for n in tool_calls if n.startswith("fetch_"))
    agent_was_active = (fetch_call_count + write_call_count) > 0
    print(
        f"{agent_handle} runner: fetch_calls={fetch_call_count} "
        f"write_calls={write_call_count} tool_trace={tool_calls}",
        flush=True,
    )

    # 3. Count posts created today by this agent
    summary = await summarize_run(
        agent_handle, thread_id, start_of_day, agent_was_active=agent_was_active
    )
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
