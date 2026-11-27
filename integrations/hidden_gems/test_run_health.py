"""An HN outage fails the @hidden-gems run; zero HN stories is a noted quiet day.

Seam: ``runner.run_agent`` with the real ``build_agent`` Deep Agents graph. The
model is faked to call ``fetch_hidden_gem_candidates`` once, and Algolia is an
``httpx.MockTransport``, so the tool's own error handling is what is tested.
"""

import sys
from pathlib import Path

import httpx
import pytest
from langchain_core.language_models.fake_chat_models import GenericFakeChatModel
from langchain_core.messages import AIMessage

sys.path.insert(0, str(Path(__file__).parent))

from _shared import port_client, runner  # noqa: E402
from hidden_gems import agent  # noqa: E402


class _ToolCallingModel(GenericFakeChatModel):
    def bind_tools(self, tools, **kwargs):
        return self


def _fake_model_and_algolia(monkeypatch, algolia):
    replies = [
        AIMessage(
            content="",
            tool_calls=[
                {"name": "fetch_hidden_gem_candidates", "args": {}, "id": "c1"}
            ],
        ),
        AIMessage(content="Nothing to publish today."),
    ]
    monkeypatch.setenv("GROVE_MODEL", "fake")
    monkeypatch.setenv("GROVE_BASE_URL", "http://grove.invalid")
    monkeypatch.setattr(
        agent, "ChatOpenAI", lambda **_kw: _ToolCallingModel(messages=iter(replies))
    )
    real_client = httpx.AsyncClient
    monkeypatch.setattr(
        httpx,
        "AsyncClient",
        lambda **kw: real_client(transport=httpx.MockTransport(algolia), **kw),
    )

    async def no_breakouts(_db):
        return []

    async def no_preflight(_handle):
        return []

    monkeypatch.setattr(agent, "fetch_breakout_candidates", no_breakouts)
    monkeypatch.setattr(runner.doctor, "preflight", no_preflight)
    for name in ("upsert_agent", "record_agent_success"):
        monkeypatch.setattr(port_client, name, lambda *_args: {"ok": True})


def _capture_run_records(monkeypatch) -> list[dict]:
    records = []

    async def capture(record):
        records.append(record)

    monkeypatch.setattr(runner, "_record_run_health", capture)
    return records


def _run():
    return runner.run_agent(
        agent.AGENT_HANDLE,
        agent.AGENT_NAME,
        agent.AGENT_BIO,
        agent.SOURCE_TYPE,
        agent.build_agent,
    )


async def test_an_algolia_http_error_fails_the_run(monkeypatch, db):
    _fake_model_and_algolia(monkeypatch, lambda _req: httpx.Response(503))
    records = _capture_run_records(monkeypatch)

    with pytest.raises(httpx.HTTPStatusError, match="503"):
        await _run()

    assert records[0]["ok"] is False
    assert "503" in records[0]["error"]


async def test_zero_show_hn_stories_is_an_ok_run_with_a_note(monkeypatch, db):
    _fake_model_and_algolia(
        monkeypatch, lambda _req: httpx.Response(200, json={"hits": []})
    )
    records = _capture_run_records(monkeypatch)

    summary = await _run()

    assert summary["ok"] is True
    assert records[0]["ok"] is True
    assert "0 Show HN stories" in records[0]["note"]
