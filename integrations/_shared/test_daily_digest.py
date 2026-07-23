"""Tests for the daily digest generator.

Seam: the filter construction, Grove LLM call, and MongoDB storage in
`integrations/_shared/daily_digest.py`.
"""

import json
import os
import sys
from datetime import datetime, timezone

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from _shared import daily_digest  # noqa: E402
from _shared.daily_digest import (  # noqa: E402
    _build_grove_prompt,
    _call_grove,
    _daily_post_filter,
    DAILY_DIGEST_AGENT_HANDLES,
)


# ---- Shared helpers -----------------------------------------------------------


def _make_post(
    i: int,
    agent: str = "@github-radar",
    rank_score: float = 70,
    momentum_score: float = 80,
    body: str = "blurb",
    verdict: str = "hype looks real",
    kind: str = "repo",
) -> dict:
    """Build a post document that matches the real write_post.py schema."""
    return {
        "_id": f"post{i}",
        "agentHandle": agent,
        "body": body,
        "verdict": verdict,
        "rankScore": rank_score + i,
        "project": {
            "title": f"repo-{i}",
            "url": f"https://example.com/repo-{i}",
            "kind": kind,
            "momentumScore": momentum_score,
        },
    }


class TestDailyPostFilter:
    """The MongoDB filter for daily digest source posts."""

    def test_excludes_community_radar(self):
        """@community-radar is an internal agent, not a daily digest source."""
        since = datetime(2026, 7, 23, tzinfo=timezone.utc)
        query = _daily_post_filter(since)
        assert "@community-radar" not in query["agentHandle"]["$in"]

    def test_excludes_weekly_digest(self):
        """@weekly-digest is an aggregator, not a daily digest source."""
        since = datetime(2026, 7, 23, tzinfo=timezone.utc)
        query = _daily_post_filter(since)
        assert "@weekly-digest" not in query["agentHandle"]["$in"]

    def test_includes_external_agents(self):
        """The four external agents are daily digest sources."""
        since = datetime(2026, 7, 23, tzinfo=timezone.utc)
        query = _daily_post_filter(since)
        handles = query["agentHandle"]["$in"]
        assert "@github-radar" in handles
        assert "@youtube-trends" in handles
        assert "@reddit-pulse" in handles
        assert "@hidden-gems" in handles

    def test_respects_24h_window(self):
        """Posts must be from the last 24 hours (postedAt >= since)."""
        since = datetime(2026, 7, 23, 0, 0, 0, tzinfo=timezone.utc)
        query = _daily_post_filter(since)
        assert query["postedAt"] == {"$gte": since}

    def test_only_synced_posts_with_evidence_contract_v2(self):
        """Same publication filter as PUBLIC_POST_FILTER."""
        since = datetime(2026, 7, 23, tzinfo=timezone.utc)
        query = _daily_post_filter(since)
        assert query["portSyncStatus"] == "synced"
        assert query["evidenceContractVersion"] == 2
        assert query["legacyDuplicateOf"] == {"$exists": False}


class TestGrovePrompt:
    """The Grove LLM prompt construction."""

    def test_prompt_instructs_top_5_and_diversity(self):
        posts = [_make_post(0, rank_score=80, momentum_score=85)]
        prompt = _build_grove_prompt(posts)
        assert "top 5" in prompt.lower() or "top five" in prompt.lower()
        assert "2 from the same" in prompt.lower() or "max 2" in prompt.lower()
        assert "JSON" in prompt or "json" in prompt

    def test_prompt_includes_post_data(self):
        posts = [
            _make_post(0, agent="@youtube-trends", rank_score=72, momentum_score=68)
        ]
        prompt = _build_grove_prompt(posts)
        assert "repo-0" in prompt
        assert "@youtube-trends" in prompt

    def test_prompt_uses_rank_score_not_momentum(self):
        """The prompt should include rankScore (top level) not search for it."""
        posts = [_make_post(0, rank_score=91, momentum_score=42)]
        prompt = _build_grove_prompt(posts)
        assert "rankScore" in prompt
        assert '"rankScore": 91' in prompt or '"rankScore":91' in prompt

    def test_prompt_reads_momentum_from_project(self):
        """momentumScore lives inside project, not at top level."""
        posts = [_make_post(0, rank_score=91, momentum_score=42)]
        prompt = _build_grove_prompt(posts)
        assert '"momentumScore": 42' in prompt or '"momentumScore":42' in prompt

    def test_prompt_includes_verdict(self):
        posts = [_make_post(0, verdict="emerging")]
        prompt = _build_grove_prompt(posts)
        assert "emerging" in prompt

    def test_prompt_does_not_include_stars_views_upvotes(self):
        """The prompt must not reference fields that don't exist on posts."""
        posts = [_make_post(0)]
        prompt = _build_grove_prompt(posts)
        assert "stars" not in prompt
        assert "views" not in prompt
        assert "upvotes" not in prompt


class TestCallGrove:
    """The Grove LLM call — mocked httpx, no network."""

    @staticmethod
    def _make_client(grove_response: str):
        class FakeResponse:
            def raise_for_status(self):
                pass

            def json(self):
                return {"choices": [{"message": {"content": grove_response}}]}

        class FakeAsyncClient:
            def __init__(self, *args, **kwargs):
                pass

            async def __aenter__(self):
                return self

            async def __aexit__(self, *args):
                return False

            async def post(self, url, **kwargs):
                assert "/chat/completions" in url
                headers = kwargs.get("headers", {})
                assert "api-key" in headers
                body = kwargs.get("json", {})
                assert "model" in body
                assert "messages" in body
                return FakeResponse()

        return FakeAsyncClient

    def _setup_env(self, monkeypatch):
        monkeypatch.setattr(
            daily_digest.httpx, "AsyncClient", self._make_client(self._response)
        )
        monkeypatch.setenv("GROVE_BASE_URL", "https://grove.example.com")
        monkeypatch.setenv("GROVE_API_KEY", "test-key")
        monkeypatch.setenv("GROVE_MODEL", "test-model")

    async def test_parses_valid_json_response(self, monkeypatch):
        """Grove returns a JSON array of {id, blurb} — parsed correctly."""
        self._response = json.dumps(
            [
                {"id": 0, "blurb": "Exploding GitHub stars signal real adoption."},
                {"id": 1, "blurb": "Viral video with 500k views on agent frameworks."},
            ]
        )
        self._setup_env(monkeypatch)

        posts = [_make_post(0, rank_score=80), _make_post(1, agent="@youtube-trends")]
        result = await _call_grove(posts)
        assert isinstance(result, list)
        assert len(result) == 2
        assert result[0]["id"] == 0
        assert "blurb" in result[0]

    async def test_handles_markdown_code_fence_response(self, monkeypatch):
        """Grove may wrap JSON in ```json fences — strip and parse."""
        self._response = '```json\n[{"id": 0, "blurb": "Hyped!"}]\n```'
        self._setup_env(monkeypatch)

        result = await _call_grove([_make_post(0, rank_score=80)])
        assert len(result) == 1
        assert result[0]["blurb"] == "Hyped!"

    async def test_invalid_json_returns_empty_list(self, monkeypatch):
        """Malformed JSON from Grove should return [], not crash."""
        self._response = "not valid json {{{"
        self._setup_env(monkeypatch)

        result = await _call_grove([_make_post(0)])
        assert result == []

    async def test_non_list_response_returns_empty_list(self, monkeypatch):
        """If Grove returns a dict (or other non-list), return []."""
        self._response = json.dumps({"id": 0, "blurb": "oops"})
        self._setup_env(monkeypatch)

        result = await _call_grove([_make_post(0)])
        assert result == []


# ---- generate_daily_digest ---------------------------------------------------


class _FakeCursor:
    def __init__(self, posts):
        self._posts = posts

    def sort(self, *args):
        return self

    def limit(self, *args):
        return self

    async def to_list(self, length):
        return list(self._posts)


class _FakePosts:
    def __init__(self, posts):
        self._posts = posts

    def find(self, *args, **kwargs):
        return _FakeCursor(list(self._posts))


class _FakeDigests:
    def __init__(self):
        self.upserted = None

    async def update_one(self, query, update, upsert=False):
        self.upserted = (query, update, upsert)


class _FakeDatabase:
    def __init__(self, posts):
        self.posts = _FakePosts(posts)
        self.digests = _FakeDigests()


class TestGenerateDailyDigest:
    """The full generate_daily_digest flow — mocked Grove + fake DB."""

    async def test_stores_digest_with_correct_shape(self, monkeypatch):
        """generate_daily_digest stores a doc with the expected keys in digests."""
        now = datetime(2026, 7, 23, 10, 0, 0, tzinfo=timezone.utc)

        sample_posts = [
            {
                "_id": "post1",
                "agentHandle": "@github-radar",
                "body": "Stars exploding",
                "verdict": "hype looks real",
                "rankScore": 78,
                "project": {
                    "title": "awesome-repo",
                    "url": "https://github.com/owner/awesome-repo",
                    "kind": "repo",
                    "momentumScore": 85,
                },
            },
            {
                "_id": "post2",
                "agentHandle": "@youtube-trends",
                "body": "Viral video",
                "verdict": "emerging",
                "rankScore": 65,
                "project": {
                    "title": "Agent Frameworks Explained",
                    "url": "https://youtube.com/watch?v=abc",
                    "kind": "video",
                    "momentumScore": 70,
                },
            },
        ]

        db = _FakeDatabase(sample_posts)

        async def fake_call_grove(posts):
            return [
                {"id": 0, "blurb": "GitHub stars signal real adoption."},
                {"id": 1, "blurb": "Viral agent framework explainer."},
            ]

        monkeypatch.setattr(daily_digest, "_call_grove", fake_call_grove)

        result = await daily_digest.generate_daily_digest(db=db, now=now)

        assert result is not None
        assert result["date"] == "2026-07-23"
        assert result["digestType"] == "daily"
        assert result["publicationSyncStatus"] == "synced"
        assert result["evidenceContractVersion"] == 2
        assert result["createdAt"] == now
        assert len(result["items"]) == 2
        item = result["items"][0]
        assert item["rank"] == 1
        assert item["agentHandle"] == "@github-radar"
        assert item["title"] == "awesome-repo"
        assert item["url"] == "https://github.com/owner/awesome-repo"
        assert item["kind"] == "repo"
        assert item["blurb"] == "GitHub stars signal real adoption."
        assert item["score"] == 78
        # stars/velocity/contributorCount don't exist on posts — set to None
        assert item["stars"] is None
        assert item["velocity"] is None
        assert item["contributorCount"] is None

        # Verify it was stored
        assert db.digests.upserted is not None
        query, update, upsert = db.digests.upserted
        assert query["date"] == "2026-07-23"
        assert query["digestType"] == "daily"
        assert upsert is True

    async def test_no_posts_returns_none(self, monkeypatch):
        """When there are no posts, no digest is generated."""
        now = datetime(2026, 7, 23, 10, 0, 0, tzinfo=timezone.utc)
        db = _FakeDatabase([])

        result = await daily_digest.generate_daily_digest(db=db, now=now)
        assert result is None

    async def test_max_5_items_and_diversity(self, monkeypatch):
        """The digest has at most 5 items and respects max 2 per agent."""
        now = datetime(2026, 7, 23, 10, 0, 0, tzinfo=timezone.utc)

        # 6 posts, 3 from one agent
        sample_posts = [
            _make_post(i, agent="@github-radar" if i < 3 else "@youtube-trends")
            for i in range(6)
        ]

        db = _FakeDatabase(sample_posts)

        async def fake_call_grove(posts):
            # Return 5 picks with max 2 per agent
            return [
                {"id": 0, "blurb": "b0"},
                {"id": 1, "blurb": "b1"},
                {"id": 3, "blurb": "b3"},
                {"id": 4, "blurb": "b4"},
                {"id": 5, "blurb": "b5"},
            ]

        monkeypatch.setattr(daily_digest, "_call_grove", fake_call_grove)
        result = await daily_digest.generate_daily_digest(db=db, now=now)

        assert result is not None
        assert len(result["items"]) <= 5
        agent_counts: dict[str, int] = {}
        for item in result["items"]:
            agent_counts[item["agentHandle"]] = (
                agent_counts.get(item["agentHandle"], 0) + 1
            )
        for count in agent_counts.values():
            assert count <= 2, f"More than 2 items from same agent: {agent_counts}"

    async def test_backfill_when_grove_picks_fewer_than_max(self, monkeypatch):
        """When Grove picks fewer than 5 (or diversity drops items), backfill."""
        now = datetime(2026, 7, 23, 10, 0, 0, tzinfo=timezone.utc)

        # 6 posts across 3 agents so backfill can fill up to 5
        sample_posts = [
            _make_post(0, agent="@github-radar", rank_score=90),
            _make_post(1, agent="@github-radar", rank_score=88),
            _make_post(2, agent="@youtube-trends", rank_score=86),
            _make_post(3, agent="@reddit-pulse", rank_score=84),
            _make_post(4, agent="@hidden-gems", rank_score=82),
            _make_post(5, agent="@hidden-gems", rank_score=80),
        ]

        db = _FakeDatabase(sample_posts)

        async def fake_call_grove(posts):
            # Grove only picks 2 — backfill should fill the rest
            return [
                {"id": 0, "blurb": "b0"},
                {"id": 2, "blurb": "b2"},
            ]

        monkeypatch.setattr(daily_digest, "_call_grove", fake_call_grove)
        result = await daily_digest.generate_daily_digest(db=db, now=now)

        assert result is not None
        assert len(result["items"]) == 5, (
            f"Expected 5 items after backfill, got {len(result['items'])}"
        )
        # Diversity still respected
        agent_counts: dict[str, int] = {}
        for item in result["items"]:
            agent_counts[item["agentHandle"]] = (
                agent_counts.get(item["agentHandle"], 0) + 1
            )
        for count in agent_counts.values():
            assert count <= 2, f"Backfill violated diversity: {agent_counts}"

    async def test_backfill_uses_body_as_blurb(self, monkeypatch):
        """Backfilled items use the post body as the blurb (truncated)."""
        now = datetime(2026, 7, 23, 10, 0, 0, tzinfo=timezone.utc)

        sample_posts = [
            _make_post(0, agent="@github-radar", body="grove picked"),
            _make_post(1, agent="@youtube-trends", body="B" * 200),
        ]

        db = _FakeDatabase(sample_posts)

        async def fake_call_grove(posts):
            return [{"id": 0, "blurb": "grove blurb"}]

        monkeypatch.setattr(daily_digest, "_call_grove", fake_call_grove)
        result = await daily_digest.generate_daily_digest(db=db, now=now)

        assert result is not None
        assert len(result["items"]) == 2
        # First item from Grove
        assert result["items"][0]["blurb"] == "grove blurb"
        # Second item is backfilled — blurb comes from body, truncated to 100
        backfill_item = result["items"][1]
        assert len(backfill_item["blurb"]) <= 100
        assert backfill_item["blurb"] == "B" * 100

    async def test_invalid_json_from_grove_still_backfills(self, monkeypatch):
        """When Grove returns invalid JSON, backfill fills from posts by rankScore."""
        now = datetime(2026, 7, 23, 10, 0, 0, tzinfo=timezone.utc)

        sample_posts = [
            _make_post(i, agent=agent)
            for i, agent in enumerate(
                [
                    "@github-radar",
                    "@github-radar",
                    "@youtube-trends",
                    "@reddit-pulse",
                    "@hidden-gems",
                    "@hidden-gems",
                ]
            )
        ]

        db = _FakeDatabase(sample_posts)

        async def fake_call_grove(posts):
            return []  # Grove returned nothing valid

        monkeypatch.setattr(daily_digest, "_call_grove", fake_call_grove)
        result = await daily_digest.generate_daily_digest(db=db, now=now)

        assert result is not None
        assert len(result["items"]) == 5, (
            f"Expected 5 backfill items, got {len(result['items'])}"
        )
        agent_counts: dict[str, int] = {}
        for item in result["items"]:
            agent_counts[item["agentHandle"]] = (
                agent_counts.get(item["agentHandle"], 0) + 1
            )
        for count in agent_counts.values():
            assert count <= 2


class TestDailyDigestAgentHandles:
    """The agent handle list excludes internal agents."""

    def test_community_radar_excluded(self):
        assert "@community-radar" not in DAILY_DIGEST_AGENT_HANDLES

    def test_weekly_digest_excluded(self):
        assert "@weekly-digest" not in DAILY_DIGEST_AGENT_HANDLES

    def test_all_four_external_agents_present(self):
        assert len(DAILY_DIGEST_AGENT_HANDLES) == 4
