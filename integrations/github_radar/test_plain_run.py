"""@github-radar's run as plain code (ticket 03), with fixture pages and faked GitHub.

Trending pages come from the 2026-11-26 fixtures. GitHub's REST answers, star
history and HN are faked per repo; every repo not named below answers with an
empty About line and no README, so the scope filter drops it.
"""

import base64
from datetime import datetime, timedelta, timezone
from pathlib import Path

import httpx
import pytest

import agent
from _shared.write_post import validate_publication_input

_NOW = datetime(2026, 11, 26, 12, tzinfo=timezone.utc)
# Sunday 2026-11-22 00:00 UTC: the week holding _NOW.
_THIS_WEEK = 1_795_305_600
_DAY = 86_400
_FIXTURES = Path(__file__).parent / "testdata" / "trending"
_PAGES = {
    "https://github.com/trending?since=daily": "trending-daily.html",
    "https://github.com/trending?since=weekly": "trending-weekly.html",
    "https://github.com/trending/python?since=daily": "trending-python-daily.html",
    "https://github.com/trending/typescript?since=daily": "trending-typescript-daily.html",
}
# Distinct slugs across the four fixture pages, counted by hand from the pages:
# 16 daily, 13 more weekly, 17 more python, 13 more typescript.
_FIXTURE_SLUGS = 59
_FIXTURE_ROWS = 16 + 15 + 24 + 17
_SEARCH_URL = "https://api.github.com/search/repositories"


def _history(days: list[int]) -> list[dict]:
    """Star-history buckets whose last 7 complete days before _NOW are ``days``, newest first."""
    return [
        {"week": _THIS_WEEK, "total": sum(days[:4]), "days": [*days[3::-1], 0, 0, 0]},
        {
            "week": _THIS_WEEK - 7 * _DAY,
            "total": sum(days[4:]),
            "days": [0, 0, 0, 0, days[6], days[5], days[4]],
        },
    ]


def _repo(slug, *, created, stars, forks, topics=(), description=""):
    return {
        "full_name": slug,
        "html_url": f"https://github.com/{slug}",
        "description": description,
        "topics": list(topics),
        "stargazers_count": stars,
        "forks_count": forks,
        "created_at": created,
    }


class _Response:
    def __init__(self, status=200, payload=None, text=""):
        self.status_code = status
        self._payload = payload
        self.text = text
        self.headers = {}

    def json(self):
        return self._payload

    def raise_for_status(self):
        if self.status_code >= 400:
            raise httpx.HTTPStatusError(
                f"HTTP {self.status_code}", request=None, response=None
            )


class _Site:
    """Answers every URL the run reads, and records the requests."""

    def __init__(self):
        self.pages = {
            url: (_FIXTURES / name).read_text() for url, name in _PAGES.items()
        }
        self.repos: dict[str, dict] = {}
        self.repo_status: int | None = None
        self.histories: dict[str, list[dict]] = {}
        self.readmes: dict[str, str] = {}
        self.hn: dict[str, list[dict]] = {}
        self.search: dict[str, list[dict]] = {}
        self.searches: list[dict] = []

    def answer(self, url, params):
        if url in self.pages:
            page = self.pages[url]
            return _Response(page) if isinstance(page, int) else _Response(text=page)
        if url == _SEARCH_URL:
            self.searches.append(params)
            topic = params["q"].split()[0].removeprefix("topic:")
            return _Response(payload={"items": self.search.get(topic, [])})
        if url.startswith("https://hn.algolia.com/"):
            slug = params["query"].strip('"')
            return _Response(payload={"hits": self.hn.get(slug, [])})
        slug, _, rest = url.removeprefix("https://api.github.com/repos/").partition("/")
        slug = f"{slug}/{rest.split('/')[0]}"
        if url.endswith("/stargazers/history"):
            return _Response(payload=self.histories[slug])
        if url.endswith("/readme"):
            if slug not in self.readmes:
                return _Response(404, {"message": "Not Found"})
            content = base64.b64encode(self.readmes[slug].encode()).decode()
            return _Response(payload={"content": content, "encoding": "base64"})
        if self.repo_status:
            return _Response(self.repo_status, {"message": "boom"})
        return _Response(
            payload=self.repos.get(
                slug, _repo(slug, created="2020-01-01T00:00:00Z", stars=1000, forks=10)
            )
        )


class _Collection:
    def __init__(self, posted=None):
        self.posted = posted or {}
        self.inserted: list[dict] = []

    async def create_index(self, *_args, **_kwargs):
        return "index"

    async def insert_many(self, docs):
        self.inserted += docs

        class _Result:
            inserted_ids = list(range(len(docs)))

        return _Result()

    async def find_one(self, query, *_args, **_kwargs):
        days_ago = self.posted.get(query["project.url"])
        if days_ago is None:
            return None
        return {"postedAt": datetime.now(timezone.utc) - timedelta(days=days_ago)}


class _Db:
    """Only the collections @github-radar may touch exist; reading any other raises."""

    def __init__(self, posted=None):
        self._collections = {
            "trending_snapshots": _Collection(),
            "posts": _Collection(posted),
        }
        self.touched: set[str] = set()

    def __getattr__(self, name):
        if name.startswith("_"):
            raise AttributeError(name)
        self.touched.add(name)
        return self._collections[name]


@pytest.fixture
def site(monkeypatch):
    import github_source
    import trending_source

    site = _Site()

    class FakeClient:
        def __init__(self, **_kwargs):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *_args):
            pass

        async def aclose(self):
            pass

        async def get(self, url, params=None, **_kwargs):
            return site.answer(url, params or {})

    async def no_sleep(_seconds):
        pass

    monkeypatch.setattr(httpx, "AsyncClient", FakeClient)
    monkeypatch.setattr(trending_source.asyncio, "sleep", no_sleep)
    monkeypatch.setattr(github_source.asyncio, "sleep", no_sleep)
    return site


@pytest.fixture
def written(monkeypatch):
    posts: list[dict] = []

    async def fake_write_post(
        handle, name, bio, source_type, project, blurb, verdict, signal, rank_score
    ):
        validate_publication_input(project, verdict, rank_score)
        posts.append(
            {
                "handle": handle,
                "project": project,
                "blurb": blurb,
                "verdict": verdict,
                "signal": signal,
                "rank_score": rank_score,
            }
        )
        return f"post-{len(posts)}"

    monkeypatch.setattr(agent, "write_post", fake_write_post)
    return posts


def _trending_day(site):
    """Six named fixture repos: three qualify, one each is on cooldown, off scope and farmed."""
    site.repos.update(
        {
            "elementalsouls/Claude-BugHunter": _repo(
                "elementalsouls/Claude-BugHunter",
                created="2026-11-01T00:00:00Z",
                stars=24_333,
                forks=2_476,
                description="A Claude Code skill bundle for bug hunting",
            ),
            "firecrawl/firecrawl": _repo(
                "firecrawl/firecrawl",
                created="2022-04-15T00:00:00Z",
                stars=210_817,
                forks=11_046,
                topics=["ai", "scraper"],
            ),
            "obra/superpowers": _repo(
                "obra/superpowers",
                created="2025-10-09T00:00:00Z",
                stars=327_351,
                forks=29_029,
                description="An agentic skills framework",
            ),
            "cursor/plugins": _repo(
                "cursor/plugins",
                created="2025-08-01T00:00:00Z",
                stars=19_755,
                forks=2_136,
                topics=["ai", "plugins"],
            ),
            "nginx/nginx": _repo(
                "nginx/nginx",
                created="2015-03-27T00:00:00Z",
                stars=33_501,
                forks=8_418,
                topics=["nginx", "http", "web-server"],
                description="The official NGINX Open Source repository.",
            ),
            "mvanhorn/last30days-skill": _repo(
                "mvanhorn/last30days-skill",
                created="2025-06-01T00:00:00Z",
                stars=75_649,
                forks=6_671,
                description="AI agent skill that researches any topic",
            ),
        }
    )
    site.readmes["nginx/nginx"] = "NGINX is an HTTP web server and reverse proxy."
    site.histories.update(
        {
            "elementalsouls/Claude-BugHunter": _history(
                [1600, 1500, 1400, 1300, 1200, 1100, 900]
            ),
            "firecrawl/firecrawl": _history([1042, 1000, 1000, 1000, 1000, 1000, 958]),
            "obra/superpowers": _history([853, 857, 860, 850, 860, 860, 860]),
            "cursor/plugins": _history([268, 250, 250, 250, 250, 250, 232]),
            "mvanhorn/last30days-skill": _history([5000, 50, 50, 50, 50, 50, 50]),
        }
    )
    # A 300-point story lifts superpowers past firecrawl.
    site.hn["obra/superpowers"] = [
        {
            "objectID": "1",
            "points": 300,
            "num_comments": 40,
            "title": "Superpowers: an agentic skills framework",
            "url": "https://github.com/obra/superpowers",
            "created_at_i": _THIS_WEEK + 2 * _DAY,
        }
    ]


async def test_a_run_writes_qualifying_trending_repos_in_hype_order(
    site, written, capsys
):
    _trending_day(site)
    db = _Db(posted={"https://github.com/cursor/plugins": 3})

    considered = await agent.scan(db, now=_NOW)

    assert [post["project"]["url"] for post in written] == [
        "https://github.com/elementalsouls/Claude-BugHunter",
        "https://github.com/obra/superpowers",
        "https://github.com/firecrawl/firecrawl",
    ]
    assert considered == _FIXTURE_SLUGS
    assert {post["handle"] for post in written} == {"@github-radar"}
    first = written[0]
    # Hype 3.189 (9,000 this week on a 15,333 base, 1,600 yesterday, 25 days old) × 25.
    assert first["rank_score"] == first["project"]["momentumScore"] == 80
    assert first["verdict"] == "emerging"
    assert [post["verdict"] for post in written[1:]] == ["hype looks real"] * 2
    assert first["signal"]["delta"] == 9_000
    assert "9,000" in first["blurb"]
    out = capsys.readouterr().out
    assert "cursor/plugins" in out and "cooldown" in out


async def test_off_scope_and_farmed_repos_are_dropped_with_their_reasons(
    site, written, capsys
):
    _trending_day(site)

    await agent.scan(_Db(), now=_NOW)

    urls = {post["project"]["url"] for post in written}
    assert "https://github.com/nginx/nginx" not in urls
    assert "https://github.com/mvanhorn/last30days-skill" not in urls
    out = capsys.readouterr().out
    nginx = next(line for line in out.splitlines() if "nginx/nginx" in line)
    assert "off-scope" in nginx
    farmed = next(
        line for line in out.splitlines() if "mvanhorn/last30days-skill" in line
    )
    assert "5000 of 5300" in farmed


async def test_a_run_writes_no_signals_snapshot_rows(site, written):
    _trending_day(site)
    db = _Db()

    await agent.scan(db, now=_NOW)

    assert db.touched == {"trending_snapshots", "posts"}
    assert len(db.trending_snapshots.inserted) == _FIXTURE_ROWS


async def test_when_trending_fails_rest_search_supplies_candidates_scored_the_same_way(
    site, written
):
    site.pages["https://github.com/trending?since=daily"] = 503
    young = _repo(
        "acme/young-agent",
        created="2026-10-01T00:00:00Z",
        stars=1_100,
        forks=40,
        topics=["agent"],
    )
    older = _repo(
        "acme/older-llm",
        created="2026-09-01T00:00:00Z",
        stars=5_000,
        forks=90,
        topics=["llm"],
    )
    site.search = {"ai": [older], "llm": [older, young]}
    site.histories["acme/young-agent"] = _history([100, 150, 150, 150, 150, 150, 150])
    site.histories["acme/older-llm"] = _history([20, 20, 20, 20, 20, 20, 30])
    db = _Db()

    considered = await agent.scan(db, now=_NOW)

    assert [s["q"] for s in site.searches] == [
        f"topic:{topic} created:>2026-08-28 stars:>100"
        for topic in ("ai", "llm", "agent")
    ]
    assert {s["sort"] for s in site.searches} == {"stars"}
    assert considered == 2
    assert [post["project"]["url"] for post in written] == [
        "https://github.com/acme/young-agent",
        "https://github.com/acme/older-llm",
    ]
    # Hype 2.4735 (1,000 this week on a 100 floor base, 100 yesterday, 56 days old) × 25.
    assert written[0]["rank_score"] == 62
    assert written[0]["verdict"] == "emerging"
    assert db.trending_snapshots.inserted == []


async def test_more_than_half_the_repo_lookups_failing_raises(site, written):
    site.repo_status = 500

    import github_source

    with pytest.raises(github_source.EnrichmentFailed):
        await agent.scan(_Db(), now=_NOW)
    assert written == []


@pytest.mark.parametrize(
    "hype, score", [(0.0, 0), (1.234, 31), (2.0, 50), (3.99, 100), (4.3, 100)]
)
def test_post_score_is_25_times_hype_capped_at_100(hype, score):
    assert agent.post_score(hype) == score


@pytest.mark.parametrize(
    "age_days, verdict",
    [
        (1, "emerging"),
        (89, "emerging"),
        (90, "hype looks real"),
        (400, "hype looks real"),
    ],
)
def test_verdict_is_emerging_under_90_days_old(age_days, verdict):
    assert agent.verdict_for(_NOW - timedelta(days=age_days), _NOW) == verdict


@pytest.mark.parametrize("considered, active", [(3, True), (0, False)])
async def test_the_runner_runs_the_plain_scan_without_an_agent_or_checkpointer(
    monkeypatch, considered, active
):
    from unittest.mock import AsyncMock

    from _shared import port_client, write_post
    from _shared import runner as runner_module

    def no_checkpointer(*_args, **_kwargs):
        raise AssertionError("the plain scan opened a LangGraph checkpointer")

    async def no_preflight(_handle):
        return []

    seen = {}

    async def fake_summarize(_handle, thread_id, _start, *, agent_was_active):
        seen["active"] = agent_was_active
        return {
            "thread_id": thread_id,
            "posts_today": 0,
            "posts_written": 0,
            "synced_this_run": 0,
            "pending_port_syncs": 0,
            "ok": agent_was_active,
        }

    monkeypatch.setattr(runner_module.MongoDBSaver, "from_conn_string", no_checkpointer)
    monkeypatch.setattr(runner_module.doctor, "preflight", no_preflight)
    monkeypatch.setattr(port_client, "upsert_agent", lambda *_args: {"ok": True})
    monkeypatch.setattr(
        port_client, "record_agent_success", lambda *_args: {"ok": True}
    )
    monkeypatch.setattr(write_post, "repair_pending_posts", AsyncMock(return_value=0))
    monkeypatch.setattr(runner_module, "summarize_run", fake_summarize)
    run_ids = []

    async def plain_scan():
        run_ids.append(write_post.current_run_id.get())
        return considered

    record: dict = {}
    summary = await runner_module._run_agent_cycle(
        "@github-radar",
        "GitHub Radar",
        "bio",
        "github",
        None,
        record,
        plain_scan=plain_scan,
    )

    assert run_ids == [record["threadId"]]
    assert record["candidatesConsidered"] == considered
    assert seen["active"] is active
    assert summary["ok"] is active


async def test_main_hands_the_runner_the_plain_scan_and_no_agent(monkeypatch):
    import main

    calls = {}
    database = object()

    async def fake_run_agent(*args, **kwargs):
        calls["args"], calls["kwargs"] = args, kwargs
        calls["considered"] = await kwargs["plain_scan"]()
        return {"ok": True}

    async def fake_scan(db, now=None):
        calls["db"] = db
        return 7

    monkeypatch.setattr(main, "run_agent", fake_run_agent)
    monkeypatch.setattr(main.mongo, "_get_db", lambda: database)
    monkeypatch.setattr(agent, "scan", fake_scan)

    await main.run_once()

    assert calls["args"] == (
        agent.AGENT_HANDLE,
        agent.AGENT_NAME,
        agent.AGENT_BIO,
        agent.SOURCE_TYPE,
    )
    assert set(calls["kwargs"]) == {"plain_scan"}
    assert calls["db"] is database and calls["considered"] == 7


async def test_a_plain_scan_past_the_runner_deadline_is_stopped(monkeypatch):
    import asyncio
    from unittest.mock import AsyncMock

    from _shared import port_client, write_post
    from _shared import runner as runner_module

    async def no_preflight(_handle):
        return []

    async def hanging_scan():
        await asyncio.Event().wait()

    monkeypatch.setattr(runner_module, "AGENT_INVOCATION_TIMEOUT_SECONDS", 0.01)
    monkeypatch.setattr(runner_module.doctor, "preflight", no_preflight)
    monkeypatch.setattr(port_client, "upsert_agent", lambda *_args: {"ok": True})
    monkeypatch.setattr(write_post, "repair_pending_posts", AsyncMock(return_value=0))

    cycle = asyncio.create_task(
        runner_module._run_agent_cycle(
            "@github-radar",
            "GitHub Radar",
            "bio",
            "github",
            None,
            {},
            plain_scan=hanging_scan,
        )
    )
    done, _ = await asyncio.wait({cycle}, timeout=1)
    cycle.cancel()

    assert cycle in done, "the runner let a hung plain scan run past its deadline"
    assert isinstance(cycle.exception(), TimeoutError)
