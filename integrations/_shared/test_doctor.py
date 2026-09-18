"""Tests for the source doctor (credential checks + preflight)."""

import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from _shared import doctor  # noqa: E402


class _Response:
    def __init__(self, status_code=200):
        self.status_code = status_code


class _FakeClient:
    def __init__(self, method_responses):
        # method_responses: {"get": [...], "post": [...]} — popped in order.
        self._queues = {k: list(v) for k, v in method_responses.items()}
        self.calls = []

    async def get(self, url, **kwargs):
        self.calls.append(("get", url, kwargs))
        return self._queues["get"].pop(0)

    async def post(self, url, **kwargs):
        self.calls.append(("post", url, kwargs))
        return self._queues["post"].pop(0)


# ─── required_checks ───


def test_required_checks_per_agent():
    assert doctor.required_checks("@github-radar") == (
        "grove",
        "mongodb",
        "github_token",
        "hn_algolia",  # the corroboration gate + engagement boost need HN
        "reddit_corpus",  # the gate's Reddit leg matches against this corpus
    )
    # hidden-gems depends on HN for discovery and engagement.
    assert "hn_algolia" in doctor.required_checks("@hidden-gems")
    assert "arxiv" not in doctor.required_checks("@hidden-gems")
    assert "youtube_key" in doctor.required_checks("@youtube-trends")
    assert "rombot" in doctor.required_checks("@community-radar")
    assert "brightdata" in doctor.required_checks("@reddit-pulse")


def test_required_checks_unknown_handle_gets_core_set():
    assert doctor.required_checks("@mystery-agent") == ("grove", "mongodb")


# ─── preflight ───


@pytest.mark.asyncio
async def test_preflight_prints_lines_and_never_raises(capsys, monkeypatch):
    async def fake_grove():
        return {"name": "grove", "status": "ok", "detail": "gateway accepted"}

    async def fake_mongodb():
        return {"name": "mongodb", "status": "fail", "detail": "ping failed: x"}

    async def fake_github_token():
        raise RuntimeError("kaboom")  # a crashing check must not kill the run

    async def fake_hn_algolia():
        return {"name": "hn_algolia", "status": "ok", "detail": "search API reachable"}

    async def fake_reddit_corpus():
        return {"name": "reddit_corpus", "status": "ok", "detail": "42 snapshots"}

    monkeypatch.setitem(doctor.CHECKS, "grove", fake_grove)
    monkeypatch.setitem(doctor.CHECKS, "mongodb", fake_mongodb)
    monkeypatch.setitem(doctor.CHECKS, "github_token", fake_github_token)
    monkeypatch.setitem(doctor.CHECKS, "hn_algolia", fake_hn_algolia)
    monkeypatch.setitem(doctor.CHECKS, "reddit_corpus", fake_reddit_corpus)

    results = await doctor.preflight("@github-radar")

    assert [r["status"] for r in results] == ["ok", "fail", "fail", "ok", "ok"]
    out = capsys.readouterr().out
    assert "[doctor] grove: OK — gateway accepted" in out
    assert "[doctor] mongodb: FAIL — ping failed: x" in out
    assert "[doctor] github_token: FAIL — check crashed: kaboom" in out
    assert "[doctor] hn_algolia: OK — search API reachable" in out
    assert "[doctor] reddit_corpus: OK — 42 snapshots" in out


# ─── individual checks ───


@pytest.mark.asyncio
async def test_check_grove_ok(monkeypatch):
    monkeypatch.setenv("GROVE_API_KEY", "test-key")
    monkeypatch.setenv("GROVE_BASE_URL", "https://gateway.test")
    monkeypatch.setenv("GROVE_MODEL", "test-model")
    client = _FakeClient({"post": [_Response(status_code=200)]})
    result = await doctor.check_grove(client)
    assert result["status"] == "ok"
    method, url, kwargs = client.calls[0]
    assert url == "https://gateway.test/chat/completions"
    assert kwargs["headers"]["api-key"] == "test-key"
    assert kwargs["json"]["max_tokens"] == 1  # cheapest possible ping


@pytest.mark.asyncio
async def test_check_grove_rejected_key(monkeypatch):
    monkeypatch.setenv("GROVE_API_KEY", "dead-key")
    monkeypatch.setenv("GROVE_BASE_URL", "https://gateway.test")
    monkeypatch.setenv("GROVE_MODEL", "test-model")
    client = _FakeClient({"post": [_Response(status_code=401)]})
    result = await doctor.check_grove(client)
    assert result["status"] == "fail"
    assert "401" in result["detail"]


@pytest.mark.asyncio
async def test_check_grove_missing_env_fails_without_network(monkeypatch):
    monkeypatch.delenv("GROVE_API_KEY", raising=False)
    monkeypatch.delenv("GROVE_BASE_URL", raising=False)
    client = _FakeClient({"post": []})
    result = await doctor.check_grove(client)
    assert result["status"] == "fail"
    assert "not set" in result["detail"]
    assert client.calls == []


@pytest.mark.asyncio
async def test_check_github_token_anonymous_ok(monkeypatch):
    monkeypatch.delenv("GITHUB_TOKEN", raising=False)
    client = _FakeClient({"get": [_Response(status_code=200)]})
    result = await doctor.check_github_token(client)
    assert result["status"] == "ok"
    assert "anonymous" in result["detail"]
    # No Authorization header is sent when no token is configured.
    _, _, kwargs = client.calls[0]
    assert "Authorization" not in kwargs["headers"]


@pytest.mark.asyncio
async def test_check_github_token_accepted(monkeypatch):
    monkeypatch.setenv("GITHUB_TOKEN", "test-token")
    client = _FakeClient({"get": [_Response(status_code=200)]})
    result = await doctor.check_github_token(client)
    assert result["status"] == "ok"
    assert result["detail"] == "token accepted"
    _, _, kwargs = client.calls[0]
    assert kwargs["headers"]["Authorization"] == "token test-token"


@pytest.mark.asyncio
async def test_check_github_token_rejected():
    client = _FakeClient({"get": [_Response(status_code=401)]})
    result = await doctor.check_github_token(client)
    assert result["status"] == "fail"


@pytest.mark.asyncio
async def test_check_rombot_token_rejected(monkeypatch):
    """THE regression: a dead rombot token must surface, not die silently."""
    monkeypatch.setenv("ROMBOT_COMMUNITY_ASK_TOKEN", "dead-token")
    client = _FakeClient({"post": [_Response(status_code=401)]})
    result = await doctor.check_rombot(client)
    assert result["status"] == "fail"
    assert "token rejected" in result["detail"]


@pytest.mark.asyncio
async def test_check_rombot_missing_token(monkeypatch):
    monkeypatch.delenv("ROMBOT_COMMUNITY_ASK_TOKEN", raising=False)
    result = await doctor.check_rombot(client=None)
    assert result["status"] == "fail"
    assert "not set" in result["detail"]


@pytest.mark.asyncio
async def test_check_youtube_key_rejected(monkeypatch):
    monkeypatch.setenv("YOUTUBE_API_KEY", "bad-key")
    client = _FakeClient({"get": [_Response(status_code=403)]})
    result = await doctor.check_youtube_key(client)
    assert result["status"] == "fail"
    assert "key rejected" in result["detail"]


@pytest.mark.asyncio
async def test_check_hn_algolia_ok():
    client = _FakeClient({"get": [_Response(status_code=200)]})
    result = await doctor.check_hn_algolia(client)
    assert result["status"] == "ok"


@pytest.mark.asyncio
async def test_check_brightdata_presence(monkeypatch):
    monkeypatch.setenv("BRIGHTDATA_API_KEY", "x")
    assert (await doctor.check_brightdata())["status"] == "ok"
    monkeypatch.delenv("BRIGHTDATA_API_KEY", raising=False)
    assert (await doctor.check_brightdata())["status"] == "fail"


@pytest.mark.asyncio
async def test_check_mongodb_ping(monkeypatch):
    class _FakeDb:
        async def command(self, cmd):
            assert cmd == "ping"
            return {"ok": 1}

    monkeypatch.setattr(doctor.mongo, "_get_db", lambda: _FakeDb())
    result = await doctor.check_mongodb()
    assert result["status"] == "ok"

    class _BrokenDb:
        async def command(self, cmd):
            raise RuntimeError("atlas unreachable")

    monkeypatch.setattr(doctor.mongo, "_get_db", lambda: _BrokenDb())
    result = await doctor.check_mongodb()
    assert result["status"] == "fail"
    assert "atlas unreachable" in result["detail"]


@pytest.mark.asyncio
async def test_check_reddit_corpus_fresh(monkeypatch):
    class _FakeSnapshots:
        async def count_documents(self, query):
            return 42

    class _FakeDb:
        reddit_post_snapshots = _FakeSnapshots()

    monkeypatch.setattr(doctor.mongo, "_get_db", lambda: _FakeDb())
    result = await doctor.check_reddit_corpus()
    assert result["status"] == "ok"
    assert "42" in result["detail"]


@pytest.mark.asyncio
async def test_check_reddit_corpus_stale_fails(monkeypatch):
    """A stale corpus means the gate's Reddit leg is blind — must surface."""

    class _FakeSnapshots:
        async def count_documents(self, query):
            return 0

    class _FakeDb:
        reddit_post_snapshots = _FakeSnapshots()

    monkeypatch.setattr(doctor.mongo, "_get_db", lambda: _FakeDb())
    result = await doctor.check_reddit_corpus()
    assert result["status"] == "fail"
    assert "blind" in result["detail"]


@pytest.mark.asyncio
async def test_check_reddit_corpus_db_error_fails(monkeypatch):
    class _FakeSnapshots:
        async def count_documents(self, query):
            raise RuntimeError("atlas unreachable")

    class _FakeDb:
        reddit_post_snapshots = _FakeSnapshots()

    monkeypatch.setattr(doctor.mongo, "_get_db", lambda: _FakeDb())
    result = await doctor.check_reddit_corpus()
    assert result["status"] == "fail"
    assert "atlas unreachable" in result["detail"]
