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

import pytest

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


# ─── Run-health records: every run leaves evidence in agent_runs ───


class _RunHealthDb:
    """Fake db capturing agent_runs upserts; optionally fails on write."""

    def __init__(self, fail=False):
        self.records = []
        self.fail = fail

    def _get_db(self):
        return self

    @property
    def agent_runs(self):
        return self

    async def update_one(self, query, update, **_kwargs):
        if self.fail:
            raise RuntimeError("agent_runs unavailable")
        self.records.append((query, update))


@pytest.mark.asyncio
async def test_crashed_run_leaves_a_failed_agent_runs_record(monkeypatch):
    """A run that crashes mid-cycle must still record ok=False + the error."""
    fake = _RunHealthDb()

    async def fake_close():
        pass

    async def crashing_cycle(*_args, **_kwargs):
        raise RuntimeError("source exploded")

    monkeypatch.setattr(runner.mongo, "_get_db", lambda: fake._get_db())
    monkeypatch.setattr(runner.mongo, "close_client", fake_close)
    monkeypatch.setattr(runner, "_run_agent_cycle", crashing_cycle)

    with pytest.raises(RuntimeError, match="source exploded"):
        await runner.run_agent("@reddit-pulse", "Reddit Pulse", "bio", "reddit", None)

    assert len(fake.records) == 1
    query, update = fake.records[0]
    record = update["$set"]
    assert query["threadId"].startswith("@reddit-pulse:crash:")
    assert record["agentHandle"] == "@reddit-pulse"
    assert record["ok"] is False
    assert "source exploded" in record["error"]
    assert "finishedAt" in record


@pytest.mark.asyncio
async def test_unhealthy_run_records_its_summary_and_doctor_findings(monkeypatch):
    """The record carries the run summary plus the doctor's FAIL lines."""
    fake = _RunHealthDb()

    async def fake_close():
        pass

    async def silent_cycle(_handle, _name, _bio, _source, _build, _run_record):
        _run_record["threadId"] = "@community-radar:run:1"
        _run_record["doctor"] = [
            {"name": "rombot", "status": "fail", "detail": "HTTP 401 — token rejected"}
        ]
        _run_record["fetchCalls"] = 0
        _run_record["writeCalls"] = 0
        return {
            "thread_id": "@community-radar:run:1",
            "posts_today": 0,
            "posts_written": 0,
            "synced_this_run": 0,
            "pending_port_syncs": 0,
            "ok": False,
        }

    monkeypatch.setattr(runner.mongo, "_get_db", lambda: fake._get_db())
    monkeypatch.setattr(runner.mongo, "close_client", fake_close)
    monkeypatch.setattr(runner, "_run_agent_cycle", silent_cycle)

    summary = await runner.run_agent(
        "@community-radar", "AI Agents Community", "bio", "community", None
    )

    assert summary["ok"] is False
    assert len(fake.records) == 1
    record = fake.records[0][1]["$set"]
    assert record["threadId"] == "@community-radar:run:1"
    assert record["ok"] is False
    assert record["doctor"][0]["name"] == "rombot"
    assert record["fetchCalls"] == 0
    assert "error" not in record


@pytest.mark.asyncio
async def test_recording_failure_never_masks_the_runs_own_error(monkeypatch):
    """A broken agent_runs write is logged, not raised past the run's outcome."""
    fake = _RunHealthDb(fail=True)

    async def fake_close():
        pass

    async def crashing_cycle(*_args, **_kwargs):
        raise RuntimeError("source exploded")

    monkeypatch.setattr(runner.mongo, "_get_db", lambda: fake._get_db())
    monkeypatch.setattr(runner.mongo, "close_client", fake_close)
    monkeypatch.setattr(runner, "_run_agent_cycle", crashing_cycle)

    # The run's own error surfaces; the recording failure is only logged.
    with pytest.raises(RuntimeError, match="source exploded"):
        await runner.run_agent("@reddit-pulse", "Reddit Pulse", "bio", "reddit", None)
