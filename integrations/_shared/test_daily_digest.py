"""Tests for the daily digest generator.

Seam: the filter construction, Grove LLM call, and MongoDB storage in
`integrations/_shared/daily_digest.py`.
"""

import json
import os
import sys
from datetime import datetime, timedelta, timezone

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from _shared import daily_digest  # noqa: E402
from _shared.daily_digest import (  # noqa: E402
    _build_grove_prompt,
    _call_grove,
    _daily_post_filter,
    DAILY_DIGEST_AGENT_HANDLES,
)
from _shared.star_history import StarHistoryBatchFailed  # noqa: E402


# ---- Shared helpers -----------------------------------------------------------


@pytest.fixture(autouse=True)
def _no_live_hn(monkeypatch):
    """Digest HN lookups stay offline unless a test supplies a story."""

    async def no_story(url, client=None):
        return None

    monkeypatch.setattr(daily_digest, "fetch_hn_engagement", no_story)
    monkeypatch.setattr(daily_digest, "fetch_hn_story", no_story)


@pytest.fixture(autouse=True)
def _no_live_github(monkeypatch):
    """Star history and /repos stay offline: unknown unless a test supplies them."""

    async def no_star_history(slugs, now=None, client=None):
        return {}, {}

    async def no_repo(client, owner, repo):
        return None

    monkeypatch.setattr(daily_digest, "fetch_stars_this_week_batch", no_star_history)
    monkeypatch.setattr(daily_digest, "_fetch_github_repo", no_repo)


def _keep_every_post(monkeypatch, listed: list[dict]):
    """Item-text tests bypass slots: every listed post becomes an item, in order."""

    def keep_all(posts, evidence, featured, now):
        chosen = [{"post": post, "slot": None, "evidence": {}} for post in listed]
        return {"chosen": chosen, "belowFloor": False}

    monkeypatch.setattr(daily_digest, "compose_daily_digest", keep_all)


def _make_post(
    i: int,
    agent: str = "@github-radar",
    rank_score: float = 70,
    momentum_score: float = 80,
    body: str = "blurb",
    verdict: str = "hype looks real",
    kind: str = "repo",
    signal: dict | None = None,
    title: str | None = None,
    description: str | None = None,
    url: str | None = None,
) -> dict:
    """Build a post document that matches the real write_post.py schema."""
    post = {
        "_id": f"post{i}",
        "agentHandle": agent,
        "body": body,
        "verdict": verdict,
        "rankScore": rank_score + i,
        "signal": signal or {},
        "project": {
            "title": title if title is not None else f"repo-{i}",
            "url": url if url is not None else f"https://example.com/repo-{i}",
            "kind": kind,
            "momentumScore": momentum_score,
        },
    }
    if description is not None:
        post["project"]["description"] = description
    return post


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

    def test_prompt_asks_for_a_blurb_per_chosen_item_without_choosing(self):
        """Evidence chooses and orders; Grove only writes."""
        posts = [_make_post(0, rank_score=80, momentum_score=85)]
        prompt = _build_grove_prompt(posts)
        assert "already chosen and ordered" in prompt
        assert "for every post" in prompt.lower()
        assert "top 5" not in prompt.lower()
        assert "JSON" in prompt

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

    def sort(self, key, direction=1):
        self._posts.sort(key=lambda doc: doc.get(key, 0), reverse=direction < 0)
        return self

    async def to_list(self, length):
        return list(self._posts)[:length]


def _kind_matches(post: dict, wanted) -> bool:
    kind = post.get("project", {}).get("kind")
    if isinstance(wanted, dict):
        return kind != wanted["$ne"]
    return kind == wanted


class _FakePosts:
    """Applies only the ``project.kind`` part of a query, plus sort and limit."""

    def __init__(self, posts):
        self._posts = posts

    def find(self, query=None, *args, **kwargs):
        wanted = (query or {}).get("project.kind")
        return _FakeCursor(
            [p for p in self._posts if wanted is None or _kind_matches(p, wanted)]
        )


class _FakeDigests:
    def __init__(self):
        self.upserted = None
        self.history: list[dict] = []
        self.last_find_query = None

    def find(self, query, projection=None):
        self.last_find_query = query
        return _FakeCursor(
            [doc for doc in self.history if doc.get("date") != query["date"]["$ne"]]
        )

    async def update_one(self, query, update, upsert=False):
        self.upserted = (query, update, upsert)


class _FakeDatabase:
    def __init__(self, posts):
        self.posts = _FakePosts(posts)
        self.digests = _FakeDigests()


def _fake_star_history(monkeypatch, weekly: dict[str, int], calls: list | None = None):
    """Star history for these lowercase slugs; any other repo is unknown."""

    async def fake_batch(slugs, now=None, client=None):
        if calls is not None:
            calls.append(list(slugs))
        return (
            {
                slug: {"starsThisWeek": weekly[slug], "starsToday": 30, "days": []}
                for slug in slugs
                if slug in weekly
            },
            {},
        )

    monkeypatch.setattr(daily_digest, "fetch_stars_this_week_batch", fake_batch)


def _blurb_every_post(monkeypatch):
    async def fake_call_grove(posts):
        return [{"id": i, "blurb": f"blurb {i}"} for i in range(len(posts))]

    monkeypatch.setattr(daily_digest, "_call_grove", fake_call_grove)


class TestGenerateDailyDigest:
    """The DB layer around compose_daily_digest — fake DB, Grove and GitHub."""

    NOW = datetime(2026, 7, 23, 10, 0, 0, tzinfo=timezone.utc)

    async def test_stores_digest_with_correct_shape(self, monkeypatch):
        """generate_daily_digest stores a doc with the expected keys in digests."""
        sample_posts = [
            {
                "_id": "post1",
                "agentHandle": "@github-radar",
                "body": "Stars exploding",
                "verdict": "hype looks real",
                "rankScore": 78,
                "signal": {"source": "github", "metric": "github_stars", "value": 4000},
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
                "signal": {"source": "youtube", "metric": "views", "value": 40000},
                "project": {
                    "title": "Agent Frameworks Explained",
                    "url": "https://youtube.com/watch?v=abc",
                    "kind": "video",
                    "momentumScore": 70,
                },
            },
        ]
        db = _FakeDatabase(sample_posts)
        _fake_star_history(monkeypatch, {"owner/awesome-repo": 300})

        async def fake_call_grove(posts):
            return [
                {"id": 0, "blurb": "GitHub stars signal real adoption."},
                {"id": 1, "blurb": "Viral agent framework explainer."},
            ]

        monkeypatch.setattr(daily_digest, "_call_grove", fake_call_grove)

        result = await daily_digest.generate_daily_digest(db=db, now=self.NOW)

        assert result is not None
        assert result["date"] == "2026-07-23"
        assert result["digestType"] == "daily"
        assert result["publicationSyncStatus"] == "synced"
        assert result["evidenceContractVersion"] == 2
        assert result["createdAt"] == self.NOW
        assert result["belowFloor"] is False
        assert len(result["items"]) == 2
        item = result["items"][0]
        assert item["rank"] == 1
        assert item["agentHandle"] == "@github-radar"
        assert item["title"] == "awesome-repo"
        assert item["url"] == "https://github.com/owner/awesome-repo"
        assert item["kind"] == "repo"
        assert item["blurb"] == "GitHub stars signal real adoption."
        assert item["score"] == 78
        assert (item["weeklyGain"], item["weeklyGainWindowDays"]) == (300, 7)
        assert "digestScore" not in item
        # stars/velocity/contributorCount don't exist on posts — set to None
        assert item["stars"] is None
        assert item["velocity"] is None
        assert item["contributorCount"] is None
        assert result["items"][1]["weeklyGain"] is None

        query, update, upsert = db.digests.upserted
        assert query == {"date": "2026-07-23", "digestType": "daily"}
        assert update["$set"]["belowFloor"] is False
        assert upsert is True

    async def test_the_live_2026_11_26_shape_goes_out_repos_first(self, monkeypatch):
        with open(os.path.join(_TESTDATA, "daily-digest-2026-11-26.json")) as f:
            live = json.load(f)
        db = _FakeDatabase([_post_from_digest_item(item) for item in live["items"]])
        _fake_star_history(
            monkeypatch,
            {"molly554/whiteboard-ai-tutor": 280, "logitcai/tinybrain": 283},
        )
        _blurb_every_post(monkeypatch)

        result = await daily_digest.generate_daily_digest(db=db, now=SLOT_NOW)

        # The three Reddit posts are old posts without self-text or a link
        # field, so they fail the content rule (ticket 06).
        kinds = [item["kind"] for item in result["items"]]
        assert kinds == ["repo", "repo"]

    async def test_a_heavy_reddit_day_cannot_push_the_repos_out_of_the_load(
        self, monkeypatch
    ):
        """Repos and other kinds are loaded with separate cuts (ADR 0008)."""
        threads = [_slot_reddit(f"thread-{n}", 2000 - n) for n in range(60)]
        for thread in threads:
            thread["rankScore"] = 95
        repo = _slot_repo("breakout")
        repo["rankScore"] = 10
        _fake_star_history(monkeypatch, {"acme/breakout": 500})
        _blurb_every_post(monkeypatch)

        result = await daily_digest.generate_daily_digest(
            db=_FakeDatabase(threads + [repo]), now=self.NOW
        )

        assert [item["url"] for item in result["items"]][0] == (
            "https://github.com/acme/breakout"
        )

    async def test_reddit_item_keeps_rank_score_and_exposes_raw_signal(
        self, monkeypatch
    ):
        post = _make_post(
            0,
            agent="@reddit-pulse",
            kind="thread",
            rank_score=73,
            signal={
                "source": "reddit",
                "metric": "upvotes",
                "value": 2109,
                "delta": 724,
            },
        )
        post["project"]["bodyText"] = "x" * 200
        db = _FakeDatabase([post])

        async def fake_call_grove(posts):
            return [{"id": 0, "blurb": "A Reddit discussion is taking off."}]

        monkeypatch.setattr(daily_digest, "_call_grove", fake_call_grove)

        result = await daily_digest.generate_daily_digest(db=db, now=self.NOW)

        assert result is not None
        item = result["items"][0]
        assert item["score"] == 73
        assert item["signalSource"] == "reddit"
        assert item["signalMetric"] == "upvotes"
        assert item["signalValue"] == 2109
        assert item["signalDelta"] == 724
        assert result["belowFloor"] is True

    async def test_no_posts_returns_none(self, monkeypatch):
        """When there are no posts, no digest is generated."""
        db = _FakeDatabase([])

        result = await daily_digest.generate_daily_digest(db=db, now=self.NOW)
        assert result is None

    async def test_nothing_chosen_stores_nothing(self, monkeypatch):
        db = _FakeDatabase([_slot_repo("slow")])
        _fake_star_history(monkeypatch, {"acme/slow": 40})

        assert await daily_digest.generate_daily_digest(db=db, now=self.NOW) is None
        assert db.digests.upserted is None

    async def test_star_history_is_fetched_once_per_distinct_repo(self, monkeypatch):
        posts = [
            _slot_repo("tool"),
            _slot_show_hn("tool", 300, url="https://github.com/acme/tool/tree/main"),
            _make_post(
                1,
                title="Breakout",
                url="https://github.com/Acme/Breakout",
                signal={"source": "github", "metric": "github_stars", "value": 9000},
            ),
            _slot_reddit("thread", 900),
        ]
        calls: list = []
        _fake_star_history(
            monkeypatch, {"acme/tool": 200, "acme/breakout": 2000}, calls=calls
        )
        _blurb_every_post(monkeypatch)

        result = await daily_digest.generate_daily_digest(
            db=_FakeDatabase(posts), now=self.NOW
        )

        assert [sorted(call) for call in calls] == [["acme/breakout", "acme/tool"]]
        assert [item["title"] for item in result["items"]] == [
            "Breakout",
            "tool",
            "thread",
        ]
        assert result["items"][1]["hnPoints"] == 300

    async def test_repos_created_at_gives_the_new_repo_boost(self, monkeypatch):
        """created_at comes from the /repos lookup, once per repo."""
        posts = [
            _slot_repo("veteran"),
            _slot_show_hn("newborn", 40, url="https://github.com/acme/newborn"),
        ]
        _fake_star_history(monkeypatch, {"acme/veteran": 600, "acme/newborn": 600})
        looked_up = []

        async def fake_repo(client, owner, repo):
            looked_up.append(f"{owner}/{repo}")
            created = "2019-01-01T00:00:00Z"
            if repo == "newborn":
                created = "2026-06-30T09:00:00Z"
            return {"created_at": created, "stargazers_count": 1000}

        monkeypatch.setattr(daily_digest, "_fetch_github_repo", fake_repo)
        _blurb_every_post(monkeypatch)

        result = await daily_digest.generate_daily_digest(
            db=_FakeDatabase(posts), now=self.NOW
        )

        assert sorted(looked_up) == ["acme/newborn", "acme/veteran"]
        assert [item["url"] for item in result["items"]] == [
            "https://github.com/acme/newborn",
            "https://github.com/acme/veteran",
        ]

    async def test_a_repos_hn_story_reaches_the_item(self, monkeypatch):
        posts = [_slot_repo("tool")]
        _fake_star_history(monkeypatch, {"acme/tool": 500})

        async def fake_hn(url, client=None):
            return {"points": 292, "comments": 139}

        monkeypatch.setattr(daily_digest, "fetch_hn_engagement", fake_hn)
        _blurb_every_post(monkeypatch)

        result = await daily_digest.generate_daily_digest(
            db=_FakeDatabase(posts), now=self.NOW
        )

        item = result["items"][0]
        assert (item["hnPoints"], item["hnComments"]) == (292, 139)

    async def test_a_show_hn_thread_carries_its_own_story(self, monkeypatch):
        show_hn = _slot_show_hn("launch", 100)
        story_id = show_hn["signal"]["evidenceUrl"].rsplit("=", 1)[1]
        looked_up = []

        async def fake_story(found_id, client=None):
            looked_up.append(found_id)
            return {"points": 130, "comments": 80}

        monkeypatch.setattr(daily_digest, "fetch_hn_story", fake_story)
        _blurb_every_post(monkeypatch)

        result = await daily_digest.generate_daily_digest(
            db=_FakeDatabase([show_hn]), now=self.NOW
        )

        assert looked_up == [story_id]
        item = result["items"][0]
        assert (item["hnPoints"], item["hnComments"]) == (130, 80)

    async def test_items_featured_in_the_last_30_daily_digests_are_not_repeated(
        self, monkeypatch
    ):
        posts = [_slot_repo(name) for name in ("old", "rerun", "new")]
        _fake_star_history(
            monkeypatch, {f"acme/{n}": 500 for n in ("old", "rerun", "new")}
        )
        db = _FakeDatabase(posts)
        db.digests.history = [
            {"date": "2026-07-01", "items": [{"url": "https://github.com/acme/old"}]},
            # Today's own earlier run must not hide today's picks on a re-run.
            {"date": "2026-07-23", "items": [{"url": "https://github.com/acme/rerun"}]},
        ]
        _blurb_every_post(monkeypatch)

        result = await daily_digest.generate_daily_digest(db=db, now=self.NOW)

        assert [item["title"] for item in result["items"]] == ["rerun", "new"]
        query = db.digests.last_find_query
        assert query["digestType"] == "daily"
        assert query["createdAt"] == {"$gte": self.NOW - timedelta(days=30)}

    async def test_a_star_history_outage_fails_the_digest_loudly(self, monkeypatch):
        async def broken_batch(slugs, now=None, client=None):
            raise StarHistoryBatchFailed("star history failed for 2 of 2 repos")

        monkeypatch.setattr(daily_digest, "fetch_stars_this_week_batch", broken_batch)
        db = _FakeDatabase([_slot_repo("a"), _slot_repo("b"), _slot_reddit("t", 900)])

        with pytest.raises(StarHistoryBatchFailed):
            await daily_digest.generate_daily_digest(db=db, now=self.NOW)
        assert db.digests.upserted is None

    async def test_grove_picks_no_longer_decide_what_goes_out(self, monkeypatch):
        """Grove naming one id neither shrinks nor reorders the digest."""
        sample_posts = [
            _make_post(0, agent="@github-radar", rank_score=90),
            _slot_reddit("loud", 2109, comments=724),
            _slot_reddit("quieter", 900, comments=80),
        ]
        db = _FakeDatabase(sample_posts)

        async def fake_call_grove(posts):
            return [{"id": 1, "blurb": "Grove blurb for the second item"}]

        monkeypatch.setattr(daily_digest, "_call_grove", fake_call_grove)

        result = await daily_digest.generate_daily_digest(db=db, now=self.NOW)

        # The repo has no star history; a lone Reddit slot tops up to two.
        first, second = result["items"]
        assert first["signalValue"] == 2109
        assert first["signalDelta"] == 724
        assert first["blurb"] == "blurb"  # no Grove blurb for id 0: body stands in
        assert second["blurb"] == "Grove blurb for the second item"
        assert result["belowFloor"] is True

    async def test_backfill_uses_body_as_blurb(self, monkeypatch):
        """Items without a Grove blurb use the post body, truncated to 100."""
        sample_posts = [
            _slot_reddit("thread", 900),
            _slot_video("video", 30000),
        ]
        sample_posts[0]["body"] = "grove picked"
        sample_posts[1]["body"] = "B" * 200
        db = _FakeDatabase(sample_posts)

        async def fake_call_grove(posts):
            return [{"id": 0, "blurb": "grove blurb"}]

        monkeypatch.setattr(daily_digest, "_call_grove", fake_call_grove)
        result = await daily_digest.generate_daily_digest(db=db, now=self.NOW)

        assert len(result["items"]) == 2
        assert result["items"][0]["blurb"] == "grove blurb"
        assert result["items"][1]["blurb"] == "B" * 100

    async def test_invalid_json_from_grove_still_sends_the_digest(self, monkeypatch):
        """When Grove returns nothing usable, the chosen items go out with body blurbs."""
        db = _FakeDatabase([_slot_reddit("thread", 900), _slot_video("video", 30000)])

        async def fake_call_grove(posts):
            return []

        monkeypatch.setattr(daily_digest, "_call_grove", fake_call_grove)
        result = await daily_digest.generate_daily_digest(db=db, now=self.NOW)

        assert len(result["items"]) == 2
        assert all(item["blurb"] == "blurb" for item in result["items"])

    async def test_a_grove_failure_costs_the_blurbs_not_the_digest(self, monkeypatch):
        _fake_star_history(monkeypatch, {"acme/tool": 500})

        async def broken_grove(_posts):
            raise RuntimeError("grove down")

        monkeypatch.setattr(daily_digest, "_call_grove", broken_grove)

        result = await daily_digest.generate_daily_digest(
            db=_FakeDatabase([_slot_repo("tool")]), now=self.NOW
        )

        assert result["items"][0]["blurb"] == "blurb"


class TestDigestItemWhat:
    """`what`: one plain line saying what the project is, for the RomBot digest."""

    NOW = datetime(2026, 7, 23, 10, 0, 0, tzinfo=timezone.utc)

    async def _digest(self, monkeypatch, posts, grove_picks):
        async def fake_call_grove(_posts):
            return grove_picks

        monkeypatch.setattr(daily_digest, "_call_grove", fake_call_grove)
        _keep_every_post(monkeypatch, posts)
        result = await daily_digest.generate_daily_digest(
            db=_FakeDatabase(posts), now=self.NOW
        )
        assert result is not None
        return result["items"]

    async def test_what_is_the_project_description_with_whitespace_collapsed(
        self, monkeypatch
    ):
        post = _make_post(
            0,
            description="  A git hook\n that won't let you push\tcode you can't explain.  ",
        )
        items = await self._digest(monkeypatch, [post], [{"id": 0, "blurb": "b"}])
        assert (
            items[0]["what"]
            == "A git hook that won't let you push code you can't explain."
        )

    async def test_long_description_is_cut_at_a_word_boundary_within_140(
        self, monkeypatch
    ):
        words = " ".join(f"word{n:02d}" for n in range(40))
        post = _make_post(0, description=words)
        items = await self._digest(monkeypatch, [post], [{"id": 0, "blurb": "b"}])
        what = items[0]["what"]
        assert len(what) <= 140
        assert words.startswith(what)
        assert words[len(what)] == " "

    async def test_description_of_exactly_140_characters_is_kept_whole(
        self, monkeypatch
    ):
        text = " ".join(["a" * 19] * 6 + ["a" * 20])
        assert len(text) == 140
        post = _make_post(0, description=text)
        items = await self._digest(monkeypatch, [post], [{"id": 0, "blurb": "b"}])
        assert items[0]["what"] == text

    async def test_word_boundary_right_after_character_140_keeps_all_140(
        self, monkeypatch
    ):
        text = " ".join(["a" * 19] * 6 + ["a" * 20])
        post = _make_post(0, description=f"{text} tail")
        items = await self._digest(monkeypatch, [post], [{"id": 0, "blurb": "b"}])
        assert items[0]["what"] == text

    async def test_single_word_longer_than_140_is_hard_cut(self, monkeypatch):
        post = _make_post(0, description="x" * 300)
        items = await self._digest(monkeypatch, [post], [{"id": 0, "blurb": "b"}])
        assert items[0]["what"] == "x" * 140

    async def test_grove_supplies_what_only_when_description_is_empty(
        self, monkeypatch
    ):
        posts = [
            _make_post(0, description=""),
            _make_post(1, agent="@youtube-trends", description="Real description."),
        ]
        picks = [
            {"id": 0, "blurb": "b0", "what": "  A tool\nfor agents  "},
            {"id": 1, "blurb": "b1", "what": "Invented by the model"},
        ]
        items = await self._digest(monkeypatch, posts, picks)
        assert items[0]["what"] == "A tool for agents"
        assert items[1]["what"] == "Real description."

    async def test_grove_what_with_a_number_is_dropped_but_a_description_keeps_its_numbers(
        self, monkeypatch
    ):
        posts = [
            _make_post(0, description=""),
            _make_post(1, agent="@youtube-trends", description=""),
            _make_post(2, agent="@reddit-pulse", description="A Python 3 agent kit."),
        ]
        picks = [
            {"id": 0, "blurb": "b0", "what": "A project named x with 12,113 stars"},
            {"id": 1, "blurb": "b1", "what": "A terminal tool for agents"},
            {"id": 2, "blurb": "b2", "what": "Invented by the model"},
        ]
        items = await self._digest(monkeypatch, posts, picks)
        assert [item["what"] for item in items] == [
            None,
            "A terminal tool for agents",
            "A Python 3 agent kit.",
        ]

    async def test_what_is_null_when_description_and_grove_are_empty(self, monkeypatch):
        posts = [
            _make_post(0),
            _make_post(1, agent="@youtube-trends", description="   "),
            _make_post(2, agent="@reddit-pulse", description=""),
        ]
        picks = [
            {"id": 0, "blurb": "b0"},
            {"id": 1, "blurb": "b1", "what": ""},
            {"id": 2, "blurb": "b2", "what": 42},
        ]
        items = await self._digest(monkeypatch, posts, picks)
        assert [item["what"] for item in items] == [None, None, None]

    async def test_backfilled_item_gets_what_from_description_only(self, monkeypatch):
        posts = [
            _make_post(0, description="Picked."),
            _make_post(1, agent="@youtube-trends", description="Backfilled one."),
            _make_post(2, agent="@reddit-pulse", description=""),
        ]
        items = await self._digest(monkeypatch, posts, [{"id": 0, "blurb": "b0"}])
        assert [item["what"] for item in items] == ["Picked.", "Backfilled one.", None]

    async def test_what_is_html_unescaped_like_the_title(self, monkeypatch):
        posts = [
            _make_post(0, description="Tom &amp; Jerry&#39;s &lt;agent&gt;&nbsp;kit"),
            _make_post(1, agent="@reddit-pulse", description=""),
        ]
        picks = [
            {"id": 0, "blurb": "b0"},
            {"id": 1, "blurb": "b1", "what": "Q&amp;A bot"},
        ]
        items = await self._digest(monkeypatch, posts, picks)
        assert items[0]["what"] == "Tom & Jerry's <agent> kit"
        assert items[1]["what"] == "Q&A bot"

    async def test_videos_carry_no_what_because_their_description_is_a_byline(
        self, monkeypatch
    ):
        byline = "By Some Channel \u00b7 230,000 views"
        posts = [
            _make_post(0, agent="@youtube-trends", kind="video", description=byline),
            _make_post(1, description="A real repo description."),
        ]
        picks = [
            {"id": 0, "blurb": "b0", "what": "Invented for a video"},
            {"id": 1, "blurb": "b1"},
        ]
        items = await self._digest(monkeypatch, posts, picks)
        assert [item["kind"] for item in items] == ["video", "repo"]
        assert [item["what"] for item in items] == [
            None,
            "A real repo description.",
        ]

    def test_item_what_skips_grove_for_a_github_repo_without_description(self):
        """A GitHub repo without a stored description gets its About line via
        the enrichment pass or nothing — Grove must not guess for it."""
        github_project = {
            "kind": "repo",
            "url": "https://github.com/JetBrains/thinkrail",
            "description": "",
        }
        assert daily_digest._item_what(github_project, "A Grove guess") is None

    def test_item_what_keeps_the_grove_fallback_for_non_github_repos(self):
        other_host = {
            "kind": "repo",
            "url": "https://gitlab.com/foo/bar",
            "description": "",
        }
        assert daily_digest._item_what(other_host, "A Grove line") == "A Grove line"

    def test_clean_what_drops_a_dangling_trailing_conjunction(self):
        """A 140-char cut can land mid-list ("... data-backed charts and");
        the clause must not end on the connective."""
        text = " ".join(["filler"] * 19) + " and " + "x" * 40
        assert daily_digest._clean_what(text) == " ".join(["filler"] * 19)

    async def test_github_repo_without_description_gets_the_authors_about_line(
        self, monkeypatch
    ):
        """The digest fetches the repo's About line once and uses it as `what`;
        Grove's guess is not used for a GitHub repo."""
        posts = [
            _make_post(
                0, url="https://github.com/JetBrains/thinkrail", description=""
            )
        ]
        calls = []

        async def fake_fetch(client, owner, repo):
            calls.append((owner, repo))
            return "Vibe code with pi in a lightweight, real IDE"

        monkeypatch.setattr(
            daily_digest, "_fetch_github_about_description", fake_fetch
        )
        items = await self._digest(
            monkeypatch, posts, [{"id": 0, "blurb": "b", "what": "A Grove guess"}]
        )
        assert items[0]["what"] == "Vibe code with pi in a lightweight, real IDE"
        assert calls == [("JetBrains", "thinkrail")]

    async def test_github_about_failure_leaves_what_null_not_a_grove_guess(
        self, monkeypatch
    ):
        posts = [_make_post(0, url="https://github.com/o/r", description="")]

        async def fake_fetch(client, owner, repo):
            return None

        monkeypatch.setattr(
            daily_digest, "_fetch_github_about_description", fake_fetch
        )
        items = await self._digest(
            monkeypatch, posts, [{"id": 0, "blurb": "b", "what": "A digit free guess"}]
        )
        assert items[0]["what"] is None

    async def test_non_github_repos_keep_the_grove_fallback_and_are_not_fetched(
        self, monkeypatch
    ):
        posts = [_make_post(0, url="https://gitlab.com/foo/bar", description="")]
        calls = []

        async def fake_fetch(client, owner, repo):
            calls.append((owner, repo))
            return "Should not be fetched"

        monkeypatch.setattr(
            daily_digest, "_fetch_github_about_description", fake_fetch
        )
        items = await self._digest(
            monkeypatch, posts, [{"id": 0, "blurb": "b", "what": "A Grove line"}]
        )
        assert items[0]["what"] == "A Grove line"
        assert calls == []

    async def test_the_about_backstop_dedupes_repeated_repos(self, monkeypatch):
        """Two items pointing at the same repo cost one API call."""
        url = "https://github.com/JetBrains/thinkrail"
        items = [
            {"kind": "repo", "url": url, "what": None},
            {"kind": "repo", "url": url, "what": None},
        ]
        calls = []

        async def fake_fetch(client, owner, repo):
            calls.append((owner, repo))
            return "An IDE that adapts to you"

        monkeypatch.setattr(
            daily_digest, "_fetch_github_about_description", fake_fetch
        )
        monkeypatch.setattr(daily_digest, "_ABOUT_PER_REPO_SLEEP_SECONDS", 0)
        await daily_digest._enrich_missing_repo_whats(items)
        assert calls == [("JetBrains", "thinkrail")]
        assert [item["what"] for item in items] == ["An IDE that adapts to you"] * 2

    async def test_backfilled_github_items_get_the_about_line_too(self, monkeypatch):
        posts = [
            _make_post(0, description="Picked."),
            _make_post(
                1,
                agent="@youtube-trends",
                url="https://github.com/o/backfilled",
                description="",
            ),
        ]

        async def fake_fetch(client, owner, repo):
            return "Backfilled description"

        monkeypatch.setattr(
            daily_digest, "_fetch_github_about_description", fake_fetch
        )
        items = await self._digest(monkeypatch, posts, [{"id": 0, "blurb": "b"}])
        assert [item["what"] for item in items] == [
            "Picked.",
            "Backfilled description",
        ]

    async def test_subpath_github_urls_are_not_fetched(self, monkeypatch):
        """Only strict repo URLs are enriched; an issues link is not a repo."""
        posts = [_make_post(0, url="https://github.com/o/r/issues", description="")]
        calls = []

        async def fake_fetch(client, owner, repo):
            calls.append((owner, repo))
            return "Should not be fetched"

        monkeypatch.setattr(
            daily_digest, "_fetch_github_about_description", fake_fetch
        )
        items = await self._digest(
            monkeypatch, posts, [{"id": 0, "blurb": "b", "what": "A Grove line"}]
        )
        assert calls == []
        assert items[0]["what"] == "A Grove line"

    async def test_dot_git_suffix_urls_are_enriched(self, monkeypatch):
        posts = [_make_post(0, url="https://github.com/o/r.git", description="")]
        calls = []

        async def fake_fetch(client, owner, repo):
            calls.append((owner, repo))
            return "An about line"

        monkeypatch.setattr(
            daily_digest, "_fetch_github_about_description", fake_fetch
        )
        items = await self._digest(monkeypatch, posts, [{"id": 0, "blurb": "b"}])
        assert calls == [("o", "r")]
        assert items[0]["what"] == "An about line"

    async def test_video_with_a_github_url_is_not_fetched(self, monkeypatch):
        """The backstop only serves kind repo; a video's byline is not a what."""
        posts = [
            _make_post(
                0,
                agent="@youtube-trends",
                kind="video",
                url="https://github.com/o/r",
                description="",
            )
        ]
        calls = []

        async def fake_fetch(client, owner, repo):
            calls.append((owner, repo))
            return "Should not be fetched"

        monkeypatch.setattr(
            daily_digest, "_fetch_github_about_description", fake_fetch
        )
        items = await self._digest(
            monkeypatch, posts, [{"id": 0, "blurb": "b", "what": "A video guess"}]
        )
        assert calls == []
        assert items[0]["what"] is None

    def test_prompt_never_asks_grove_for_what_on_a_video(self):
        posts = [
            _make_post(0, kind="video", description=""),
            _make_post(1, agent="@reddit-pulse", description=""),
        ]
        prompt = _build_grove_prompt(posts)
        assert "[1]" in prompt
        assert "[0, 1]" not in prompt

    def test_prompt_never_asks_grove_for_what_on_a_github_repo(self):
        """Grove's answer for a GitHub repo is always discarded (the About
        backstop owns it), so the prompt does not pay for it."""
        posts = [
            _make_post(0, url="https://github.com/o/r", description=""),
            _make_post(1, agent="@reddit-pulse", description=""),
        ]
        prompt = _build_grove_prompt(posts)
        assert "[1]" in prompt
        assert "[0, 1]" not in prompt

    def test_prompt_asks_for_what_only_on_posts_without_a_description(self):
        posts = [
            _make_post(0, description="Has one."),
            _make_post(1, agent="@youtube-trends", description=""),
            _make_post(2, agent="@reddit-pulse"),
        ]
        prompt = _build_grove_prompt(posts)
        assert '"what"' in prompt
        assert "[1, 2]" in prompt

    def test_prompt_lets_grove_decline_and_forbids_numbers_in_what(self):
        prompt = _build_grove_prompt([_make_post(0, description="")])
        assert "no numbers" in prompt
        assert "empty string" in prompt

    def test_prompt_does_not_ask_for_what_when_every_post_has_a_description(self):
        prompt = _build_grove_prompt([_make_post(0, description="Has one.")])
        assert '"what"' not in prompt


class TestDigestItemTitleAndVerdict:
    """Titles reach RomBot as plain text; the post's verdict rides along."""

    NOW = datetime(2026, 7, 23, 10, 0, 0, tzinfo=timezone.utc)

    async def _items(self, monkeypatch, posts, picks):
        async def fake_call_grove(_posts):
            return picks

        monkeypatch.setattr(daily_digest, "_call_grove", fake_call_grove)
        _keep_every_post(monkeypatch, posts)
        result = await daily_digest.generate_daily_digest(
            db=_FakeDatabase(posts), now=self.NOW
        )
        assert result is not None
        return result["items"]

    async def test_title_is_html_unescaped_for_picked_and_backfilled_items(
        self, monkeypatch
    ):
        posts = [
            _make_post(0, title="Tom &amp; Jerry&#39;s &quot;agent&quot;"),
            _make_post(1, agent="@youtube-trends", title="Plain &lt;b&gt; title"),
        ]
        items = await self._items(monkeypatch, posts, [{"id": 0, "blurb": "b"}])
        assert items[0]["title"] == 'Tom & Jerry\'s "agent"'
        assert items[1]["title"] == "Plain <b> title"

    async def test_verdict_is_the_posts_verdict(self, monkeypatch):
        posts = [
            _make_post(0, verdict="emerging"),
            _make_post(1, agent="@youtube-trends", verdict="cooling"),
        ]
        items = await self._items(monkeypatch, posts, [{"id": 0, "blurb": "b"}])
        assert [item["verdict"] for item in items] == ["emerging", "cooling"]

    async def test_missing_or_empty_verdict_is_null(self, monkeypatch):
        no_verdict = _make_post(0)
        del no_verdict["verdict"]
        posts = [
            no_verdict,
            _make_post(1, agent="@youtube-trends", verdict=""),
        ]
        items = await self._items(monkeypatch, posts, [{"id": 0, "blurb": "b"}])
        assert [item["verdict"] for item in items] == [None, None]


class TestDailyDigestAgentHandles:
    """The agent handle list excludes internal agents."""

    def test_community_radar_excluded(self):
        assert "@community-radar" not in DAILY_DIGEST_AGENT_HANDLES

    def test_weekly_digest_excluded(self):
        assert "@weekly-digest" not in DAILY_DIGEST_AGENT_HANDLES

    def test_all_four_external_agents_present(self):
        assert len(DAILY_DIGEST_AGENT_HANDLES) == 4


# ---- Evidence-ranked selection (audit 2026-11-09, items 2, 5, 6) --------------


# ---- Slots (ADR 0008): compose_daily_digest ----------------------------------

SLOT_NOW = datetime(2026, 11, 26, 8, 45, tzinfo=timezone.utc)
_TESTDATA = os.path.join(os.path.dirname(os.path.abspath(__file__)), "testdata")


def _post_from_digest_item(item: dict) -> dict:
    """The stored post a live digest item was built from (write_post.py shape)."""
    return {
        "_id": item["url"],
        "agentHandle": item["agentHandle"],
        "body": item["blurb"],
        "verdict": "emerging",
        "rankScore": 70,
        "signal": {
            "source": item["signalSource"],
            "metric": item["signalMetric"],
            "value": item["signalValue"],
            "delta": item["signalDelta"],
            "evidenceUrl": item["url"],
        },
        "project": {
            "title": item["title"],
            "url": item["url"],
            "kind": item["kind"],
            "description": item["what"],
        },
    }


def _stars(stars_this_week: int, stars_today: int = 20, **extra) -> dict:
    return {"starsThisWeek": stars_this_week, "starsToday": stars_today, **extra}


class TestDigestSlotsRegression:
    """2026-11-26: three Reddit image and meme posts led, both repos came last."""

    def test_the_live_2026_11_26_shape_leads_with_both_repos(self):
        with open(os.path.join(_TESTDATA, "daily-digest-2026-11-26.json")) as f:
            live = json.load(f)
        posts = [_post_from_digest_item(item) for item in live["items"]]
        # whiteboard-ai-tutor's stored weeklyGain was 280; tinybrain's blurb
        # names 283 weekly stars (its weeklyGain was hidden as born this week).
        evidence = {
            "molly554/whiteboard-ai-tutor": _stars(280),
            "logitcai/tinybrain": _stars(283),
        }

        result = daily_digest.compose_daily_digest(
            posts, evidence, featured=set(), now=SLOT_NOW
        )

        kinds = [chosen["post"]["project"]["kind"] for chosen in result["chosen"]]
        urls = [chosen["post"]["project"]["url"] for chosen in result["chosen"]]
        assert kinds[:2] == ["repo", "repo"]
        assert {
            "https://github.com/molly554/whiteboard-ai-tutor",
            "https://github.com/logitcai/tinybrain",
        } <= set(urls[:4])
        # Ticket 06: the Reddit items are old posts with no self-text field.
        assert kinds == ["repo", "repo"]
        assert result["belowFloor"] is False


def _slot_repo(name: str) -> dict:
    url = f"https://github.com/acme/{name}"
    return _make_post(
        0,
        agent="@github-radar",
        title=name,
        url=url,
        description=f"{name} does a thing",
        signal={
            "source": "github",
            "metric": "github_stars",
            "value": 1000,
            "evidenceUrl": url,
        },
    )


_SUBSTANCE = "x" * 200


def _slot_reddit(
    name: str,
    upvotes: int,
    comments: int = 50,
    body: str | None = _SUBSTANCE,
    link: str | None = "",
) -> dict:
    """A Reddit post as reddit_pulse stores it; ``None`` leaves a field out (old post)."""
    post = _make_post(
        0,
        agent="@reddit-pulse",
        kind="thread",
        title=name,
        url=f"https://www.reddit.com/r/LocalLLaMA/comments/{name}",
        signal={
            "source": "reddit",
            "metric": "upvotes",
            "value": upvotes,
            "delta": comments,
        },
    )
    if body is not None:
        post["project"]["bodyText"] = body
    if link is not None:
        post["project"]["contentHref"] = link
    return post


def _slot_show_hn(
    name: str, points: int, url: str | None = None, story_text: str | None = _SUBSTANCE
) -> dict:
    story = f"https://news.ycombinator.com/item?id={abs(hash(name)) % 10**8}"
    link = url or f"https://{name}.dev"
    post = _make_post(
        0,
        agent="@hidden-gems",
        kind="repo" if "github.com" in link else "thread",
        title=f"Show HN: {name}",
        description=f"Show HN: {name}",
        url=link,
        signal={
            "source": "hacker_news",
            "metric": "hn_points",
            "value": points,
            "evidenceUrl": story,
        },
    )
    if story_text is not None:
        post["project"]["storyText"] = story_text
    return post


def _slot_video(name: str, views: int) -> dict:
    return _make_post(
        0,
        agent="@youtube-trends",
        kind="video",
        title=name,
        url=f"https://www.youtube.com/watch?v={name}",
        signal={"source": "youtube", "metric": "views", "value": views},
    )


def _compose(posts, evidence=None, featured=None):
    return daily_digest.compose_daily_digest(
        posts, evidence or {}, featured=featured or set(), now=SLOT_NOW
    )


def _titles(result) -> list[str]:
    return [chosen["post"]["project"]["title"] for chosen in result["chosen"]]


class TestRepoSlotRules:
    """Up to 3 repos lead, by hype score, with at least 100 stars this week."""

    def test_a_repo_needs_100_stars_this_week(self):
        posts = [_slot_repo("hundred"), _slot_repo("ninety-nine"), _slot_repo("unknown")]
        evidence = {"acme/hundred": _stars(100), "acme/ninety-nine": _stars(99)}
        assert _titles(_compose(posts, evidence)) == ["hundred"]

    def test_a_repo_in_the_30_day_memory_does_not_qualify(self):
        posts = [_slot_repo("old-news"), _slot_repo("seen-as-subpath"), _slot_repo("fresh")]
        evidence = {
            "acme/old-news": _stars(5000),
            "acme/seen-as-subpath": _stars(5000),
            "acme/fresh": _stars(150),
        }
        featured = {
            "https://github.com/acme/old-news",
            "https://github.com/Acme/seen-as-subpath/releases/tag/v1",
        }
        assert _titles(_compose(posts, evidence, featured)) == ["fresh"]

    def test_at_most_three_repos_strongest_hype_first(self):
        weekly = {"a": 200, "b": 5000, "c": 900, "d": 150, "e": 3000}
        posts = [_slot_repo(name) for name in weekly]
        evidence = {f"acme/{name}": _stars(stars) for name, stars in weekly.items()}
        assert _titles(_compose(posts, evidence)) == ["b", "e", "c"]

    def test_an_unused_repo_slot_is_never_refilled_with_another_kind(self):
        posts = [
            _slot_repo("only-repo"),
            _slot_reddit("first", 2040),
            _slot_reddit("second", 1150),
            _slot_reddit("third", 814),
        ]
        result = _compose(posts, {"acme/only-repo": _stars(400)})
        assert _titles(result) == ["only-repo", "first"]
        assert result["belowFloor"] is False

    def test_with_no_repo_the_digest_still_goes_out_with_the_other_slots(self):
        posts = [
            _slot_repo("quiet"),
            _slot_reddit("thread", 900),
            _slot_video("video", 50000),
        ]
        result = _compose(posts, {"acme/quiet": _stars(40)})
        assert _titles(result) == ["thread", "video"]
        assert [c["slot"] for c in result["chosen"]] == ["reddit", "video"]
        assert result["belowFloor"] is False


class TestDigestCountRules:
    """At most 5 items, at least 2 (decision Q31)."""

    def _six_slots(self, repos: int) -> tuple[list[dict], dict]:
        names = ["r1", "r2", "r3"][:repos]
        posts = [_slot_repo(name) for name in names] + [
            _slot_reddit("thread", 900),
            _slot_show_hn("launch", 180),
            _slot_video("video", 40000),
        ]
        return posts, {f"acme/{name}": _stars(500) for name in names}

    def test_when_all_six_slots_qualify_the_video_is_dropped(self):
        result = _compose(*self._six_slots(repos=3))
        assert [c["slot"] for c in result["chosen"]] == [
            "repo",
            "repo",
            "repo",
            "reddit",
            "hn",
        ]
        assert result["belowFloor"] is False

    def test_with_five_slots_filled_the_video_stays(self):
        result = _compose(*self._six_slots(repos=2))
        assert [c["slot"] for c in result["chosen"]] == [
            "repo",
            "repo",
            "reddit",
            "hn",
            "video",
        ]

    def test_under_two_items_the_strongest_non_repo_items_make_two(self):
        posts = [
            _slot_repo("slow"),
            _slot_reddit("third", 300),
            _slot_reddit("first", 2040),
            _slot_reddit("second", 1150),
        ]
        result = _compose(posts, {"acme/slow": _stars(60)})
        assert _titles(result) == ["first", "second"]
        assert result["belowFloor"] is True

    def test_a_lone_item_is_sent_and_marked_below_floor(self):
        result = _compose([_slot_repo("slow"), _slot_video("video", 900)])
        assert _titles(result) == ["video"]
        assert result["belowFloor"] is True


class TestShowHnPointsJoinTheRepo:
    """A GitHub-linked Show HN story's points become the repo's hnPoints (Q29)."""

    def test_a_linked_show_hn_story_gives_the_repo_its_points_and_lifts_it(self):
        posts = [
            _slot_repo("plain"),
            _slot_repo("launched"),
            _slot_show_hn(
                "launched", 420, url="https://github.com/acme/launched/releases/tag/v1.0"
            ),
        ]
        evidence = {"acme/plain": _stars(500), "acme/launched": _stars(500)}

        result = _compose(posts, evidence)

        assert _titles(result) == ["launched", "plain"]
        launched, plain = result["chosen"]
        assert launched["post"]["agentHandle"] == "@github-radar"
        assert launched["evidence"]["hnPoints"] == 420
        assert plain["evidence"]["hnPoints"] is None

    def test_a_show_hn_only_repo_qualifies_on_its_star_history(self):
        posts = [_slot_show_hn("solo", 150, url="https://github.com/acme/solo")]
        result = _compose(posts, {"acme/solo": _stars(120)})
        assert _titles(result) == ["Show HN: solo"]
        assert result["chosen"][0]["slot"] == "repo"
        assert result["chosen"][0]["evidence"]["hnPoints"] == 150

    def test_the_show_hn_story_wins_over_a_stronger_unrelated_story(self):
        posts = [
            _slot_repo("tool"),
            _slot_show_hn("tool", 300, url="https://github.com/acme/tool"),
        ]
        evidence = {"acme/tool": _stars(500, hnPoints=900, hnComments=400)}

        chosen = _compose(posts, evidence)["chosen"][0]

        assert chosen["evidence"]["hnPoints"] == 300
        assert chosen["evidence"]["hnComments"] is None


def _slots(result) -> list[str]:
    return [chosen["slot"] for chosen in result["chosen"]]


def _with_two_repos(*others) -> tuple[list[dict], dict]:
    """Two qualifying repos, so a gated-out item cannot slip in as a top-up."""
    posts = [_slot_repo("r1"), _slot_repo("r2"), *others]
    return posts, {"acme/r1": _stars(500), "acme/r2": _stars(500)}


class TestRedditGate:
    """Ticket 06: ≥300 upvotes, ≥30 comments, and self-text or a GitHub link."""

    def test_the_floors_are_named(self):
        assert (
            daily_digest.REDDIT_MIN_UPVOTES,
            daily_digest.REDDIT_MIN_COMMENTS,
            daily_digest.REDDIT_MIN_BODY_CHARS,
        ) == (300, 30, 200)

    def test_a_thread_at_every_floor_takes_the_slot(self):
        result = _compose(*_with_two_repos(_slot_reddit("at", 300, comments=30)))
        assert _slots(result) == ["repo", "repo", "reddit"]

    def test_299_upvotes_is_below_the_floor(self):
        result = _compose(*_with_two_repos(_slot_reddit("low", 299, comments=30)))
        assert _slots(result) == ["repo", "repo"]

    def test_29_comments_is_below_the_floor(self):
        result = _compose(*_with_two_repos(_slot_reddit("quiet", 300, comments=29)))
        assert _slots(result) == ["repo", "repo"]

    def test_199_characters_of_self_text_is_not_enough(self):
        result = _compose(*_with_two_repos(_slot_reddit("short", 900, body="x" * 199)))
        assert _slots(result) == ["repo", "repo"]

    def test_a_github_repo_link_stands_in_for_self_text(self):
        thread = _slot_reddit("linked", 900, body="", link="https://github.com/acme/tool")
        result = _compose(*_with_two_repos(thread))
        assert _slots(result) == ["repo", "repo", "reddit"]

    def test_a_link_post_without_self_text_or_a_github_link_is_excluded(self):
        thread = _slot_reddit("article", 900, body="", link="https://example.com/news")
        result = _compose(*_with_two_repos(thread))
        assert _slots(result) == ["repo", "repo"]

    @pytest.mark.parametrize(
        "link",
        [
            "https://i.redd.it/560sypctfn3i1.png",
            "https://v.redd.it/a9idfrkmbs3i1",
            "https://www.reddit.com/gallery/1y5blyd",
        ],
    )
    def test_image_video_and_gallery_posts_are_excluded_even_with_text(self, link):
        thread = _slot_reddit("media", 2040, link=link)
        result = _compose(*_with_two_repos(thread))
        assert _slots(result) == ["repo", "repo"]

    def test_an_old_post_without_the_new_fields_fails_the_content_rule(self):
        thread = _slot_reddit("legacy", 2040, body=None, link=None)
        thread["project"]["description"] = "legacy " + "x" * 300
        result = _compose(*_with_two_repos(thread))
        assert _slots(result) == ["repo", "repo"]

    def test_the_strongest_thread_that_passes_takes_the_slot(self):
        posts, evidence = _with_two_repos(
            _slot_reddit("image", 2040, link="https://i.redd.it/x.png"),
            _slot_reddit("text", 814),
        )
        assert _titles(_compose(posts, evidence))[2:] == ["text"]


class TestHnGate:
    """Ticket 06 / Q30: a non-GitHub Show HN post, ≥100 points, ≥200 chars of story_text."""

    def test_the_floors_are_named(self):
        assert (daily_digest.HN_MIN_POINTS, daily_digest.HN_MIN_STORY_CHARS) == (100, 200)

    def test_a_self_text_post_at_both_floors_takes_the_slot(self):
        result = _compose(*_with_two_repos(_slot_show_hn("launch", 100)))
        assert _slots(result) == ["repo", "repo", "hn"]

    def test_99_points_is_below_the_floor(self):
        result = _compose(*_with_two_repos(_slot_show_hn("launch", 99)))
        assert _slots(result) == ["repo", "repo"]

    def test_a_url_post_has_no_story_text_and_fails(self):
        result = _compose(*_with_two_repos(_slot_show_hn("launch", 400, story_text=None)))
        assert _slots(result) == ["repo", "repo"]

    def test_199_characters_of_story_text_fails(self):
        result = _compose(
            *_with_two_repos(_slot_show_hn("launch", 400, story_text="x" * 199))
        )
        assert _slots(result) == ["repo", "repo"]

    def test_story_text_is_counted_after_unescaping_and_stripping_tags(self):
        # 199 visible characters: raw length is far over 200, plain text is not.
        escaped = "<p><i>" + "&#x27;" * 100 + "y" * 99 + "</i></p>"
        assert len(escaped) > 200
        short = _slot_show_hn("short", 400, story_text=escaped)
        result = _compose(*_with_two_repos(short))
        assert _slots(result) == ["repo", "repo"]

        enough = _slot_show_hn("enough", 400, story_text=escaped + "z")
        result = _compose(*_with_two_repos(enough))
        assert _slots(result) == ["repo", "repo", "hn"]


class TestVideoGate:
    """Ticket 06: ≥20,000 views from youtube_trends' allow-listed channels."""

    def test_the_floor_is_named(self):
        assert daily_digest.VIDEO_MIN_VIEWS == 20_000

    def test_a_video_at_the_floor_takes_the_slot(self):
        result = _compose(*_with_two_repos(_slot_video("video", 20_000)))
        assert _slots(result) == ["repo", "repo", "video"]

    def test_19_999_views_is_below_the_floor(self):
        result = _compose(*_with_two_repos(_slot_video("video", 19_999)))
        assert _slots(result) == ["repo", "repo"]

    def test_a_video_not_from_the_allow_listed_channel_scan_is_excluded(self):
        video = _slot_video("video", 90_000)
        video["agentHandle"] = "@hidden-gems"
        result = _compose(*_with_two_repos(video))
        assert _slots(result) == ["repo", "repo"]


class TestWeakDayAndTopUp:
    """An empty slot stays empty; the 2-item top-up never admits a content failure."""

    def test_on_a_weak_day_the_slot_stays_empty_and_no_other_kind_fills_it(self):
        posts, evidence = _with_two_repos(
            _slot_reddit("first", 2040),
            _slot_reddit("second", 1150),
            _slot_show_hn("weak", 40),
            _slot_video("weak-video", 900),
        )
        result = _compose(posts, evidence)
        assert _slots(result) == ["repo", "repo", "reddit"]
        assert result["belowFloor"] is False

    def test_the_top_up_ignores_engagement_floors_but_never_the_content_rule(self):
        posts = [
            _slot_reddit("image", 2040, link="https://i.redd.it/x.png"),
            _slot_reddit("gallery", 1500, link="https://www.reddit.com/gallery/abc"),
            _slot_reddit("legacy", 1200, body=None, link=None),
            _slot_reddit("small-text", 120, comments=5),
            _slot_show_hn("url-only", 900, story_text=None),
            _slot_video("small-video", 900),
        ]
        result = _compose(posts)
        assert _titles(result) == ["small-text", "small-video"]
        assert result["belowFloor"] is True

    def test_a_day_with_only_content_failures_sends_nothing(self):
        posts = [
            _slot_reddit("image", 2040, link="https://i.redd.it/x.png"),
            _slot_show_hn("url-only", 900, story_text=None),
        ]
        result = _compose(posts)
        assert result["chosen"] == []
        assert result["belowFloor"] is True


class TestDigestGatesRegression:
    """2026-11-26: the image posts that led the live digest are now excluded."""

    def _live_posts(self) -> list[dict]:
        with open(os.path.join(_TESTDATA, "daily-digest-2026-11-26.json")) as f:
            live = json.load(f)
        posts = [_post_from_digest_item(item) for item in live["items"]]
        for post in posts:
            if post["agentHandle"] != "@reddit-pulse":
                continue
            # As the writer now stores them: the live `what` was title + link.
            words = post["project"]["description"].split()
            link = words[-1] if words[-1].startswith("https://") else ""
            post["project"]["bodyText"] = ""
            post["project"]["contentHref"] = link
        return posts

    def test_the_image_posts_are_excluded_even_as_a_top_up(self):
        posts = self._live_posts()
        assert [p["project"]["contentHref"] for p in posts[:2]] == [
            "https://i.redd.it/560sypctfn3i1.png",
            "https://i.redd.it/a9idfrkmbs3i1.jpeg",
        ]
        # No repo evidence: the digest is below its 2-item floor.
        result = daily_digest.compose_daily_digest(
            posts, {}, featured=set(), now=SLOT_NOW
        )
        assert result["chosen"] == []
        assert result["belowFloor"] is True
