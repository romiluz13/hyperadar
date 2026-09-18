"""Tests for the runner's tool-trace extraction.

The runner logs the LLM tool-call trace after agent.ainvoke so a "wrote 0"
event is self-diagnosing. LangGraph's ainvoke returns a STATE DICT (not an
object), so the trace must read messages with dict access
(result.get("messages", [])), not getattr (which returns None for a dict and
made the logging show fetch_calls=0 even when the LLM called tools — the
YouTube misdiagnosis root cause).
"""

import os
import sys
from types import SimpleNamespace

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from _shared import runner  # noqa: E402


def _message(tool_calls=None):
    """A minimal stand-in for a LangChain message: has a .tool_calls list."""
    return SimpleNamespace(tool_calls=tool_calls or [])


def test_extract_tool_trace_reads_messages_from_dict_result():
    """LangGraph ainvoke returns a state dict; the trace must read it via dict access."""
    # result is a STATE DICT — the shape agent.ainvoke actually returns.
    result = {
        "messages": [
            _message(),  # HumanMessage-like, no tool calls
            _message([{"name": "fetch_youtube_videos", "args": {}, "id": "call-1"}]),
            _message([{"name": "write_youtube_post", "args": {}, "id": "call-2"}]),
        ],
    }
    trace = runner._extract_tool_trace(result)
    assert trace == ["fetch_youtube_videos", "write_youtube_post"]


def test_extract_tool_trace_counts_fetch_and_write_calls():
    """The counts (fetch_calls/write_calls) derive from the trace."""
    result = {
        "messages": [
            _message([{"name": "fetch_youtube_videos", "args": {}, "id": "1"}]),
            _message([{"name": "write_youtube_post", "args": {}, "id": "2"}]),
            _message([{"name": "write_youtube_post", "args": {}, "id": "3"}]),
        ],
    }
    trace = runner._extract_tool_trace(result)
    fetch = sum(1 for n in trace if n.startswith("fetch_"))
    write = sum(1 for n in trace if n.startswith("write_"))
    assert fetch == 1
    assert write == 2


def test_extract_tool_trace_empty_when_no_messages():
    result = {"messages": []}
    assert runner._extract_tool_trace(result) == []


def test_extract_tool_trace_handles_missing_messages_key():
    """A result without a 'messages' key yields an empty trace, not a crash."""
    result = {"other": "data"}
    assert runner._extract_tool_trace(result) == []


# ─── Run health (_run_ok): quiet days are healthy, silent runs are not ───


def test_run_ok_synced_posts_pass():
    """The normal day: posts written and synced, nothing pending."""
    assert runner._run_ok(
        posts_written=5, synced_this_run=5, pending_port_syncs=0, agent_was_active=True
    )


def test_run_ok_healthy_quiet_day_passes():
    """0 posts with the agent actively fetching/writing = gates did their job.

    The pool-scaled threshold and republish cooldown can legitimately reject
    every candidate. That must not fail the run (hidden-gems 2026-09-18).
    """
    assert runner._run_ok(
        posts_written=0, synced_this_run=0, pending_port_syncs=0, agent_was_active=True
    )


def test_run_ok_silent_agent_fails():
    """0 posts and the agent never called a fetch_*/write_* tool = broken run.

    community-radar's source 401: tool_trace showed no fetch or write call.
    """
    assert not runner._run_ok(
        posts_written=0, synced_this_run=0, pending_port_syncs=0, agent_was_active=False
    )


def test_run_ok_pending_syncs_fail():
    """Posts stuck pending Port sync fail even when the agent was active."""
    assert not runner._run_ok(
        posts_written=2, synced_this_run=0, pending_port_syncs=2, agent_was_active=True
    )


def test_run_ok_written_but_never_synced_fails():
    """Posts written this run, none synced, none pending = suspicious state."""
    assert not runner._run_ok(
        posts_written=3, synced_this_run=0, pending_port_syncs=0, agent_was_active=True
    )
