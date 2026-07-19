import asyncio
import importlib.util
import io
import json
import urllib.error
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import AsyncMock

import pytest
from bson import ObjectId
from pymongo.errors import DuplicateKeyError


class FakeProcess:
    def __init__(self, output: str):
        self.output = output
        self.returncode = 0

    async def communicate(self):
        return self.output.encode(), b""


class HangingProcess:
    def __init__(self):
        self.returncode = None
        self.killed = False
        self._released = asyncio.Event()

    async def communicate(self):
        await self._released.wait()
        return b"", b""

    def kill(self):
        self.killed = True
        self.returncode = -9
        self._released.set()


def load_source(relative_path: str, module_name: str):
    path = Path(__file__).parents[1] / relative_path
    spec = importlib.util.spec_from_file_location(module_name, path)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


def test_one_catalog_drives_python_agent_identity():
    from _shared.agent_catalog import AGENT_CATALOG, agent_identity

    assert len(AGENT_CATALOG) == 6
    assert len({agent["handle"] for agent in AGENT_CATALOG}) == 6
    assert agent_identity("@youtube-trends")["bio"].startswith("The hype amplifier")


@pytest.mark.asyncio
async def test_youtube_candidate_preserves_channel_and_views(monkeypatch):
    source = load_source("youtube_trends/source.py", "youtube_truth_source")
    monkeypatch.setattr(source.shutil, "which", lambda _: "/usr/local/bin/yt-dlp")
    subprocess_calls = []

    async def fake_create_subprocess_exec(*args, **_kwargs):
        subprocess_calls.append(args)
        return FakeProcess(
            json.dumps(
                {
                    "id": "abc123",
                    "title": "A complete title",
                    "channel": "Signal Channel",
                    "view_count": 285000,
                    "duration": 720,
                    "upload_date": "20260715",
                }
            )
            + "\n"
        )

    monkeypatch.setattr(
        source.asyncio, "create_subprocess_exec", fake_create_subprocess_exec
    )

    candidates = await source.fetch_youtube_candidates(max_results=1)

    assert "--dump-json" in subprocess_calls[0]
    assert "--dateafter" in subprocess_calls[0]
    assert candidates[0]["viewCount"] == 285000
    assert candidates[0]["channel"] == "Signal Channel"
    assert candidates[0]["channel_url"] == source.CHANNELS[0]
    assert "youtube_search_position" not in candidates[0]
    assert "serp_rank" not in candidates[0]
    assert "stars" not in candidates[0]


@pytest.mark.asyncio
async def test_youtube_metadata_delimiters_cannot_forge_observed_counts(monkeypatch):
    source = load_source("youtube_trends/source.py", "youtube_delimiter_source")
    monkeypatch.setattr(source.shutil, "which", lambda _: "/usr/local/bin/yt-dlp")

    async def fake_create_subprocess_exec(*_args, **_kwargs):
        return FakeProcess(
            json.dumps(
                {
                    "id": "safe123",
                    "title": "Breakout|Fake Channel|999999999",
                    "channel": "Real|Channel",
                    "view_count": 123,
                    "duration": 60,
                }
            )
            + "\n"
        )

    monkeypatch.setattr(
        source.asyncio, "create_subprocess_exec", fake_create_subprocess_exec
    )

    candidates = await source.fetch_youtube_candidates(max_results=1)

    assert candidates[0]["title"] == "Breakout|Fake Channel|999999999"
    assert candidates[0]["channel"] == "Real|Channel"
    assert candidates[0]["viewCount"] == 123


@pytest.mark.asyncio
async def test_youtube_source_kills_a_command_that_exceeds_its_deadline(monkeypatch):
    source = load_source("youtube_trends/source.py", "youtube_timeout_source")
    monkeypatch.setattr(source.shutil, "which", lambda _: "/usr/local/bin/yt-dlp")
    monkeypatch.setattr(source, "SOURCE_COMMAND_TIMEOUT_SECONDS", 0.01, raising=False)
    processes = []

    async def fake_create_subprocess_exec(*_args, **_kwargs):
        process = HangingProcess()
        processes.append(process)
        return process

    monkeypatch.setattr(
        source.asyncio, "create_subprocess_exec", fake_create_subprocess_exec
    )

    candidates = await asyncio.wait_for(
        source.fetch_youtube_candidates(max_results=1), timeout=0.2
    )

    assert candidates == []
    assert processes and all(process.killed for process in processes)


@pytest.mark.asyncio
async def test_youtube_source_kills_its_command_when_the_run_is_cancelled(monkeypatch):
    source = load_source("youtube_trends/source.py", "youtube_cancel_source")
    monkeypatch.setattr(source.shutil, "which", lambda _: "/usr/local/bin/yt-dlp")
    processes = []

    async def fake_create_subprocess_exec(*_args, **_kwargs):
        process = HangingProcess()
        processes.append(process)
        return process

    monkeypatch.setattr(
        source.asyncio, "create_subprocess_exec", fake_create_subprocess_exec
    )

    task = asyncio.create_task(source.fetch_youtube_candidates(max_results=1))
    while not processes:
        await asyncio.sleep(0)
    task.cancel()

    with pytest.raises(asyncio.CancelledError):
        await task
    assert all(process.killed for process in processes)


def test_hidden_gem_hn_evidence_never_becomes_github_stars():
    source = load_source("hidden_gems/source.py", "hidden_gems_truth_source")

    candidate = source.normalize_hn_story(
        {
            "type": "story",
            "url": "https://github.com/example/project",
            "title": "Show HN: Example",
            "score": 298,
            "descendants": 42,
        },
        123,
    )

    assert candidate["discovery_source"] == "hacker_news"
    assert candidate["hn_points"] == 298
    assert candidate["hn_comments"] == 42
    assert candidate["evidence_url"] == "https://news.ycombinator.com/item?id=123"
    assert "stars" not in candidate


def test_github_velocity_is_labeled_as_lifetime_average_not_recent_growth():
    source = load_source("github_radar/github_source.py", "github_truth_source")
    candidate = {
        "stars": 700,
        "created_at": (datetime.now(timezone.utc) - timedelta(days=14)).isoformat(),
    }

    momentum = source.compute_momentum(candidate, [], prior_posts=4)

    assert momentum["avgStarsPerWeekSinceCreation"] == pytest.approx(350, abs=30)
    assert momentum["sustainedSixWeekGrowth"] is False
    assert "starsPerWeek" not in momentum


def test_github_sustained_growth_requires_six_observations_spanning_five_weeks():
    source = load_source("github_radar/github_source.py", "github_sustained_source")
    start = datetime.now(timezone.utc) - timedelta(days=35)
    history = [
        {"capturedAt": start + timedelta(days=7 * index), "value": 100 + 10 * index}
        for index in range(6)
    ]
    candidate = {
        "stars": 200,
        "created_at": (start - timedelta(days=10)).isoformat(),
    }

    momentum = source.compute_momentum(candidate, history, prior_posts=5)

    assert momentum["sustainedSixWeekGrowth"] is True


def test_github_sustained_growth_rejects_flat_history_and_a_current_regression():
    source = load_source("github_radar/github_source.py", "github_growth_guard_source")
    start = datetime.now(timezone.utc) - timedelta(days=35)
    flat_history = [
        {"capturedAt": start + timedelta(days=7 * index), "value": 100}
        for index in range(6)
    ]
    rising_history = [
        {"capturedAt": start + timedelta(days=7 * index), "value": 100 + 10 * index}
        for index in range(6)
    ]

    flat = source.compute_momentum(
        {"stars": 100, "created_at": start.isoformat()}, flat_history, prior_posts=1
    )
    regressed = source.compute_momentum(
        {"stars": 140, "created_at": start.isoformat()},
        rising_history,
        prior_posts=1,
    )

    assert flat["sustainedSixWeekGrowth"] is False
    assert regressed["sustainedSixWeekGrowth"] is False


@pytest.mark.asyncio
async def test_github_ossinsight_includes_repos_with_ai_description_not_just_topics(
    monkeypatch,
):
    """A repo with no AI topics but an AI-related description should pass the filter.

    The previous narrow topic filter (ai/llm/agent/gpt/openai/ml) missed 63%
    of OSSInsight trending repos. Description-based matching catches repos
    like stablyai/orca (agent fleet) that lack ai as a topic.
    """
    source = load_source("github_radar/github_source.py", "github_ossinsight_source")
    monkeypatch.setattr(source, "_token", "fake-token")
    monkeypatch.setattr(source, "_headers", {"Authorization": "token fake-token"})

    class FakeResponse:
        def __init__(self, data, status=200):
            self._data = data
            self.status_code = status

        def json(self):
            return self._data

        def raise_for_status(self):
            if self.status_code != 200:
                raise RuntimeError(f"HTTP {self.status_code}")

    class FakeClient:
        call_count = 0

        def __init__(self, **_kw):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *a):
            pass

        async def get(self, url, **kw):
            type(self).call_count += 1
            if "ossinsight" in url:
                return FakeResponse(
                    {
                        "data": {
                            "rows": [
                                {
                                    "repo_name": "stablyai/orca",
                                    "description": "Agent fleet manager",
                                    "stars": 6,
                                    "primary_language": "Python",
                                },
                                {
                                    "repo_name": "someuser/nonai",
                                    "description": "A recipe app",
                                    "stars": 5,
                                    "primary_language": "Go",
                                },
                            ]
                        }
                    }
                )
            # GitHub API call for repo details
            if "stablyai/orca" in url:
                return FakeResponse(
                    {
                        "topics": ["cli", "automation"],
                        "description": "Agent fleet manager for parallel agents",
                        "stargazers_count": 500,
                        "created_at": "2026-06-01",
                        "pushed_at": "2026-07-19",
                    }
                )
            return FakeResponse(
                {
                    "topics": ["recipes"],
                    "description": "A recipe app",
                    "stargazers_count": 10,
                    "created_at": "2026-01-01",
                    "pushed_at": "2026-07-01",
                },
                status=200,
            )

    monkeypatch.setattr(source.httpx, "AsyncClient", FakeClient)
    candidates = await source._fetch_ossinsight_trending(max_results=5)
    titles = [c["title"] for c in candidates]
    assert "stablyai/orca" in titles
    assert "someuser/nonai" not in titles

    from _shared import mongo

    post_id = ObjectId()
    signal_id = ObjectId()

    class FakeCursor:
        def __init__(self, documents):
            self.documents = documents

        def sort(self, *_args):
            return self

        def limit(self, *_args):
            return self

        async def to_list(self, length=None):
            return list(self.documents if length is None else self.documents[:length])

    class FakePosts:
        query = None

        def find(self, query, _projection):
            self.query = query
            return FakeCursor([{"_id": post_id}])

    class FakeReceipts:
        query = None

        def find(self, query, _projection):
            self.query = query
            return FakeCursor([{"signalId": signal_id}])

    class FakeSignals:
        query = None

        def find(self, query):
            self.query = query
            return FakeCursor([])

    class FakeDatabase:
        posts = FakePosts()
        signal_receipts = FakeReceipts()
        signals = FakeSignals()

    database = FakeDatabase()
    monkeypatch.setattr(mongo, "_get_db", lambda: database)

    await mongo.get_momentum_history(
        "https://github.com/example/project",
        source="github",
        metric="github_stars",
    )

    assert database.posts.query == {
        "project.url": "https://github.com/example/project",
        "portSyncStatus": "synced",
        "evidenceContractVersion": 2,
        "legacyDuplicateOf": {"$exists": False},
    }
    assert database.signal_receipts.query == {
        "_id": {"$in": [str(post_id)]},
        "state": "complete",
        "signal.projectId": "https://github.com/example/project",
        "signal.source": "github",
        "signal.metric": "github_stars",
    }
    assert database.signals.query == {
        "_id": {"$in": [signal_id]},
        "projectId": "https://github.com/example/project",
        "source": "github",
        "metric": "github_stars",
    }


@pytest.mark.asyncio
async def test_weekly_digest_summary_is_derived_only_from_synchronized_wave_counts(
    monkeypatch,
):
    from _shared import hype_waves

    agent = load_source("weekly_digest/agent.py", "weekly_digest_safe_agent")
    updates = []
    published = {}

    waves = [
        {
            "label": "growth exploded",
            "projects": [
                {"url": "https://example.com/one", "momentumScore": 70},
                {"url": "https://example.com/two", "momentumScore": 80},
            ],
        },
        {
            "label": "doubled adoption",
            "projects": [
                {"url": "https://example.com/two", "momentumScore": 80},
            ],
        },
    ]

    class Digests:
        async def find_one(self, _query):
            return None

        async def update_one(self, _query, update, **_kwargs):
            updates.append(update)

    class Database:
        digests = Digests()

    async def capture_write(*args, **_kwargs):
        published.update({"project": args[4], "body": args[5]})
        return "507f1f77bcf86cd799439011"

    monkeypatch.setattr(agent.mongo, "_get_db", lambda: Database())
    monkeypatch.setattr(agent, "write_post", capture_write)
    monkeypatch.setattr(hype_waves, "compute_hype_waves", lambda: waves)

    await agent.write_digest.coroutine()

    summary = updates[-1]["$set"]["summary"]
    assert summary == (
        "This weekly edit connects 2 synchronized source projects across 2 "
        "semantic themes. Open each project dossier for source-labeled evidence."
    )
    assert published["project"]["description"] == summary
    assert published["body"].startswith(summary)
    assert "growth" not in summary
    assert "adoption" not in summary


@pytest.mark.asyncio
async def test_weekly_digest_retry_resynchronizes_the_exact_staged_snapshot(
    monkeypatch,
):
    from _shared import hype_waves

    agent = load_source("weekly_digest/agent.py", "weekly_digest_retry_agent")
    staged_at = datetime(2026, 7, 13, tzinfo=timezone.utc)
    staged_waves = [
        {
            "label": "agent memory",
            "projects": [
                {"url": "https://example.com/staged", "momentumScore": 40},
            ],
        }
    ]
    staged_summary = (
        "This weekly edit connects 1 synchronized source project across 1 "
        "semantic theme. Open each project dossier for source-labeled evidence."
    )
    existing = {
        "weekId": "2026-W28",
        "weekOf": staged_at,
        "summary": staged_summary,
        "waves": staged_waves,
        "rankScore": 40,
        "publicationSyncStatus": "pending",
        "evidenceContractVersion": 2,
    }
    updates = []
    published = {}
    compute_calls = 0

    class Digests:
        async def find_one(self, _query):
            return existing

        async def update_one(self, _query, update, **_kwargs):
            updates.append(update)

    class Database:
        digests = Digests()

    def recompute_newer_waves():
        nonlocal compute_calls
        compute_calls += 1
        return [
            {
                "label": "new unsynchronized wave",
                "projects": [
                    {"url": "https://example.com/new", "momentumScore": 99},
                ],
            }
        ]

    async def capture_write(*args, **_kwargs):
        published.update({"project": args[4], "body": args[5], "rank": args[8]})
        return "507f1f77bcf86cd799439011"

    monkeypatch.setattr(agent.mongo, "_get_db", lambda: Database())
    monkeypatch.setattr(agent, "write_post", capture_write)
    monkeypatch.setattr(hype_waves, "compute_hype_waves", recompute_newer_waves)

    await agent.write_digest.coroutine()

    assert compute_calls == 0
    assert published["project"]["description"] == staged_summary
    assert published["project"]["momentumScore"] == 40
    assert published["body"].startswith(staged_summary)
    assert published["rank"] == 40
    assert updates[-1]["$set"]["waves"] == staged_waves
    assert updates[-1]["$set"]["weekOf"] == staged_at


def test_weekly_digest_rank_comes_from_clustered_project_scores():
    agent = load_source("weekly_digest/agent.py", "weekly_digest_rank_agent")

    assert agent.digest_rank_score([]) == 0
    assert (
        agent.digest_rank_score(
            [
                {
                    "avgMomentum": 100,
                    "projects": [
                        {
                            "url": "hyperadar://digest/2026-W28",
                            "momentumScore": 100,
                        },
                        {
                            "url": "https://example.com/source-one",
                            "momentumScore": 72.5,
                        },
                    ],
                },
                {
                    "avgMomentum": 81.5,
                    "projects": [
                        {
                            "url": "https://example.com/source-two",
                            "momentumScore": 81.5,
                        }
                    ],
                },
            ]
        )
        == 77.0
    )


def test_weekly_digest_input_dedupes_source_projects_before_the_limit():
    agent = load_source("weekly_digest/agent.py", "weekly_digest_pipeline_agent")
    since = datetime.now(timezone.utc) - timedelta(days=7)

    pipeline = agent.weekly_post_pipeline(since)

    assert list(pipeline[0]) == ["$match"]
    assert pipeline[0]["$match"]["agentHandle"] == {
        "$in": [
            "@github-radar",
            "@reddit-pulse",
            "@youtube-trends",
            "@hidden-gems",
        ]
    }
    assert pipeline[0]["$match"]["evidenceContractVersion"] == 2
    assert pipeline[1:] == [
        {"$sort": {"rankScore": -1, "postedAt": -1}},
        {"$group": {"_id": "$project.url", "post": {"$first": "$$ROOT"}}},
        {"$replaceRoot": {"newRoot": "$post"}},
        {"$sort": {"rankScore": -1, "postedAt": -1}},
        {"$limit": 15},
    ]


@pytest.mark.asyncio
async def test_weekly_digest_publishes_only_after_write_post_succeeds(monkeypatch):
    from _shared import hype_waves

    agent = load_source("weekly_digest/agent.py", "weekly_digest_publication_agent")
    updates = []
    events = []

    class Digests:
        async def find_one(self, _query):
            return None

        async def update_one(self, query, update, **kwargs):
            updates.append((query, update, kwargs))
            events.append(update["$set"]["publicationSyncStatus"])

    class Database:
        digests = Digests()

    async def successful_write(*_args, **_kwargs):
        events.append("port-synced-post")
        return "507f1f77bcf86cd799439011"

    database = Database()
    monkeypatch.setattr(agent.mongo, "_get_db", lambda: database)
    assert "db" not in vars(agent.mongo)
    monkeypatch.setattr(agent, "write_post", successful_write)
    monkeypatch.setattr(hype_waves, "compute_hype_waves", lambda: [])

    await agent.write_digest.coroutine()

    assert events == ["pending", "port-synced-post", "synced"]
    assert updates[-1][1]["$set"]["publicationSyncStatus"] == "synced"


@pytest.mark.asyncio
async def test_weekly_digest_remains_private_when_write_post_fails(monkeypatch):
    from _shared import hype_waves

    agent = load_source("weekly_digest/agent.py", "weekly_digest_failure_agent")
    updates = []

    class Digests:
        async def find_one(self, _query):
            return None

        async def update_one(self, query, update, **kwargs):
            updates.append((query, update, kwargs))

    class Database:
        digests = Digests()

    async def failed_write(*_args, **_kwargs):
        raise RuntimeError("Port unavailable")

    database = Database()
    monkeypatch.setattr(agent.mongo, "_get_db", lambda: database)
    monkeypatch.setattr(agent, "write_post", failed_write)
    monkeypatch.setattr(hype_waves, "compute_hype_waves", lambda: [])

    with pytest.raises(RuntimeError, match="Port unavailable"):
        await agent.write_digest.coroutine()

    assert len(updates) == 1
    assert updates[0][1]["$set"]["publicationSyncStatus"] == "pending"


@pytest.mark.asyncio
async def test_episode_seed_closes_its_sync_and_async_mongo_clients(monkeypatch):
    from _shared import mongo

    seed = load_source("_shared/seed_episodes.py", "episode_seed_lifecycle")
    closed = {"sync": False, "async": False}

    class Posts:
        def aggregate(self, _pipeline):
            return []

    class Episodes:
        def count_documents(self, _query):
            return 0

    class Database:
        posts = Posts()
        episodes = Episodes()

    class Client:
        def __getitem__(self, _name):
            return Database()

        def close(self):
            closed["sync"] = True

    async def close_async_client():
        closed["async"] = True

    monkeypatch.setattr(seed.pymongo, "MongoClient", lambda *_args, **_kwargs: Client())
    monkeypatch.setattr(mongo, "close_client", close_async_client)

    await seed.seed_episodes()

    assert closed == {"sync": True, "async": True}


@pytest.mark.asyncio
async def test_reddit_candidate_returns_structured_upvote_data(monkeypatch):
    source = load_source("reddit_pulse/reddit_source.py", "reddit_truth_source")
    monkeypatch.setattr(source.shutil, "which", lambda _: "/usr/local/bin/bdata")
    full_url = (
        "https://www.reddit.com/r/LocalLLaMA/comments/abc123/"
        "a_complete_title_that_must_never_be_truncated/"
    )
    subprocess_calls = []

    async def fake_create_subprocess_exec(*args, **_kwargs):
        subprocess_calls.append(args)
        return FakeProcess(
            json.dumps(
                [
                    {
                        "url": full_url,
                        "title": "A complete Reddit title | with a separator",
                        "description": "Search result description",
                        "num_upvotes": 342,
                        "num_comments": 89,
                        "community_name": "LocalLLaMA",
                    }
                ]
            )
        )

    monkeypatch.setattr(
        source.asyncio, "create_subprocess_exec", fake_create_subprocess_exec
    )

    candidates = await source.fetch_reddit_candidates(max_results=1)

    assert "reddit_posts" in subprocess_calls[0]
    assert "--format" in subprocess_calls[0]
    assert candidates[0]["url"] == full_url
    assert candidates[0]["title"] == "A complete Reddit title | with a separator"
    assert candidates[0]["num_upvotes"] == 342
    assert candidates[0]["num_comments"] == 89
    assert candidates[0]["subreddit"] == "LocalLLaMA"
    assert candidates[0]["visibility_score"] > 0
    assert candidates[0]["evidence_url"] == full_url


@pytest.mark.asyncio
async def test_community_source_parses_rombot_api_response(monkeypatch):
    """The community source calls the RomBot API and parses the text answer."""
    source = load_source("community_ask/source.py", "community_truth_source")
    monkeypatch.setattr(
        source.os, "environ", {"ROMBOT_COMMUNITY_ASK_TOKEN": "fake-token"}
    )

    class FakeResponse:
        def __init__(self, data):
            self._data = data
            self.status_code = 200

        def json(self):
            return self._data

        def raise_for_status(self):
            pass

    class FakeClient:
        def __init__(self, **_kw):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *a):
            pass

        async def post(self, url, **kw):
            assert "api.rombot.uk" in url
            assert kw["headers"]["X-Community-Ask-Token"] == "fake-token"
            return FakeResponse(
                {
                    "answer": (
                        "TOPIC: LangGraph vs CrewAI for multi-agent workflows\n"
                        "WHO: @devbuilder\n"
                        "SUMMARY: Community compared LangGraph's graph-based orchestration with CrewAI's role-based approach.\n"
                        "CONTRIBUTORS: ~10-15\n"
                        "TOPIC: Coding agents replacing IDE plugins\n"
                        "WHO: @aieng\n"
                        "SUMMARY: Discussion on whether Claude Code and Cursor agents replace traditional IDE extensions.\n"
                        "CONTRIBUTORS: ~8-12\n"
                    ),
                    "model": "grove",
                    "latency_ms": 3500,
                }
            )

    monkeypatch.setattr(source.httpx, "AsyncClient", FakeClient)
    candidates = await source.fetch_community_candidates(max_results=5)

    assert len(candidates) == 2
    assert candidates[0]["title"] == "LangGraph vs CrewAI for multi-agent workflows"
    assert candidates[0]["kind"] == "discussion"
    assert candidates[0]["num_contributors"] == 15  # upper bound of ~10-15
    assert candidates[0]["visibility_score"] > 0
    assert candidates[1]["title"] == "Coding agents replacing IDE plugins"
    assert candidates[1]["num_contributors"] == 12  # upper bound of ~8-12


def test_community_evidence_copy_describes_real_discourse():
    from _shared.evidence_copy import community_evidence_copy

    assert community_evidence_copy(12) == (
        "12 community members discussed this in the AI Agents Community "
        "corpus. Real developer discourse, not search visibility."
    )


@pytest.mark.asyncio
async def test_reddit_source_kills_a_command_that_exceeds_its_deadline(monkeypatch):
    source = load_source("reddit_pulse/reddit_source.py", "reddit_timeout_source")
    monkeypatch.setattr(source.shutil, "which", lambda _: "/usr/local/bin/bdata")
    monkeypatch.setattr(source, "SOURCE_COMMAND_TIMEOUT_SECONDS", 0.01, raising=False)
    processes = []

    async def fake_create_subprocess_exec(*_args, **_kwargs):
        process = HangingProcess()
        processes.append(process)
        return process

    monkeypatch.setattr(
        source.asyncio, "create_subprocess_exec", fake_create_subprocess_exec
    )

    candidates = await asyncio.wait_for(
        source.fetch_reddit_candidates(max_results=1), timeout=0.2
    )

    assert candidates == []
    assert processes and all(process.killed for process in processes)


@pytest.mark.asyncio
async def test_reddit_source_kills_its_command_when_the_run_is_cancelled(monkeypatch):
    source = load_source("reddit_pulse/reddit_source.py", "reddit_cancel_source")
    monkeypatch.setattr(source.shutil, "which", lambda _: "/usr/local/bin/bdata")
    processes = []

    async def fake_create_subprocess_exec(*_args, **_kwargs):
        process = HangingProcess()
        processes.append(process)
        return process

    monkeypatch.setattr(
        source.asyncio, "create_subprocess_exec", fake_create_subprocess_exec
    )

    task = asyncio.create_task(source.fetch_reddit_candidates(max_results=1))
    while not processes:
        await asyncio.sleep(0)
    task.cancel()

    with pytest.raises(asyncio.CancelledError):
        await task
    assert all(process.killed for process in processes)


def test_port_catalog_errors_cannot_be_silenced():
    from _shared.port_client import require_success

    success = {"ok": True, "entity": {"identifier": "project"}}
    assert require_success(success, "sync project") is success

    with pytest.raises(RuntimeError, match="sync project.*network unavailable"):
        require_success(
            {"ok": False, "error": "network_error", "message": "network unavailable"},
            "sync project",
        )


def test_quarantine_cleanup_deletes_the_post_twin(monkeypatch):
    from _shared import port_client

    calls = []
    monkeypatch.setattr(
        port_client,
        "_req",
        lambda method, path: calls.append((method, path)) or {"ok": True},
    )

    assert port_client.delete_post_entity("post-123") == {"ok": True}
    assert calls == [("DELETE", "/blueprints/hyperadar_post/entities/post-123")]


def test_port_upsert_creates_only_after_a_not_found_response(monkeypatch):
    from _shared import port_client

    calls = []
    responses = iter(
        [
            {"ok": False, "status": 404, "message": "missing"},
            {"ok": True},
        ]
    )

    def request(method, path, body=None, **_kwargs):
        calls.append((method, path, body))
        return next(responses)

    monkeypatch.setattr(port_client, "_req", request)

    result = port_client._upsert("example", "entity", {"title": "Example"})

    assert result == {"ok": True}
    assert [call[0] for call in calls] == ["PATCH", "POST"]


def test_port_upsert_converges_when_another_worker_creates_first(monkeypatch):
    from _shared import port_client

    calls = []
    responses = iter(
        [
            {"ok": False, "status": 404, "message": "missing"},
            {"ok": False, "status": 409, "message": "already exists"},
            {"ok": True},
        ]
    )

    def request(method, path, body=None, **_kwargs):
        calls.append((method, path, body))
        return next(responses)

    monkeypatch.setattr(port_client, "_req", request)

    result = port_client._upsert("example", "entity", {"title": "Example"})

    assert result == {"ok": True}
    assert [call[0] for call in calls] == ["PATCH", "POST", "PATCH"]


def test_port_upsert_does_not_turn_an_outage_into_a_create(monkeypatch):
    from _shared import port_client

    calls = []

    def request(method, path, body=None, **_kwargs):
        calls.append((method, path, body))
        return {"ok": False, "status": 503, "message": "unavailable"}

    monkeypatch.setattr(port_client, "_req", request)

    result = port_client._upsert("example", "entity", {"title": "Example"})

    assert result["status"] == 503
    assert [call[0] for call in calls] == ["PATCH"]


def test_port_client_retries_rate_limits_using_retry_after(monkeypatch):
    from _shared import port_client

    calls = []
    sleeps = []

    class Response:
        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return False

        def read(self):
            return b'{"ok": true}'

    responses = iter(
        [
            urllib.error.HTTPError(
                "https://api.getport.io/v1/example",
                429,
                "rate limited",
                {"Retry-After": "0"},
                io.BytesIO(b'{"message": "slow down"}'),
            ),
            Response(),
        ]
    )

    def urlopen(*_args, **_kwargs):
        calls.append(True)
        response = next(responses)
        if isinstance(response, Exception):
            raise response
        return response

    monkeypatch.setattr(port_client, "_token", lambda: "test-token")
    monkeypatch.setattr(port_client.urllib.request, "urlopen", urlopen)
    monkeypatch.setattr(port_client.time, "sleep", sleeps.append)

    result = port_client._req("GET", "/example")

    assert result == {"ok": True}
    assert len(calls) == 2
    assert sleeps == [0.0]


def test_port_retry_after_is_bounded_below_the_reconciliation_lease():
    from _shared import port_client

    assert port_client._retry_delay({"Retry-After": "9999"}, 0) == 30


def test_agent_identity_sync_preserves_operator_health_state(monkeypatch):
    from _shared import port_client

    captured = {}

    def capture_upsert(blueprint, identifier, payload, create_defaults=None):
        captured.update(
            {
                "blueprint": blueprint,
                "identifier": identifier,
                "payload": payload,
                "create_defaults": create_defaults,
            }
        )
        return {"ok": True}

    monkeypatch.setattr(port_client, "_upsert", capture_upsert)

    port_client.upsert_agent("@test-agent", "Test Agent", "Bio", "test")

    properties = captured["payload"]["properties"]
    assert properties == {
        "handle": "@test-agent",
        "name": "Test Agent",
        "bio": "Bio",
        "sourceType": "test",
    }
    assert captured["create_defaults"] == {"properties": {"status": "active"}}


def test_source_evidence_copy_is_generated_only_from_observed_values():
    from _shared.evidence_copy import (
        github_evidence_copy,
        hidden_gem_evidence_copy,
        reddit_evidence_copy,
        youtube_evidence_copy,
    )

    assert github_evidence_copy(338.4, 2294, False) == (
        "AVG 338.4★/wk since creation. 2,294 GitHub stars observed; "
        "recent growth was not independently measured."
    )
    assert youtube_evidence_copy(362154) == (
        "362,154 YouTube views observed. Search surfaced this video; "
        "upload-age view velocity was not measured."
    )
    assert hidden_gem_evidence_copy("hacker_news", 293) == (
        "293 HN points observed. Early attention—not GitHub stars or a proven trajectory."
    )
    assert reddit_evidence_copy(342, 89) == (
        "342 Reddit upvotes observed across 89 comments. "
        "Hot-post engagement, not GitHub stars."
    )


def test_publication_input_rejects_values_that_port_cannot_store():
    from _shared.write_post import validate_publication_input

    project = {
        "url": "https://example.com/project",
        "title": "Project",
        "kind": "repo",
        "momentumScore": 50,
    }
    validate_publication_input(project, "emerging", 50)

    with pytest.raises(ValueError, match="verdict"):
        validate_publication_input(project, "definitely viral", 50)
    with pytest.raises(ValueError, match="kind"):
        validate_publication_input({**project, "kind": "podcast"}, "emerging", 50)
    with pytest.raises(ValueError, match="URL"):
        validate_publication_input({**project, "url": ""}, "emerging", 50)
    with pytest.raises(ValueError, match="rank score"):
        validate_publication_input(project, "emerging", 101)


def test_signal_snapshot_preserves_only_auditable_source_locators():
    from _shared.write_post import _signal_snapshot

    stored = _signal_snapshot(
        {
            "source": "reddit",
            "metric": "search_visibility_proxy",
            "value": 70,
            "delta": 0,
            "evidenceUrl": "https://www.google.com/search?q=agents",
            "evidenceLabel": "Re-run source query",
            "sourceQuery": "site:reddit.com AI agents",
            "modelScratchpad": "must never persist",
        },
        "reddit",
        "https://reddit.com/r/example/comments/123/example",
    )

    assert stored["evidenceUrl"].startswith("https://www.google.com/search?")
    assert stored["evidenceLabel"] == "Re-run source query"
    assert stored["sourceQuery"] == "site:reddit.com AI agents"
    assert "modelScratchpad" not in stored


def test_signal_lease_outlives_the_driver_io_budget():
    from _shared import mongo

    assert mongo.SIGNAL_LEASE_SECONDS > mongo.MONGO_IO_BUDGET_SECONDS


def test_port_projects_keep_internal_identity_but_publish_an_https_digest_url(
    monkeypatch,
):
    from _shared import port_client
    from _shared.slug import project_slug_for_url

    captured = {}

    def capture_upsert(blueprint, identifier, payload, create_defaults=None):
        captured.update(
            {
                "blueprint": blueprint,
                "identifier": identifier,
                "payload": payload,
                "create_defaults": create_defaults,
            }
        )
        return {"ok": True}

    monkeypatch.setenv("NEXT_PUBLIC_APP_URL", "https://hyperadar.example")
    monkeypatch.setattr(port_client, "_upsert", capture_upsert)

    port_client.upsert_project(
        "hyperadar://digest/2026-W27",
        "Weekly digest",
        "site",
        "Summary",
        ["digest"],
        70,
        "emerging",
    )

    assert captured["identifier"] == project_slug_for_url("hyperadar://digest/2026-W27")
    assert (
        captured["payload"]["properties"]["url"]
        == "https://hyperadar.example/digest/2026-W27"
    )


def test_successful_run_records_time_without_inventing_metrics(monkeypatch):
    from _shared import port_client

    captured = {}

    def capture_request(method, path, body=None, **_kwargs):
        captured.update({"method": method, "path": path, "body": body})
        return {"ok": True}

    monkeypatch.setattr(port_client, "_req", capture_request)

    port_client.record_agent_success("@test-agent")

    assert captured["method"] == "PATCH"
    assert captured["path"].endswith("/entities/test-agent")
    assert set(captured["body"]["properties"]) == {"lastRunAt"}


def test_reaction_index_deduplicates_likes_without_blocking_discussion(db):
    post_id = f"reaction-index-{datetime.now(timezone.utc).timestamp()}"
    base = {"postId": post_id, "userId": "same-user"}
    try:
        db.reactions.insert_one({**base, "type": "like"})
        with pytest.raises(DuplicateKeyError):
            db.reactions.insert_one({**base, "type": "like"})

        db.reactions.insert_many(
            [
                {**base, "type": "share", "attempt": 1},
                {**base, "type": "share", "attempt": 2},
                {**base, "type": "comment", "text": "first"},
                {**base, "type": "comment", "text": "second"},
            ]
        )
        assert db.reactions.count_documents({"postId": post_id}) == 5
    finally:
        db.reactions.delete_many({"postId": post_id})


def test_embedding_audit_is_unique_per_post(db):
    post_id = f"audit-index-{datetime.now(timezone.utc).timestamp()}"
    try:
        db.embeddings_audit.insert_one({"postId": post_id, "dims": 384})
        with pytest.raises(DuplicateKeyError):
            db.embeddings_audit.insert_one({"postId": post_id, "dims": 384})
    finally:
        db.embeddings_audit.delete_many({"postId": post_id})


@pytest.mark.asyncio
async def test_publication_claim_is_atomic_under_concurrency(db):
    from _shared import mongo

    unique = datetime.now(timezone.utc).timestamp()
    publication_key = f"publication-claim-{unique}"
    project_url = f"https://example.com/{publication_key}"
    post = {
        "agentHandle": "@claim-test",
        "body": "One claim, regardless of concurrent workers.",
        "project": {"url": project_url},
        "portSyncStatus": "pending",
    }
    try:
        claims = await asyncio.gather(
            *(mongo.claim_post(publication_key, post) for _ in range(8))
        )

        assert len({post_id for post_id, _created in claims}) == 1
        assert sum(created for _post_id, created in claims) == 1
        assert db.posts.count_documents({"publicationKey": publication_key}) == 1
    finally:
        db.posts.delete_many({"publicationKey": publication_key})


@pytest.mark.asyncio
async def test_signal_receipt_allows_one_time_series_append_under_concurrency(db):
    from _shared import mongo

    post_id = f"signal-receipt-{datetime.now(timezone.utc).timestamp()}"
    project_url = f"https://example.com/{post_id}"
    signal = {
        "projectId": project_url,
        "source": "test",
        "metric": "observed_mentions",
        "value": 42,
        "delta": 7,
    }
    try:
        await asyncio.gather(*(mongo.ensure_signal(post_id, signal) for _ in range(8)))

        assert db.signals.count_documents({"postId": post_id}) == 1
        receipt = db.signal_receipts.find_one({"_id": post_id})
        assert receipt["state"] == "complete"
        assert receipt["signal"]["value"] == 42
    finally:
        db.signals.delete_many({"postId": post_id})
        db.signal_receipts.delete_many({"_id": post_id})


@pytest.mark.asyncio
async def test_signal_receipt_takeover_exposes_only_the_fenced_canonical_append(
    db, monkeypatch
):
    from _shared import mongo

    post_id = f"signal-takeover-{datetime.now(timezone.utc).timestamp()}"
    project_url = f"https://example.com/{post_id}"
    signal = {
        "projectId": project_url,
        "source": "test",
        "metric": "observed_mentions",
        "value": 42,
        "delta": 7,
    }
    database = mongo._get_db()
    first_insert_paused = asyncio.Event()
    release_first_insert = asyncio.Event()
    insert_calls = 0

    class PausingSignals:
        async def find_one(self, *args, **kwargs):
            return await database.signals.find_one(*args, **kwargs)

        async def insert_one(self, document):
            nonlocal insert_calls
            insert_calls += 1
            if insert_calls == 1:
                first_insert_paused.set()
                await release_first_insert.wait()
            return await database.signals.insert_one(document)

    class DatabaseProxy:
        signal_receipts = database.signal_receipts
        signals = PausingSignals()

    clock = [datetime.now(timezone.utc)]
    monkeypatch.setattr(mongo, "_get_db", lambda: DatabaseProxy())
    monkeypatch.setattr(mongo, "_now", lambda: clock[0])

    try:
        first_owner = asyncio.create_task(mongo.ensure_signal(post_id, signal))
        # Timeouts are Atlas-tolerant: this test verifies lease-takeover logic,
        # not latency. The production signal-receipt budget is 125s; a tight 5s
        # budget cancels the task under real Atlas load, and the cancellation
        # races with the autouse close_client() fixture, surfacing as a
        # misleading "Cannot use AsyncMongoClient after close".
        await asyncio.wait_for(first_insert_paused.wait(), timeout=30)
        clock[0] += timedelta(seconds=mongo.SIGNAL_LEASE_SECONDS + 1)

        await asyncio.wait_for(mongo.ensure_signal(post_id, signal), timeout=30)
        release_first_insert.set()
        await asyncio.wait_for(first_owner, timeout=30)

        receipt = db.signal_receipts.find_one({"_id": post_id})
        assert receipt["state"] == "complete"
        assert receipt["leaseEpoch"] == 2
        assert db.signals.count_documents({"_id": receipt["signalId"]}) == 1
        assert (
            db.signals.count_documents({"postId": post_id, "_id": receipt["signalId"]})
            == 1
        )
    finally:
        release_first_insert.set()
        db.signals.delete_many({"postId": post_id})
        db.signal_receipts.delete_many({"_id": post_id})


@pytest.mark.asyncio
async def test_concurrent_write_post_calls_publish_one_daily_claim(db, monkeypatch):
    from _shared import embeddings, episodic_memory, port_client
    from _shared import write_post as write_post_module

    unique = datetime.now(timezone.utc).timestamp()
    url = f"https://example.com/concurrent-write-{unique}"
    project = {
        "url": url,
        "title": "Concurrent publication proof",
        "kind": "site",
        "description": "Every worker saw the same source item.",
        "topics": ["concurrency"],
        "momentumScore": 61,
        "hypeVerdict": "emerging",
    }
    signal = {"source": "test", "metric": "mentions", "value": 8, "delta": 3}

    async def no_prior_episodes(*_args, **_kwargs):
        return []

    monkeypatch.setattr(embeddings, "embed_project", lambda *_args: [0.1] * 384)
    monkeypatch.setattr(episodic_memory, "retrieve_similar_episodes", no_prior_episodes)
    monkeypatch.setattr(port_client, "upsert_agent", lambda *_args: {"ok": True})
    monkeypatch.setattr(port_client, "upsert_project", lambda *_args: {"ok": True})
    monkeypatch.setattr(port_client, "upsert_post", lambda *_args: {"ok": True})

    try:
        post_ids = await asyncio.gather(
            *(
                write_post_module.write_post(
                    "@concurrent-agent",
                    "Concurrent Agent",
                    "Tests atomic publication claims",
                    "test",
                    project,
                    "Only one public claim should exist.",
                    "emerging",
                    signal,
                    61,
                )
                for _ in range(8)
            )
        )

        assert len(set(post_ids)) == 1
        assert db.posts.count_documents({"project.url": url}) == 1
        assert db.signals.count_documents({"projectId": url}) == 1
        stored = db.posts.find_one({"project.url": url})
        assert isinstance(stored["publicationKey"], str)
        assert stored["publicationDay"] == datetime.now(timezone.utc).date().isoformat()
    finally:
        posts = list(db.posts.find({"project.url": url}, {"_id": 1}))
        post_ids = [str(post["_id"]) for post in posts]
        db.posts.delete_many({"project.url": url})
        db.projects.delete_many({"url": url})
        db.signals.delete_many({"projectId": url})
        db.signal_receipts.delete_many({"_id": {"$in": post_ids}})
        db.embeddings_audit.delete_many({"projectId": url})


@pytest.mark.asyncio
async def test_concurrent_agents_reconcile_multi_source_boost(db, monkeypatch):
    from _shared import embeddings, episodic_memory, port_client
    from _shared import write_post as write_post_module

    unique = datetime.now(timezone.utc).timestamp()
    url = f"https://example.com/concurrent-agents-{unique}"
    project = {
        "url": url,
        "title": "Concurrent agent convergence",
        "kind": "site",
        "description": "Two independent agents found the same project.",
        "topics": ["concurrency"],
        "momentumScore": 60,
        "hypeVerdict": "emerging",
    }

    async def no_prior_episodes(*_args, **_kwargs):
        return []

    monkeypatch.setattr(embeddings, "embed_project", lambda *_args: [0.1] * 384)
    monkeypatch.setattr(episodic_memory, "retrieve_similar_episodes", no_prior_episodes)
    monkeypatch.setattr(port_client, "upsert_agent", lambda *_args: {"ok": True})
    monkeypatch.setattr(port_client, "upsert_project", lambda *_args: {"ok": True})
    monkeypatch.setattr(port_client, "upsert_post", lambda *_args: {"ok": True})

    try:
        await asyncio.gather(
            write_post_module.write_post(
                "@concurrent-one",
                "Concurrent One",
                "First independent agent",
                "test",
                project,
                "First synchronized observation.",
                "emerging",
                {"source": "one", "metric": "mentions", "value": 1, "delta": 0},
                60,
            ),
            write_post_module.write_post(
                "@concurrent-two",
                "Concurrent Two",
                "Second independent agent",
                "test",
                project,
                "Second synchronized observation.",
                "emerging",
                {"source": "two", "metric": "mentions", "value": 1, "delta": 0},
                60,
            ),
        )

        posts = list(db.posts.find({"project.url": url}))
        assert len(posts) == 2
        assert {post["rankScore"] for post in posts} == {70}
        assert {post["multiSourceBoost"] for post in posts} == {10}
        assert db.projects.find_one({"url": url})["momentumScore"] == 70
    finally:
        posts = list(db.posts.find({"project.url": url}, {"_id": 1}))
        post_ids = [str(post["_id"]) for post in posts]
        db.posts.delete_many({"project.url": url})
        db.projects.delete_many({"url": url})
        db.signals.delete_many({"projectId": url})
        db.signal_receipts.delete_many({"_id": {"$in": post_ids}})
        db.embeddings_audit.delete_many({"projectId": url})


@pytest.mark.asyncio
async def test_multi_source_reconciliation_preserves_the_human_rank_bonus(
    db, monkeypatch
):
    from _shared import embeddings, episodic_memory, port_client
    from _shared import write_post as write_post_module

    unique = datetime.now(timezone.utc).timestamp()
    url = f"https://example.com/human-rank-preservation-{unique}"
    project = {
        "url": url,
        "title": "Human rank preservation",
        "kind": "site",
        "description": "Independent sources should not erase human interest.",
        "topics": ["ranking"],
        "momentumScore": 60,
        "hypeVerdict": "emerging",
    }

    async def no_prior_episodes(*_args, **_kwargs):
        return []

    monkeypatch.setattr(embeddings, "embed_project", lambda *_args: [0.1] * 384)
    monkeypatch.setattr(episodic_memory, "retrieve_similar_episodes", no_prior_episodes)
    monkeypatch.setattr(port_client, "upsert_agent", lambda *_args: {"ok": True})
    monkeypatch.setattr(port_client, "upsert_project", lambda *_args: {"ok": True})
    monkeypatch.setattr(port_client, "upsert_post", lambda *_args: {"ok": True})

    try:
        first_post_id = await write_post_module.write_post(
            "@human-rank-one",
            "Human Rank One",
            "First source",
            "test",
            project,
            "First synchronized observation.",
            "emerging",
            {"source": "one", "metric": "mentions", "value": 1, "delta": 0},
            60,
        )
        db.posts.update_one(
            {"_id": ObjectId(first_post_id)},
            {"$set": {"rankScore": 64, "humanRankBonus": 4}},
        )

        await write_post_module.write_post(
            "@human-rank-two",
            "Human Rank Two",
            "Second source",
            "test",
            project,
            "Second synchronized observation.",
            "emerging",
            {"source": "two", "metric": "mentions", "value": 1, "delta": 0},
            60,
        )

        first = db.posts.find_one({"_id": ObjectId(first_post_id)})
        assert first["multiSourceBoost"] == 10
        assert first["humanRankBonus"] == 4
        assert first["rankScore"] == 74
    finally:
        posts = list(db.posts.find({"project.url": url}, {"_id": 1}))
        post_ids = [str(post["_id"]) for post in posts]
        db.posts.delete_many({"project.url": url})
        db.projects.delete_many({"url": url})
        db.signals.delete_many({"projectId": url})
        db.signal_receipts.delete_many({"_id": {"$in": post_ids}})
        db.embeddings_audit.delete_many({"projectId": url})


@pytest.mark.asyncio
async def test_crash_before_reconciliation_keeps_the_post_private_and_repairable(
    db, monkeypatch
):
    from _shared import port_client
    from _shared import write_post as write_post_module

    url = (
        f"https://example.com/reconcile-crash-{datetime.now(timezone.utc).timestamp()}"
    )
    post_id = db.posts.insert_one(
        {
            "agentHandle": "@reconcile-crash",
            "body": "Durable base evidence.",
            "verdict": "emerging",
            "rankScore": 62,
            "baseRankScore": 62,
            "postedAt": datetime.now(timezone.utc),
            "reactionCounts": {"likes": 0, "comments": 0, "shares": 0},
            "project": {
                "url": url,
                "title": "Reconciliation crash proof",
                "kind": "site",
                "description": "Must remain private until derived scores converge.",
                "topics": ["recovery"],
                "momentumScore": 62,
                "baseMomentumScore": 62,
                "hypeVerdict": "emerging",
            },
            "signalsSummary": "mentions=1",
            "portSyncStatus": "pending",
            "evidenceContractVersion": 2,
        }
    ).inserted_id

    monkeypatch.setattr(port_client, "upsert_agent", lambda *_args: {"ok": True})
    monkeypatch.setattr(port_client, "upsert_project", lambda *_args: {"ok": True})
    monkeypatch.setattr(port_client, "upsert_post", lambda *_args: {"ok": True})

    async def crash(_project_url, **_kwargs):
        raise RuntimeError("crash before reconciliation")

    monkeypatch.setattr(write_post_module, "_reconcile_multi_source", crash)

    try:
        with pytest.raises(RuntimeError, match="crash before reconciliation"):
            await write_post_module._sync_port_twin(
                str(post_id),
                "@reconcile-crash",
                "Reconcile Crash",
                "Recovery test",
                "test",
                {
                    "url": url,
                    "title": "Reconciliation crash proof",
                    "kind": "site",
                    "description": "Must remain private until derived scores converge.",
                    "topics": ["recovery"],
                    "momentumScore": 62,
                    "baseMomentumScore": 62,
                    "hypeVerdict": "emerging",
                },
                [0.1] * 384,
            )

        stored = db.posts.find_one({"_id": post_id})
        assert stored["portSyncStatus"] == "pending"
        assert db.projects.find_one({"url": url}) is None
    finally:
        db.posts.delete_one({"_id": post_id})
        db.projects.delete_many({"url": url})
        db.embeddings_audit.delete_many({"postId": str(post_id)})


@pytest.mark.asyncio
async def test_single_source_base_twin_is_published_by_reconciliation(db, monkeypatch):
    from _shared import port_client
    from _shared import write_post as write_post_module

    url = f"https://example.com/reconcile-base-{datetime.now(timezone.utc).timestamp()}"
    post_id = db.posts.insert_one(
        {
            "agentHandle": "@reconcile-base",
            "body": "One source is still publishable.",
            "verdict": "emerging",
            "rankScore": 58,
            "baseRankScore": 58,
            "postedAt": datetime.now(timezone.utc),
            "reactionCounts": {"likes": 0, "comments": 0, "shares": 0},
            "project": {
                "url": url,
                "title": "Single source reconciliation",
                "kind": "site",
                "description": "A single source needs a completed publication state.",
                "topics": ["reconciliation"],
                "momentumScore": 58,
                "baseMomentumScore": 58,
                "hypeVerdict": "emerging",
            },
            "signalsSummary": "mentions=1",
            "portSyncStatus": "pending",
            "evidenceContractVersion": 2,
        }
    ).inserted_id

    monkeypatch.setattr(port_client, "upsert_project", lambda *_args: {"ok": True})
    monkeypatch.setattr(port_client, "upsert_post", lambda *_args: {"ok": True})

    try:
        await write_post_module._reconcile_multi_source(
            url,
            current_post_id=str(post_id),
            project_snapshot={
                "url": url,
                "title": "Single source reconciliation",
                "kind": "site",
                "description": "A single source needs a completed publication state.",
                "topics": ["reconciliation"],
                "momentumScore": 58,
                "hypeVerdict": "emerging",
            },
            embedding=[0.1] * 384,
        )

        stored = db.posts.find_one({"_id": post_id})
        assert stored["portSyncStatus"] == "synced"
        assert stored["multiSourceSyncStatus"] == "synced"
        assert stored["multiSourceBoost"] == 0
        assert db.projects.find_one({"url": url})["momentumScore"] == 58
    finally:
        db.posts.delete_one({"_id": post_id})
        db.projects.delete_many({"url": url})
        db.project_reconcile_leases.delete_many({"_id": url})


@pytest.mark.asyncio
async def test_lost_reconciliation_lease_never_publishes_a_gated_post(db, monkeypatch):
    from _shared import port_client
    from _shared import write_post as write_post_module

    url = (
        f"https://example.com/reconcile-fence-{datetime.now(timezone.utc).timestamp()}"
    )
    post_id = db.posts.insert_one(
        {
            "agentHandle": "@reconcile-fence",
            "body": "Port acceptance is not publication authority.",
            "verdict": "emerging",
            "rankScore": 55,
            "baseRankScore": 55,
            "postedAt": datetime.now(timezone.utc),
            "reactionCounts": {"likes": 0, "comments": 0, "shares": 0},
            "project": {
                "url": url,
                "title": "Reconciliation fence proof",
                "kind": "site",
                "description": "A stale worker must fail closed.",
                "topics": ["reconciliation"],
                "momentumScore": 55,
                "baseMomentumScore": 55,
                "hypeVerdict": "emerging",
            },
            "portSyncStatus": "pending",
            "evidenceContractVersion": 2,
        }
    ).inserted_id

    monkeypatch.setattr(port_client, "upsert_project", lambda *_args: {"ok": True})

    def accept_after_losing_lease(*_args):
        db.project_reconcile_leases.delete_one({"_id": url})
        return {"ok": True}

    monkeypatch.setattr(port_client, "upsert_post", accept_after_losing_lease)

    try:
        with pytest.raises(RuntimeError, match="lease lost"):
            await write_post_module._reconcile_multi_source(
                url,
                current_post_id=str(post_id),
                project_snapshot={
                    "url": url,
                    "title": "Reconciliation fence proof",
                    "kind": "site",
                    "description": "A stale worker must fail closed.",
                    "topics": ["reconciliation"],
                    "momentumScore": 55,
                    "hypeVerdict": "emerging",
                },
                embedding=[0.1] * 384,
            )

        stored = db.posts.find_one({"_id": post_id})
        assert stored["portSyncStatus"] == "pending"
        assert stored["multiSourceSyncStatus"] == "pending"
        assert db.projects.find_one({"url": url}) is None
    finally:
        db.posts.delete_one({"_id": post_id})
        db.projects.delete_many({"url": url})
        db.project_reconcile_leases.delete_many({"_id": url})


@pytest.mark.asyncio
async def test_stale_reconciliation_owner_cannot_gate_a_public_post(db, monkeypatch):
    from _shared import port_client
    from _shared import write_post as write_post_module

    url = f"https://example.com/stale-gate-{datetime.now(timezone.utc).timestamp()}"
    post_id = db.posts.insert_one(
        {
            "agentHandle": "@stale-gate",
            "body": "A stale worker must not hide a synchronized claim.",
            "verdict": "emerging",
            "rankScore": 51,
            "baseRankScore": 51,
            "postedAt": datetime.now(timezone.utc),
            "reactionCounts": {"likes": 0, "comments": 0, "shares": 0},
            "project": {
                "url": url,
                "title": "Stale gate fence",
                "kind": "site",
                "description": "Lease ownership fences the visibility gate.",
                "topics": ["reconciliation"],
                "momentumScore": 51,
                "baseMomentumScore": 51,
                "hypeVerdict": "emerging",
            },
            "signalsSummary": "mentions=1",
            "portSyncStatus": "synced",
            "evidenceContractVersion": 2,
        }
    ).inserted_id
    port_calls = []
    monkeypatch.setattr(
        port_client,
        "upsert_project",
        lambda *_args: port_calls.append("project") or {"ok": True},
    )
    monkeypatch.setattr(
        port_client,
        "upsert_post",
        lambda *_args: port_calls.append("post") or {"ok": True},
    )

    try:
        with pytest.raises(RuntimeError, match="lease lost"):
            await write_post_module._reconcile_multi_source_locked(
                url,
                "stale-owner",
                project_snapshot={
                    "url": url,
                    "title": "Stale gate fence",
                    "kind": "site",
                    "description": "Lease ownership fences the visibility gate.",
                    "topics": ["reconciliation"],
                    "momentumScore": 51,
                    "hypeVerdict": "emerging",
                },
                embedding=[0.1] * 384,
            )

        stored = db.posts.find_one({"_id": post_id})
        assert stored["portSyncStatus"] == "synced"
        assert "multiSourceSyncStatus" not in stored
        assert port_calls == []
    finally:
        db.posts.delete_one({"_id": post_id})
        db.projects.delete_many({"url": url})
        db.project_reconcile_leases.delete_many({"_id": url})


@pytest.mark.asyncio
async def test_quarantined_publication_cannot_be_repaired_or_republished(monkeypatch):
    from _shared import write_post as write_post_module

    existing = {
        "_id": "quarantined-post",
        "agentHandle": "@quarantined",
        "body": "Unsupported legacy copy",
        "portSyncStatus": "quarantined",
        "project": {"url": "https://example.com/quarantined"},
    }
    sync_calls = []

    async def capture_sync(*_args, **_kwargs):
        sync_calls.append(True)

    monkeypatch.setattr(write_post_module, "_sync_port_twin", capture_sync)

    with pytest.raises(RuntimeError, match="quarantined"):
        await write_post_module._repair_existing_post(
            existing,
            "@quarantined",
            "Quarantined",
            "Unsafe legacy evidence",
            "test",
            {
                "url": "https://example.com/quarantined",
                "title": "Fresh candidate",
                "kind": "site",
                "description": "Fresh evidence",
                "topics": [],
                "momentumScore": 50,
                "hypeVerdict": "emerging",
            },
            "Fresh body",
            "emerging",
            50,
        )

    assert sync_calls == []


@pytest.mark.asyncio
async def test_project_reconciliation_lease_serializes_workers(db):
    from _shared import write_post as write_post_module

    project_url = (
        f"https://example.com/reconcile-lease-{datetime.now(timezone.utc).timestamp()}"
    )
    first_owner = await write_post_module._acquire_project_reconcile_lease(project_url)
    second = asyncio.create_task(
        write_post_module._acquire_project_reconcile_lease(project_url)
    )
    try:
        await asyncio.sleep(0.2)
        assert not second.done()
        await write_post_module._release_project_reconcile_lease(
            project_url, first_owner
        )
        second_owner = await asyncio.wait_for(second, timeout=2)
        assert second_owner != first_owner
        await write_post_module._release_project_reconcile_lease(
            project_url, second_owner
        )
    finally:
        if not second.done():
            second.cancel()
        db.project_reconcile_leases.delete_many({"_id": project_url})


@pytest.mark.asyncio
async def test_partial_multi_source_sync_hides_every_affected_post_until_retry(
    db, monkeypatch
):
    from _shared import embeddings, episodic_memory, port_client
    from _shared import write_post as write_post_module

    unique = datetime.now(timezone.utc).timestamp()
    url = f"https://example.com/multi-source-retry-{unique}"
    project = {
        "url": url,
        "title": "Multi-source retry",
        "kind": "site",
        "description": "Derived scores fail closed.",
        "topics": ["recovery"],
        "momentumScore": 60,
        "hypeVerdict": "emerging",
    }

    async def no_prior_episodes(*_args, **_kwargs):
        return []

    post_calls = 0

    def fail_during_reconciliation(*_args):
        nonlocal post_calls
        post_calls += 1
        if post_calls == 3:
            return {"ok": False, "message": "derived Port split"}
        return {"ok": True}

    monkeypatch.setattr(embeddings, "embed_project", lambda *_args: [0.1] * 384)
    monkeypatch.setattr(episodic_memory, "retrieve_similar_episodes", no_prior_episodes)
    monkeypatch.setattr(port_client, "upsert_agent", lambda *_args: {"ok": True})
    monkeypatch.setattr(port_client, "upsert_project", lambda *_args: {"ok": True})
    monkeypatch.setattr(port_client, "upsert_post", fail_during_reconciliation)

    try:
        await write_post_module.write_post(
            "@multi-one",
            "Multi One",
            "First source",
            "test",
            project,
            "First observation.",
            "emerging",
            {"source": "one", "metric": "mentions", "value": 1, "delta": 0},
            60,
        )
        with pytest.raises(RuntimeError, match="derived Port split"):
            await write_post_module.write_post(
                "@multi-two",
                "Multi Two",
                "Second source",
                "test",
                project,
                "Second observation.",
                "emerging",
                {"source": "two", "metric": "mentions", "value": 1, "delta": 0},
                60,
            )

        assert (
            db.posts.count_documents({"project.url": url, "portSyncStatus": "synced"})
            == 0
        )

        monkeypatch.setattr(port_client, "upsert_post", lambda *_args: {"ok": True})
        await write_post_module.repair_pending_posts(
            "@multi-one", "Multi One", "First source", "test"
        )
        await write_post_module.repair_pending_posts(
            "@multi-two", "Multi Two", "Second source", "test"
        )

        posts = list(db.posts.find({"project.url": url}))
        assert len(posts) == 2
        assert {post["portSyncStatus"] for post in posts} == {"synced"}
        assert {post["rankScore"] for post in posts} == {70}
    finally:
        posts = list(db.posts.find({"project.url": url}, {"_id": 1}))
        post_ids = [str(post["_id"]) for post in posts]
        db.posts.delete_many({"project.url": url})
        db.projects.delete_many({"url": url})
        db.signals.delete_many({"projectId": url})
        db.signal_receipts.delete_many({"_id": {"$in": post_ids}})
        db.embeddings_audit.delete_many({"projectId": url})


@pytest.mark.asyncio
async def test_embedding_audit_rejects_a_conflicting_retry(db):
    from _shared import write_post as write_post_module

    post_id = f"conflicting-audit-{datetime.now(timezone.utc).timestamp()}"
    db.embeddings_audit.insert_one(
        {
            "postId": post_id,
            "projectId": "https://example.com/original",
            "agentHandle": "@original",
            "dims": 384,
            "model": "all-MiniLM-L6-v2",
        }
    )
    try:
        with pytest.raises(RuntimeError, match="audit conflict"):
            await write_post_module._ensure_embedding_audit(
                post_id,
                "https://example.com/replacement",
                "@replacement",
                [0.1] * 384,
            )
    finally:
        db.embeddings_audit.delete_many({"postId": post_id})


def test_port_post_normalizes_legacy_naive_utc_timestamp(monkeypatch):
    from _shared import port_client

    captured = {}

    def capture_upsert(_blueprint, _identifier, payload):
        captured.update(payload)
        return {"ok": True}

    monkeypatch.setattr(port_client, "_upsert", capture_upsert)

    port_client.upsert_post(
        "post-1",
        "@test-agent",
        "https://example.com/project",
        "Evidence",
        "emerging",
        64,
        datetime(2026, 7, 11, 21, 58, 8),
    )

    assert captured["properties"]["postedAt"] == "2026-07-11T21:58:08+00:00"


@pytest.mark.asyncio
async def test_mongo_reuses_one_async_client_within_an_event_loop():
    from _shared import mongo

    first = mongo._get_db().client
    second = mongo._get_db().client
    try:
        assert first is second
    finally:
        await mongo.close_client()


@pytest.mark.asyncio
async def test_run_summary_requires_current_run_sync_and_no_historical_pending(db):
    from _shared.runner import summarize_run

    unique = datetime.now(timezone.utc).timestamp()
    agent_handle = f"@run-proof-{unique}"
    current_run = f"run:{unique}"
    prior_run = f"prior:{unique}"
    start_of_day = datetime.now(timezone.utc).replace(
        hour=0, minute=0, second=0, microsecond=0
    )
    synced_id = db.posts.insert_one(
        {
            "agentHandle": agent_handle,
            "postedAt": datetime.now(timezone.utc),
            "runId": prior_run,
            "portSyncedByRunId": prior_run,
            "portSyncStatus": "synced",
        }
    ).inserted_id
    try:
        no_op = await summarize_run(agent_handle, current_run, start_of_day)
        assert no_op["posts_today"] == 1
        assert no_op["synced_this_run"] == 0
        assert no_op["ok"] is False

        db.posts.update_one(
            {"_id": synced_id}, {"$set": {"portSyncedByRunId": current_run}}
        )
        converged = await summarize_run(agent_handle, current_run, start_of_day)
        assert converged["ok"] is True

        db.posts.insert_one(
            {
                "agentHandle": agent_handle,
                "postedAt": datetime.now(timezone.utc) - timedelta(days=2),
                "portSyncStatus": "pending",
            }
        )
        blocked = await summarize_run(agent_handle, current_run, start_of_day)
        assert blocked["pending_port_syncs"] == 1
        assert blocked["ok"] is False
    finally:
        db.posts.delete_many({"agentHandle": agent_handle})


@pytest.mark.asyncio
async def test_runner_repairs_historical_pending_posts_before_source_scan(
    db, monkeypatch
):
    from _shared import port_client
    from _shared import runner as runner_module

    unique = datetime.now(timezone.utc).timestamp()
    agent_handle = f"@backlog-repair-{unique}"
    url = f"https://example.com/backlog-repair-{unique}"
    project_id = db.projects.insert_one(
        {
            "url": url,
            "title": "Historical retry proof",
            "kind": "site",
            "description": "A stored project whose source will not emit again",
            "topics": ["port", "recovery"],
            "momentumScore": 67,
            "hypeVerdict": "emerging",
            "embedding": [0.1] * 384,
            "firstSeenAt": datetime.now(timezone.utc) - timedelta(days=3),
            "lastSeenAt": datetime.now(timezone.utc) - timedelta(days=3),
        }
    ).inserted_id
    post_id = db.posts.insert_one(
        {
            "agentHandle": agent_handle,
            "body": "Stored evidence should converge without another candidate.",
            "verdict": "emerging",
            "rankScore": 67,
            "project": {
                "url": url,
                "title": "Historical retry proof",
                "kind": "site",
                "description": "A stored project whose source will not emit again",
                "topics": ["port", "recovery"],
                "momentumScore": 67,
                "baseMomentumScore": 67,
                "hypeVerdict": "emerging",
            },
            "postedAt": datetime.now(timezone.utc) - timedelta(days=3),
            "reactionCounts": {"likes": 2, "comments": 1, "shares": 1},
            "portSyncStatus": "pending",
            "evidenceContractVersion": 2,
        }
    ).inserted_id

    class FakeCheckpointer:
        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return None

    class QuietAgent:
        async def ainvoke(self, *_args, **_kwargs):
            return None

    monkeypatch.setattr(
        runner_module.MongoDBSaver,
        "from_conn_string",
        lambda *_args, **_kwargs: FakeCheckpointer(),
    )
    monkeypatch.setattr(port_client, "upsert_agent", lambda *_args: {"ok": True})
    port_projects = []

    def capture_project(*args):
        port_projects.append(args)
        return {"ok": True}

    monkeypatch.setattr(port_client, "upsert_project", capture_project)
    monkeypatch.setattr(port_client, "upsert_post", lambda *_args: {"ok": True})
    monkeypatch.setattr(
        port_client, "record_agent_success", lambda *_args: {"ok": True}
    )

    try:
        summary = await runner_module._run_agent_cycle(
            agent_handle,
            "Backlog Repair",
            "Repairs stored twins",
            "test",
            lambda **_kwargs: QuietAgent(),
        )

        repaired = db.posts.find_one({"_id": post_id})
        assert summary["ok"] is True
        assert summary["synced_this_run"] == 1
        assert summary["pending_port_syncs"] == 0
        assert repaired["portSyncStatus"] == "synced"
        assert repaired["portSyncedByRunId"] == summary["thread_id"]
        assert db.posts.count_documents({"project.url": url}) == 1
        assert db.embeddings_audit.count_documents({"postId": str(post_id)}) == 1
        canonical = db.projects.find_one({"_id": project_id})
        assert canonical["title"] == "Historical retry proof"
        assert canonical["momentumScore"] == 67
        assert port_projects[0][1] == "Historical retry proof"
        assert port_projects[0][5] == 67
    finally:
        db.posts.delete_many({"project.url": url})
        db.projects.delete_one({"_id": project_id})
        db.signal_receipts.delete_many({"signal.projectId": url})
        db.embeddings_audit.delete_many({"projectId": url})


@pytest.mark.asyncio
async def test_runner_stops_an_agent_invocation_that_exceeds_its_deadline(monkeypatch):
    from _shared import port_client
    from _shared import runner as runner_module

    class FakeCheckpointer:
        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return None

    class HangingAgent:
        async def ainvoke(self, *_args, **_kwargs):
            await asyncio.Event().wait()

    monkeypatch.setenv("MONGODB_URI", "mongodb://example.invalid/hyperadar")
    monkeypatch.setattr(
        runner_module, "AGENT_INVOCATION_TIMEOUT_SECONDS", 0.01, raising=False
    )
    monkeypatch.setattr(
        runner_module.MongoDBSaver,
        "from_conn_string",
        lambda *_args, **_kwargs: FakeCheckpointer(),
    )
    monkeypatch.setattr(port_client, "upsert_agent", lambda *_args: {"ok": True})
    monkeypatch.setattr(
        runner_module.write_post,
        "repair_pending_posts",
        AsyncMock(return_value=0),
    )

    with pytest.raises(RuntimeError, match="timed out"):
        await asyncio.wait_for(
            runner_module._run_agent_cycle(
                "@timeout-agent",
                "Timeout Agent",
                "Timeout proof",
                "test",
                lambda **_kwargs: HangingAgent(),
            ),
            timeout=0.2,
        )


@pytest.mark.asyncio
async def test_backlog_repair_preserves_a_v2_private_project_snapshot(db, monkeypatch):
    from _shared import embeddings, port_client
    from _shared import write_post as write_post_module

    unique = datetime.now(timezone.utc).timestamp()
    url = f"https://example.com/v2-backlog-{unique}"
    db.projects.insert_one(
        {
            "url": url,
            "title": "Old public title",
            "kind": "site",
            "description": "Old public description",
            "topics": ["old"],
            "momentumScore": 31,
            "hypeVerdict": "cooling",
            "embedding": [0.2] * 384,
        }
    )
    pending_id = db.posts.insert_one(
        {
            "agentHandle": "@v2-backlog",
            "body": "Fresh private evidence.",
            "verdict": "emerging",
            "rankScore": 88,
            "baseRankScore": 88,
            "project": {
                "url": url,
                "title": "Fresh private title",
                "kind": "site",
                "description": "Fresh private description",
                "topics": ["fresh"],
                "momentumScore": 88,
                "baseMomentumScore": 88,
                "hypeVerdict": "emerging",
            },
            "signal": {
                "projectId": url,
                "source": "test",
                "metric": "mentions",
                "value": 8,
                "delta": 1,
            },
            "postedAt": datetime.now(timezone.utc),
            "reactionCounts": {"likes": 0, "comments": 0, "shares": 0},
            "portSyncStatus": "pending",
            "evidenceContractVersion": 2,
        }
    ).inserted_id
    synced_projects = []

    monkeypatch.setattr(embeddings, "embed_project", lambda *_args: [0.8] * 384)
    monkeypatch.setattr(port_client, "upsert_agent", lambda *_args: {"ok": True})

    def capture_project(*args):
        synced_projects.append(args)
        return {"ok": True}

    monkeypatch.setattr(port_client, "upsert_project", capture_project)
    monkeypatch.setattr(port_client, "upsert_post", lambda *_args: {"ok": True})

    try:
        repaired = await write_post_module.repair_pending_posts(
            "@v2-backlog", "V2 Backlog", "Retry proof", "test"
        )

        assert repaired == 1
        assert synced_projects[0][1] == "Fresh private title"
        assert synced_projects[0][5] == 88
        canonical = db.projects.find_one({"url": url})
        assert canonical["title"] == "Fresh private title"
        assert canonical["description"] == "Fresh private description"
        assert db.posts.find_one({"_id": pending_id})["portSyncStatus"] == "synced"
    finally:
        db.posts.delete_many({"project.url": url})
        db.projects.delete_many({"url": url})
        db.signals.delete_many({"projectId": url})
        db.signal_receipts.delete_many({"signal.projectId": url})
        db.embeddings_audit.delete_many({"projectId": url})


@pytest.mark.asyncio
async def test_failed_port_sync_cannot_change_the_published_project_snapshot(
    db, monkeypatch
):
    from _shared import embeddings, episodic_memory, port_client
    from _shared import write_post as write_post_module

    unique = datetime.now(timezone.utc).timestamp()
    url = f"https://example.com/publication-gate-{unique}"
    old_title = "Last verified title"
    old_score = 31
    project_id = db.projects.insert_one(
        {
            "url": url,
            "slug": f"publication-gate-{unique}",
            "title": old_title,
            "kind": "site",
            "description": "The last fully synchronized snapshot",
            "topics": ["verified"],
            "momentumScore": old_score,
            "hypeVerdict": "cooling",
            "embedding": [0.2] * 384,
            "firstSeenAt": datetime.now(timezone.utc) - timedelta(days=2),
            "lastSeenAt": datetime.now(timezone.utc) - timedelta(days=2),
        }
    ).inserted_id
    db.posts.insert_one(
        {
            "agentHandle": "@already-published",
            "body": "This is the last public evidence.",
            "verdict": "cooling",
            "rankScore": old_score,
            "project": {
                "url": url,
                "title": old_title,
                "kind": "site",
                "description": "The last fully synchronized snapshot",
                "topics": ["verified"],
                "momentumScore": old_score,
            },
            "postedAt": datetime.now(timezone.utc) - timedelta(days=1),
            "reactionCounts": {"likes": 0, "comments": 0, "shares": 0},
            "portSyncStatus": "synced",
            "evidenceContractVersion": 2,
        }
    ).inserted_id

    async def no_prior_episodes(*_args, **_kwargs):
        return []

    def project_embedding(title, *_args):
        value = 0.8 if title == "Unverified replacement title" else 0.9
        return [value] * 384

    monkeypatch.setattr(embeddings, "embed_project", project_embedding)
    monkeypatch.setattr(episodic_memory, "retrieve_similar_episodes", no_prior_episodes)
    monkeypatch.setattr(port_client, "upsert_agent", lambda *_args: {"ok": True})
    monkeypatch.setattr(
        port_client,
        "upsert_project",
        lambda *_args: {"ok": False, "message": "simulated Port outage"},
    )
    monkeypatch.setattr(port_client, "upsert_post", lambda *_args: {"ok": True})

    project = {
        "url": url,
        "title": "Unverified replacement title",
        "kind": "site",
        "description": "This snapshot must remain private until convergence",
        "topics": ["pending"],
        "momentumScore": 88,
        "hypeVerdict": "hype looks real",
    }
    signal = {
        "source": "test",
        "metric": "mentions",
        "value": 99,
        "delta": 68,
    }

    try:
        with pytest.raises(RuntimeError, match="simulated Port outage"):
            await write_post_module.write_post(
                "@publication-gate",
                "Publication Gate",
                "Tests fail-closed publication",
                "test",
                project,
                "This claim is not public yet.",
                "hype looks real",
                signal,
                88,
            )

        pending = db.posts.find_one(
            {"agentHandle": "@publication-gate", "project.url": url}
        )
        assert pending["portSyncStatus"] == "pending"
        assert pending["project"]["description"] == project["description"]
        assert pending["project"]["topics"] == project["topics"]

        published_project = db.projects.find_one({"_id": project_id})
        assert published_project["title"] == old_title
        assert published_project["momentumScore"] == old_score
        assert (
            published_project["description"] == "The last fully synchronized snapshot"
        )

        stored_signal = db.signals.find_one(
            {"projectId": url, "value": signal["value"]}
        )
        assert stored_signal["postId"] == str(pending["_id"])

        synced_projects = []

        def sync_project(*args):
            synced_projects.append(args)
            return {"ok": True}

        monkeypatch.setattr(port_client, "upsert_project", sync_project)
        later_scan = {
            **project,
            "title": "A later scan must not replace the claimed snapshot",
            "description": "Different input after response loss",
            "momentumScore": 99,
        }
        repaired_id = await write_post_module.write_post(
            "@publication-gate",
            "Publication Gate",
            "Tests fail-closed publication",
            "test",
            later_scan,
            "Different copy after response loss.",
            "emerging",
            {**signal, "value": 100},
            99,
        )

        repaired = db.posts.find_one({"_id": pending["_id"]})
        canonical = db.projects.find_one({"_id": project_id})
        assert repaired_id == str(pending["_id"])
        assert synced_projects[0][1] == "Unverified replacement title"
        assert canonical["title"] == "Unverified replacement title"
        assert canonical["description"] == project["description"]
        assert canonical["momentumScore"] == 98
        assert repaired["project"]["baseMomentumScore"] == 88
        assert canonical["embedding"][0] == 0.8
        assert repaired["verdict"] == "hype looks real"
    finally:
        db.posts.delete_many({"project.url": url})
        db.projects.delete_one({"_id": project_id})
        db.signals.delete_many({"projectId": url})
        db.signal_receipts.delete_many({"signal.projectId": url})
        db.embeddings_audit.delete_many({"projectId": url})


@pytest.mark.asyncio
async def test_partial_port_failure_heals_on_retry_without_duplicate_posts(
    db, monkeypatch
):
    from _shared import embeddings, episodic_memory, port_client
    from _shared import write_post as write_post_module

    unique = datetime.now(timezone.utc).timestamp()
    url = f"https://example.com/port-retry-{unique}"
    project = {
        "url": url,
        "title": "Port retry proof",
        "kind": "site",
        "description": "A deterministic partial-sync test",
        "topics": ["port", "recovery"],
        "momentumScore": 64,
        "hypeVerdict": "emerging",
    }
    signal = {
        "source": "test",
        "metric": "mentions",
        "value": 1,
        "delta": 1,
    }
    project_attempts = 0
    post_timestamps = []
    post_reaction_counts = []

    async def no_prior_episodes(*_args, **_kwargs):
        return []

    def sync_project(*_args, **_kwargs):
        nonlocal project_attempts
        project_attempts += 1
        if project_attempts == 1:
            return {"ok": False, "message": "simulated Port outage"}
        return {"ok": True}

    def sync_post(*args, **_kwargs):
        post_timestamps.append(args[6])
        post_reaction_counts.append(args[7:10])
        return {"ok": True}

    monkeypatch.setattr(embeddings, "embed_project", lambda *_args: [0.1] * 384)
    monkeypatch.setattr(episodic_memory, "retrieve_similar_episodes", no_prior_episodes)
    monkeypatch.setattr(port_client, "upsert_agent", lambda *_args: {"ok": True})
    monkeypatch.setattr(port_client, "upsert_project", sync_project)
    monkeypatch.setattr(port_client, "upsert_post", sync_post)

    try:
        with pytest.raises(RuntimeError, match="simulated Port outage"):
            await write_post_module.write_post(
                "@test-agent",
                "Test Agent",
                "Tests recovery",
                "test",
                project,
                "A recoverable signal.",
                "emerging",
                signal,
                64,
            )

        pending = db.posts.find_one({"project.url": url})
        assert pending["portSyncStatus"] == "pending"
        assert db.embeddings_audit.count_documents({"projectId": url}) == 1
        legacy_posted_at = datetime.now(timezone.utc) - timedelta(days=1)
        db.posts.update_one(
            {"_id": pending["_id"]},
            {
                "$set": {
                    "postedAt": legacy_posted_at,
                    "reactionCounts": {"likes": 3, "comments": 2, "shares": 1},
                }
            },
        )

        repaired_id = await write_post_module.write_post(
            "@test-agent",
            "Test Agent",
            "Tests recovery",
            "test",
            project,
            "A recoverable signal.",
            "emerging",
            signal,
            64,
        )

        assert db.posts.count_documents({"project.url": url}) == 1
        repaired = db.posts.find_one({"project.url": url})
        assert repaired_id == str(repaired["_id"])
        assert repaired["portSyncStatus"] == "synced"
        assert project_attempts == 2
        # BSON persists datetimes at millisecond precision and this sync client
        # reads UTC values back as naive datetimes. The Port twin must receive
        # the exact value stored in MongoDB, not a new retry timestamp.
        assert post_timestamps == [repaired["postedAt"]]
        assert post_reaction_counts == [(3, 2, 1)]
        assert db.signals.count_documents({"projectId": url}) == 1
        assert db.embeddings_audit.count_documents({"projectId": url}) == 1
    finally:
        db.posts.delete_many({"project.url": url})
        db.projects.delete_many({"url": url})
        db.signals.delete_many({"projectId": url})
        db.signal_receipts.delete_many({"signal.projectId": url})
        db.embeddings_audit.delete_many({"projectId": url})


@pytest.mark.asyncio
async def test_audit_failure_keeps_post_private_until_retry(db, monkeypatch):
    from _shared import embeddings, episodic_memory, port_client
    from _shared import write_post as write_post_module

    url = f"https://example.com/audit-retry-{datetime.now(timezone.utc).timestamp()}"
    audit_attempts = 0
    original_ensure_audit = write_post_module._ensure_embedding_audit

    async def no_prior_episodes(*_args, **_kwargs):
        return []

    async def flaky_audit(*args, **kwargs):
        nonlocal audit_attempts
        audit_attempts += 1
        if audit_attempts == 1:
            raise RuntimeError("simulated audit outage")
        await original_ensure_audit(*args, **kwargs)

    monkeypatch.setattr(embeddings, "embed_project", lambda *_args: [0.1] * 384)
    monkeypatch.setattr(episodic_memory, "retrieve_similar_episodes", no_prior_episodes)
    monkeypatch.setattr(port_client, "upsert_agent", lambda *_args: {"ok": True})
    monkeypatch.setattr(port_client, "upsert_project", lambda *_args: {"ok": True})
    monkeypatch.setattr(port_client, "upsert_post", lambda *_args: {"ok": True})
    monkeypatch.setattr(write_post_module, "_ensure_embedding_audit", flaky_audit)

    project = {
        "url": url,
        "title": "Audit retry proof",
        "kind": "site",
        "description": "A deterministic audit failure test",
        "topics": ["audit", "recovery"],
        "momentumScore": 60,
        "hypeVerdict": "emerging",
    }
    signal = {"source": "test", "metric": "mentions", "value": 1, "delta": 1}

    try:
        with pytest.raises(RuntimeError, match="simulated audit outage"):
            await write_post_module.write_post(
                "@test-agent",
                "Test Agent",
                "Tests audit recovery",
                "test",
                project,
                "Audit should gate publication.",
                "emerging",
                signal,
                60,
            )

        pending = db.posts.find_one({"project.url": url})
        assert pending["portSyncStatus"] == "pending"

        repaired_id = await write_post_module.write_post(
            "@test-agent",
            "Test Agent",
            "Tests audit recovery",
            "test",
            project,
            "Audit should gate publication.",
            "emerging",
            signal,
            60,
        )
        assert repaired_id == str(pending["_id"])
        repaired = db.posts.find_one({"_id": pending["_id"]})
        assert repaired["portSyncStatus"] == "synced"
        assert db.embeddings_audit.count_documents({"postId": repaired_id}) == 1
        assert db.posts.count_documents({"project.url": url}) == 1
        assert db.signals.count_documents({"projectId": url}) == 1
    finally:
        db.posts.delete_many({"project.url": url})
        db.projects.delete_many({"url": url})
        db.signals.delete_many({"projectId": url})
        db.signal_receipts.delete_many({"signal.projectId": url})
        db.embeddings_audit.delete_many({"projectId": url})
