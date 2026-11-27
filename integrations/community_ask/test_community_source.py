"""@community-radar fails loudly when RomBot is down and stays ok on a quiet day.

Seams: ``fetch_community_candidates`` against an ``httpx.MockTransport``, and
``runner.run_agent`` with the real ``build_agent`` Deep Agents graph whose
faked model calls the agent's own tool once.
"""

import sys
from pathlib import Path

import httpx
import pytest
from langchain_core.language_models.fake_chat_models import GenericFakeChatModel
from langchain_core.messages import AIMessage

sys.path.insert(0, str(Path(__file__).parent))

import agent  # noqa: E402
import source  # noqa: E402
from _shared import port_client, runner  # noqa: E402


def _rombot(monkeypatch, handler):
    monkeypatch.setenv("ROMBOT_COMMUNITY_ASK_TOKEN", "test-token")
    real_client = httpx.AsyncClient
    monkeypatch.setattr(
        httpx,
        "AsyncClient",
        lambda **kw: real_client(transport=httpx.MockTransport(handler), **kw),
    )


def _answer(text):
    return lambda _req: httpx.Response(200, json={"answer": text})


def _timeout(request):
    raise httpx.ReadTimeout("community-ask took too long", request=request)


@pytest.mark.parametrize(
    "handler",
    [lambda _req: httpx.Response(502), _timeout],
    ids=["http-error", "timeout"],
)
async def test_a_rombot_outage_raises_instead_of_a_quiet_day(monkeypatch, handler):
    _rombot(monkeypatch, handler)

    with pytest.raises(httpx.HTTPError):
        await source.fetch_community_candidates()


@pytest.mark.parametrize(
    "answer",
    ["", "   ", "The community was quiet."],
    ids=["empty", "blank", "no-topics"],
)
async def test_an_answer_without_topics_is_a_quiet_day(monkeypatch, answer):
    _rombot(monkeypatch, _answer(answer))

    assert await source.fetch_community_candidates() == []


class _ToolCallingModel(GenericFakeChatModel):
    def bind_tools(self, tools, **kwargs):
        return self


async def test_a_quiet_day_run_reports_ok(monkeypatch, db):
    """The runner must see the agent's own tool call, or 0 posts reads as broken."""
    tool_names = []
    real_create = agent.create_deep_agent

    def capture_tools(**kwargs):
        tool_names.extend(t.name for t in kwargs["tools"])
        return real_create(**kwargs)

    def replies():
        yield AIMessage(
            content="", tool_calls=[{"name": tool_names[0], "args": {}, "id": "c1"}]
        )
        yield AIMessage(content="No community discussions to publish today.")

    async def no_preflight(_handle):
        return []

    monkeypatch.setenv("GROVE_MODEL", "fake")
    monkeypatch.setenv("GROVE_BASE_URL", "http://grove.invalid")
    monkeypatch.setattr(agent, "create_deep_agent", capture_tools)
    monkeypatch.setattr(
        agent, "ChatOpenAI", lambda **_kw: _ToolCallingModel(messages=replies())
    )
    monkeypatch.setattr(runner.doctor, "preflight", no_preflight)
    for name in ("upsert_agent", "record_agent_success"):
        monkeypatch.setattr(port_client, name, lambda *_args: {"ok": True})
    _rombot(monkeypatch, _answer(""))

    summary = await runner.run_agent(
        agent.AGENT_HANDLE,
        agent.AGENT_NAME,
        agent.AGENT_BIO,
        agent.SOURCE_TYPE,
        agent.build_agent,
    )

    assert summary["posts_written"] == 0
    assert summary["ok"] is True
