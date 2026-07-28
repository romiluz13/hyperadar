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
    """RED: LangGraph ainvoke returns a dict; getattr(result, 'messages') is None."""
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
