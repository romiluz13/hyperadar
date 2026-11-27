"""Tests for the daily digest generator.

Seam: the filter construction, Grove LLM call, and MongoDB storage in
`integrations/_shared/daily_digest.py`.
"""

import base64
import json
import os
import sys
import time
from datetime import datetime, timedelta, timezone

import httpx
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

_REAL_FETCH_README = daily_digest._fetch_github_readme
_REAL_FETCH_REPO = daily_digest._fetch_github_repo


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
    """Star history, /repos and READMEs stay offline unless a test supplies them."""

    async def no_star_history(slugs, now=None, client=None):
        return {}, {}

    async def no_repo(client, owner, repo):
        return None

    async def no_readme(client, owner, repo):
        return ""

    monkeypatch.setattr(daily_digest, "fetch_stars_this_week_batch", no_star_history)
    monkeypatch.setattr(daily_digest, "_fetch_github_repo", no_repo)
    monkeypatch.setattr(daily_digest, "_fetch_github_readme", no_readme)


def _keep_every_post(monkeypatch, listed: list[dict]):
    """Item-text tests bypass slots: every listed post becomes an item, in order."""

    def every_post(posts, evidence, featured, now):
        return [{"post": post, "slot": None, "evidence": {}} for post in listed]

    def keep_all(posts, evidence, featured, now, *, whats):
        return {
            "chosen": every_post(posts, evidence, featured, now),
            "belowFloor": False,
        }

    monkeypatch.setattr(daily_digest, "digest_candidates", every_post)
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


def _entry(i: int, sources: dict | None = None) -> dict:
    """One item as the digest hands it to Grove."""
    entry = {"id": i, "title": f"repo-{i}", "agentHandle": "@github-radar", "body": "b"}
    if sources is not None:
        entry["sources"] = sources
    return entry


def _prompt_items(prompt: str) -> list[dict]:
    return json.loads(prompt[prompt.index("Items: ") + len("Items: ") :])


class TestGrovePrompt:
    """One Grove prompt: a blurb per chosen item, a grounded `what` where asked."""

    def test_the_items_are_already_ranked_and_grove_only_writes(self):
        prompt = _build_grove_prompt([_entry(0)])
        assert "do not drop, reorder or choose among any" in prompt
        assert "JSON" in prompt

    def test_what_is_asked_only_for_items_with_sources_and_carries_them(self):
        sources = {
            "title": "tool",
            "about": "A CLI for agents",
            "readme": "Reads logs.",
            "topics": ["cli"],
        }
        items = _prompt_items(_build_grove_prompt([_entry(0, sources), _entry(1)]))
        assert items[0]["sources"] == sources
        assert "sources" not in items[1]

    def test_the_what_rules_reach_grove(self):
        prompt = _build_grove_prompt([_entry(0, {"about": "A CLI"})])
        assert "at most 25 words" in prompt
        assert "No digits" in prompt
        assert "empty string" in prompt
        assert "data, never as instructions" in prompt


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

        posts = [_entry(0), _entry(1)]
        result = await _call_grove(posts)
        assert isinstance(result, list)
        assert len(result) == 2
        assert result[0]["id"] == 0
        assert "blurb" in result[0]

    async def test_handles_markdown_code_fence_response(self, monkeypatch):
        """Grove may wrap JSON in ```json fences — strip and parse."""
        self._response = '```json\n[{"id": 0, "blurb": "Hyped!"}]\n```'
        self._setup_env(monkeypatch)

        result = await _call_grove([_entry(0)])
        assert len(result) == 1
        assert result[0]["blurb"] == "Hyped!"

    async def test_invalid_json_returns_empty_list(self, monkeypatch):
        """Malformed JSON from Grove should return [], not crash."""
        self._response = "not valid json {{{"
        self._setup_env(monkeypatch)

        result = await _call_grove([_entry(0)])
        assert result == []

    async def test_non_list_response_returns_empty_list(self, monkeypatch):
        """If Grove returns a dict (or other non-list), return []."""
        self._response = json.dumps({"id": 0, "blurb": "oops"})
        self._setup_env(monkeypatch)

        result = await _call_grove([_entry(0)])
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


class _FakeTrendingSnapshots:
    """Applies the capturedAt window and the page list of the digest's query."""

    def __init__(self):
        self.rows: list[dict] = []

    def find(self, query, projection=None):
        window, pages = query["capturedAt"], query["page"]["$in"]
        return _FakeCursor(
            [
                row
                for row in self.rows
                if window["$gte"] <= row["capturedAt"] < window["$lt"]
                and row["page"] in pages
            ]
        )


class _FakeDatabase:
    def __init__(self, posts):
        self.posts = _FakePosts(posts)
        self.digests = _FakeDigests()
        self.trending_snapshots = _FakeTrendingSnapshots()


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
    """Grove blurbs every item; its `what` is grounded only in ``_SUBSTANCE``."""

    async def fake_call_grove(posts):
        return [
            {"id": i, "blurb": f"blurb {i}", "what": _SUBSTANCE_WHAT}
            for i in range(len(posts))
        ]

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
        assert result["starHistoryFailed"] is False
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
        post["project"]["bodyText"] = _SUBSTANCE
        db = _FakeDatabase([post])

        async def fake_call_grove(posts):
            return [
                {
                    "id": 0,
                    "blurb": "A Reddit discussion is taking off.",
                    "what": _SUBSTANCE_WHAT,
                }
            ]

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

    async def test_a_star_history_outage_still_sends_the_non_repo_items(
        self, monkeypatch
    ):
        async def broken_batch(slugs, now=None, client=None):
            raise StarHistoryBatchFailed("star history failed for 2 of 2 repos")

        monkeypatch.setattr(daily_digest, "fetch_stars_this_week_batch", broken_batch)
        _blurb_every_post(monkeypatch)
        db = _FakeDatabase(
            [
                _slot_repo("a"),
                _slot_repo("b"),
                _slot_reddit("t", 900),
                _slot_video("v", 30000),
            ]
        )

        result = await daily_digest.generate_daily_digest(db=db, now=self.NOW)

        assert [item["kind"] for item in result["items"]] == ["thread", "video"]
        assert result["starHistoryFailed"] is True
        assert db.digests.upserted[1]["$set"]["starHistoryFailed"] is True

    async def test_any_other_evidence_failure_still_fails_the_digest(self, monkeypatch):
        async def broken_batch(slugs, now=None, client=None):
            raise KeyError("starsThisWeek")

        monkeypatch.setattr(daily_digest, "fetch_stars_this_week_batch", broken_batch)
        db = _FakeDatabase([_slot_repo("a"), _slot_reddit("t", 900)])

        with pytest.raises(KeyError):
            await daily_digest.generate_daily_digest(db=db, now=self.NOW)
        assert db.digests.upserted is None

    async def test_grove_picks_no_longer_decide_what_goes_out(self, monkeypatch):
        """Grove naming one id neither shrinks nor reorders the digest."""
        sample_posts = [
            _make_post(0, agent="@github-radar", rank_score=90),
            _slot_reddit("loud", 2109, comments=724),
            _slot_video("talk", 900),
        ]
        db = _FakeDatabase(sample_posts)

        async def fake_call_grove(posts):
            return [
                {"id": 0, "what": _SUBSTANCE_WHAT},
                {
                    "id": 1,
                    "blurb": "Grove blurb for the second item",
                    "what": _SUBSTANCE_WHAT,
                },
            ]

        monkeypatch.setattr(daily_digest, "_call_grove", fake_call_grove)

        result = await daily_digest.generate_daily_digest(db=db, now=self.NOW)

        # The repo has no star history; a below-floor video tops up to two.
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
            return [{"id": 0, "blurb": "grove blurb", "what": _SUBSTANCE_WHAT}]

        monkeypatch.setattr(daily_digest, "_call_grove", fake_call_grove)
        result = await daily_digest.generate_daily_digest(db=db, now=self.NOW)

        assert len(result["items"]) == 2
        assert result["items"][0]["blurb"] == "grove blurb"
        assert result["items"][1]["blurb"] == "B" * 100

    async def test_invalid_json_from_grove_still_sends_the_digest(self, monkeypatch):
        """When Grove returns nothing usable, the chosen items go out with body blurbs.

        A Reddit thread with no `what` left loses its slot (task 22).
        """
        _fake_star_history(monkeypatch, {"acme/r1": 500})
        posts = [
            _slot_repo("r1"),
            _slot_reddit("thread", 900),
            _slot_video("video", 30000),
        ]

        async def fake_call_grove(posts):
            return []

        monkeypatch.setattr(daily_digest, "_call_grove", fake_call_grove)
        result = await daily_digest.generate_daily_digest(
            db=_FakeDatabase(posts), now=self.NOW
        )

        assert [item["title"] for item in result["items"]] == ["r1", "video"]
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


_OPENSRE_ABOUT = "The open-source agent for CI/CD reliability."
_OPENSRE_README = (
    "Your CI tells you it broke, but not why or how to fix it. OpenSRE is a CLI "
    "agent that reads the failed run, traces the cause and pushes a fix for you "
    "to review."
)
_OPENSRE_WHAT = (
    "Open-source CLI agent that reads a failed CI/CD run, traces the cause and "
    "pushes a fix for review."
)
_BODY = (
    "I built a local tracer that records every tool call an agent makes and "
    "replays the run step by step, so you can see where the plan went wrong. "
    "It runs offline and keeps the traces in a folder you own."
)


def _repo_post(name: str, about: str = "", topics: list[str] | None = None) -> dict:
    post = _slot_repo(name)
    post["project"]["description"] = about
    post["project"]["topics"] = topics or []
    return post


def _fake_github(monkeypatch, repos=None, readmes=None, readme_calls=None):
    """/repos and README answers keyed by lowercase owner/repo; others unknown."""

    async def fake_repo(client, owner, repo):
        return (repos or {}).get(f"{owner}/{repo}".lower())

    async def fake_readme(client, owner, repo):
        if readme_calls is not None:
            readme_calls.append(f"{owner}/{repo}".lower())
        return (readmes or {}).get(f"{owner}/{repo}".lower(), "")

    monkeypatch.setattr(daily_digest, "_fetch_github_repo", fake_repo)
    monkeypatch.setattr(daily_digest, "_fetch_github_readme", fake_readme)


def _grove_whats(monkeypatch, whats: dict, seen: list | None = None):
    """Grove blurbs every item and answers `what` for the ids in ``whats``."""

    async def fake_call_grove(entries):
        if seen is not None:
            seen.extend(entries)
        return [
            {"id": e["id"], "blurb": f"blurb {e['id']}"}
            | ({"what": whats[e["id"]]} if e["id"] in whats else {})
            for e in entries
        ]

    monkeypatch.setattr(daily_digest, "_call_grove", fake_call_grove)


class TestDigestItemWhat:
    """`what`: Grove's grounded sentence, else the About line, else null."""

    NOW = datetime(2026, 7, 23, 10, 0, 0, tzinfo=timezone.utc)

    async def _digest(self, monkeypatch, posts):
        _keep_every_post(monkeypatch, posts)
        return await daily_digest.generate_daily_digest(
            db=_FakeDatabase(posts), now=self.NOW
        )

    async def test_a_grounded_grove_sentence_is_the_repos_what(self, monkeypatch):
        _fake_github(monkeypatch, readmes={"acme/opensre": _OPENSRE_README})
        seen: list = []
        _grove_whats(monkeypatch, {0: _OPENSRE_WHAT}, seen)

        result = await self._digest(
            monkeypatch, [_repo_post("opensre", _OPENSRE_ABOUT)]
        )

        assert result["items"][0]["what"] == _OPENSRE_WHAT
        assert result["whatRejections"] == 0
        assert seen[0]["sources"]["about"] == _OPENSRE_ABOUT
        assert seen[0]["sources"]["readme"] == _OPENSRE_README

    async def test_an_ungrounded_sentence_falls_back_to_the_about_line(
        self, monkeypatch
    ):
        _fake_github(monkeypatch, readmes={"acme/opensre": _OPENSRE_README})
        _grove_whats(
            monkeypatch, {0: "Open-source CLI agent that saves your team hours."}
        )

        result = await self._digest(
            monkeypatch,
            [_repo_post("opensre", "  The open-source agent\nfor CI/CD reliability. ")],
        )

        assert result["items"][0]["what"] == _OPENSRE_ABOUT
        assert result["whatRejections"] == 1

    async def test_a_long_about_line_is_cut_at_a_word_boundary_within_140(
        self, monkeypatch
    ):
        about = " ".join(["filler"] * 19) + " and " + "x" * 40
        _grove_whats(monkeypatch, {})

        result = await self._digest(monkeypatch, [_repo_post("long", about)])

        assert result["items"][0]["what"] == " ".join(["filler"] * 19)

    async def test_a_number_absent_from_the_sources_is_rejected(self, monkeypatch):
        _grove_whats(monkeypatch, {0: "The open-source agent with 12,113 stars."})

        result = await self._digest(
            monkeypatch, [_repo_post("opensre", _OPENSRE_ABOUT)]
        )

        assert result["items"][0]["what"] == _OPENSRE_ABOUT
        assert result["whatRejections"] == 1

    async def test_a_grove_failure_falls_back_to_about_then_null_and_is_counted(
        self, monkeypatch
    ):
        async def broken_grove(_entries):
            raise RuntimeError("grove down")

        monkeypatch.setattr(daily_digest, "_call_grove", broken_grove)

        result = await self._digest(
            monkeypatch, [_repo_post("opensre", _OPENSRE_ABOUT), _repo_post("bare")]
        )

        assert [item["what"] for item in result["items"]] == [_OPENSRE_ABOUT, None]
        assert result["whatRejections"] == 2

    async def test_github_s_about_line_stands_in_for_an_empty_description(
        self, monkeypatch
    ):
        _fake_github(
            monkeypatch,
            repos={"acme/opensre": {"description": _OPENSRE_ABOUT, "topics": ["sre"]}},
        )
        seen: list = []
        _grove_whats(monkeypatch, {0: ""}, seen)

        result = await self._digest(monkeypatch, [_repo_post("opensre")])

        assert result["items"][0]["what"] == _OPENSRE_ABOUT
        assert seen[0]["sources"]["topics"] == ["sre"]

    async def test_the_readme_is_fetched_only_for_the_chosen_repos(self, monkeypatch):
        names = ("a", "b", "c", "d", "slow")
        weekly = {"acme/a": 900, "acme/b": 800, "acme/c": 700, "acme/d": 600}
        _fake_star_history(monkeypatch, {**weekly, "acme/slow": 40})
        calls: list = []
        _fake_github(monkeypatch, readme_calls=calls)
        _grove_whats(monkeypatch, {})

        result = await daily_digest.generate_daily_digest(
            db=_FakeDatabase([_repo_post(n) for n in names]), now=self.NOW
        )

        assert len(result["items"]) == 3
        assert sorted(calls) == ["acme/a", "acme/b", "acme/c"]

    async def test_a_reddit_body_is_summarised_under_the_same_check(self, monkeypatch):
        threads = [
            _slot_reddit("tracer", 900, body=_BODY),
            _slot_reddit("other", 900, body=_BODY),
        ]
        seen: list = []
        _grove_whats(
            monkeypatch,
            {
                0: "A local tracer that records every tool call an agent makes and replays the run.",
                1: "A cloud tracer for agents.",
            },
            seen,
        )

        result = await self._digest(monkeypatch, threads)

        assert [item["what"] for item in result["items"]] == [
            "A local tracer that records every tool call an agent makes and replays the run.",
            None,
        ]
        assert seen[0]["sources"]["text"] == _BODY
        assert result["whatRejections"] == 1

    async def test_a_reddit_link_to_a_github_repo_takes_that_repos_what(
        self, monkeypatch
    ):
        thread = _slot_reddit(
            "launch", 900, body="", link="https://github.com/acme/opensre"
        )
        _fake_github(
            monkeypatch,
            repos={"acme/opensre": {"description": _OPENSRE_ABOUT}},
            readmes={"acme/opensre": _OPENSRE_README},
        )
        seen: list = []
        _grove_whats(monkeypatch, {0: _OPENSRE_WHAT}, seen)

        result = await self._digest(monkeypatch, [thread])

        assert result["items"][0]["what"] == _OPENSRE_WHAT
        assert seen[0]["sources"]["readme"] == _OPENSRE_README

    async def test_a_reddit_body_summary_rejected_falls_back_to_the_linked_repo(
        self, monkeypatch
    ):
        thread = _slot_reddit(
            "launch", 900, body=_BODY, link="https://github.com/acme/opensre"
        )
        _fake_github(
            monkeypatch, repos={"acme/opensre": {"description": _OPENSRE_ABOUT}}
        )
        _grove_whats(monkeypatch, {0: "A cloud tracer for agents."})

        result = await self._digest(monkeypatch, [thread])

        assert result["items"][0]["what"] == _OPENSRE_ABOUT
        assert result["whatRejections"] == 1

    async def test_an_hn_story_text_is_summarised(self, monkeypatch):
        story = _slot_show_hn("tracer", 150, story_text=f"<p>{_BODY}</p>")
        _grove_whats(
            monkeypatch, {0: "A local tracer that replays the run step by step."}
        )

        result = await self._digest(monkeypatch, [story])

        assert (
            result["items"][0]["what"]
            == "A local tracer that replays the run step by step."
        )

    async def test_a_video_has_no_what_and_grove_is_not_asked_for_one(
        self, monkeypatch
    ):
        seen: list = []
        _grove_whats(monkeypatch, {0: "Invented for a video"}, seen)

        result = await self._digest(monkeypatch, [_slot_video("talk", 30000)])

        assert result["items"][0]["what"] is None
        assert "sources" not in seen[0]
        assert result["whatRejections"] == 0


def _trending_row(slug: str, rank: int, page: str, captured_at: datetime) -> dict:
    """A trending_snapshots document as github_radar writes it."""
    return {
        "date": captured_at.date().isoformat(),
        "page": page,
        "rank": rank,
        "slug": slug,
        "windowStars": 100,
        "description": "",
        "language": None,
        "capturedAt": captured_at,
    }


class TestItemStats:
    """Numbers RomBot may print come only from these fields."""

    NOW = datetime(2026, 7, 23, 10, 0, 0, tzinfo=timezone.utc)

    async def _items(self, monkeypatch, posts, trending=()):
        _blurb_every_post(monkeypatch)
        db = _FakeDatabase(posts)
        db.trending_snapshots.rows = list(trending)
        result = await daily_digest.generate_daily_digest(db=db, now=self.NOW)
        return {item["title"]: item for item in result["items"]}

    async def test_a_repo_carries_its_star_history_and_weekly_gain(self, monkeypatch):
        _fake_star_history(monkeypatch, {"acme/tool": 500})

        items = await self._items(monkeypatch, [_slot_repo("tool")])

        tool = items["tool"]
        assert (tool["starsThisWeek"], tool["starsToday"]) == (500, 30)
        assert (tool["weeklyGain"], tool["weeklyGainWindowDays"]) == (500, 7)
        assert (tool["upvotes"], tool["comments"]) == (None, None)
        assert (tool["trendingRank"], tool["trendingSince"]) == (None, None)

    async def test_a_reddit_thread_carries_upvotes_and_comments(self, monkeypatch):
        items = await self._items(
            monkeypatch,
            [_slot_reddit("thread", 900, comments=64), _slot_video("v", 30000)],
        )

        thread = items["thread"]
        assert (thread["upvotes"], thread["comments"]) == (900, 64)
        assert (thread["starsThisWeek"], thread["starsToday"]) == (None, None)
        assert (thread["weeklyGain"], thread["weeklyGainWindowDays"]) == (None, None)
        assert (items["v"]["upvotes"], items["v"]["comments"]) == (None, None)

    async def test_the_trending_rank_comes_only_from_today_s_all_language_parse(
        self, monkeypatch
    ):
        _fake_star_history(
            monkeypatch, {"acme/tool": 500, "acme/other": 400, "acme/third": 300}
        )
        today = self.NOW.replace(hour=6)
        yesterday = today - timedelta(days=1)
        rows = [
            _trending_row("Acme/Tool", 4, "all/daily", today),
            _trending_row("acme/tool", 1, "all/weekly", today),
            _trending_row("acme/other", 2, "all/weekly", today),
            _trending_row("acme/other", 1, "all/daily", yesterday),
            _trending_row("acme/third", 1, "python/daily", today),
        ]

        items = await self._items(
            monkeypatch, [_slot_repo(n) for n in ("tool", "other", "third")], rows
        )

        assert (items["tool"]["trendingRank"], items["tool"]["trendingSince"]) == (
            4,
            "daily",
        )
        assert (items["other"]["trendingRank"], items["other"]["trendingSince"]) == (
            2,
            "weekly",
        )
        assert (items["third"]["trendingRank"], items["third"]["trendingSince"]) == (
            None,
            None,
        )

    async def test_a_later_parse_the_same_day_replaces_an_earlier_one(
        self, monkeypatch
    ):
        _fake_star_history(monkeypatch, {"acme/tool": 500, "acme/gone": 400})
        early, late = self.NOW.replace(hour=1), self.NOW.replace(hour=6)
        rows = [
            _trending_row("acme/tool", 9, "all/daily", early),
            _trending_row("acme/gone", 1, "all/daily", early),
            _trending_row("acme/tool", 4, "all/daily", late),
        ]

        items = await self._items(
            monkeypatch, [_slot_repo("tool"), _slot_repo("gone")], rows
        )

        assert items["tool"]["trendingRank"] == 4
        assert items["gone"]["trendingRank"] is None

    async def test_every_item_gets_a_topic_emoji(self, monkeypatch):
        _fake_star_history(monkeypatch, {"acme/tool": 500, "acme/plain": 400})
        tool = _repo_post("tool", "A tool", topics=["llm", "agents"])
        plain = _repo_post("plain", "Notes and whiteboards")

        items = await self._items(
            monkeypatch, [tool, plain, _slot_reddit("thread", 900)]
        )

        assert items["tool"]["emoji"] == "🤖"
        assert items["plain"]["emoji"] == "📦"
        assert items["thread"]["emoji"] == "📦"


_KIND_EMOJI = {"repo": "📦", "video": "🎥", "thread": "💬"}
_STAT = {
    "github_stars": "⭐ {}",
    "hn_points": "{} נק׳ ב-HN",
    "upvotes": "⬆ {} הצבעות",
    "views": "👁 {} צפיות",
}


def _render_unpatched_rombot(payload: dict) -> list[tuple[str, str]]:
    """Each item's two lines under today's RomBot SKILL.md rules, before any
    translation: the first 4 items, the kind emoji, `what` (or the repo
    placeholder), the stat by `signalMetric` and growth from `weeklyGain`."""
    blocks = []
    for item in payload["items"][:4]:
        line = f"• {_KIND_EMOJI.get(item['kind'], '📌')} *{item['title'].replace('*', '')}*"
        if item.get("what"):
            line += f" — {item['what']}"
        elif item["kind"] == "repo":
            line += " — אין תיאור זמין"
        metric = item.get("signalMetric")
        parts = [_STAT[metric].format(item["signalValue"])] if metric in _STAT else []
        gain = item.get("weeklyGain")
        if (
            metric == "github_stars"
            and isinstance(gain, (int, float))
            and gain > 0
            and item.get("weeklyGainWindowDays") in (7, 8)
        ):
            parts.append(f"{gain} חדשים בשבוע")
        hn = item.get("hnPoints")
        if metric != "hn_points" and isinstance(hn, (int, float)) and hn >= 20:
            parts.append(f"{hn} נק׳ ב-HN")
        parts.append(f"🔗 {item['url']}")
        blocks.append((line, " · ".join(parts)))
    return blocks


class TestUnpatchedRomBotRender:
    """HypeRadar ships before the RomBot patch, so today's rules must work."""

    NOW = datetime(2026, 7, 23, 10, 0, 0, tzinfo=timezone.utc)

    async def test_three_repos_lead_with_a_what_and_growth(self, monkeypatch):
        names = ("opensre", "tool", "kit")
        _fake_star_history(
            monkeypatch, {"acme/opensre": 900, "acme/tool": 700, "acme/kit": 500}
        )
        _fake_github(monkeypatch, readmes={"acme/opensre": _OPENSRE_README})
        _grove_whats(
            monkeypatch,
            {
                0: _OPENSRE_WHAT,
                1: "An invented engine.",
                3: "A local tracer that replays the run step by step.",
            },
        )
        posts = [
            _repo_post("opensre", _OPENSRE_ABOUT),
            _repo_post("tool", "A CLI that turns logs into timelines"),
            _repo_post("kit", "Agent kit for local models"),
            _slot_reddit("thread", 900, body=_BODY),
            _slot_video("talk", 30000),
        ]

        digest = await daily_digest.generate_daily_digest(
            db=_FakeDatabase(posts), now=self.NOW
        )
        payload = json.loads(json.dumps(digest, default=str))
        blocks = _render_unpatched_rombot(payload)

        assert len(blocks) == 4
        assert [line.split(" *")[0] for line, _ in blocks] == ["• 📦"] * 3 + ["• 💬"]
        assert [line.split("*")[1] for line, _ in blocks[:3]] == list(names)
        for line, stat in blocks[:3]:
            assert " — " in line and "אין תיאור זמין" not in line
            assert "חדשים בשבוע" in stat
        assert blocks[0][0].endswith(_OPENSRE_WHAT)


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

        result = _compose(posts, evidence)

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


# On topic, so the Show HN scope rule passes unless a test says otherwise: two
# distinct keywords, neither of which maps to a topic emoji.
_SUBSTANCE = ("coding programming " * 11)[:200]
_SUBSTANCE_WHAT = "Coding."


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


def _compose(posts, evidence=None, featured=None, whats=None):
    """Every non-repo post has a final `what` unless ``whats`` says otherwise."""
    described = {post["project"]["url"]: "A described item." for post in posts}
    return daily_digest.compose_daily_digest(
        posts,
        evidence or {},
        featured=featured or set(),
        now=SLOT_NOW,
        whats={**described, **(whats or {})},
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

    def test_the_top_up_never_takes_a_second_item_of_one_kind(self):
        posts = [
            _slot_repo("slow"),
            _slot_reddit("first", 2040),
            _slot_reddit("second", 1150),
        ]
        result = _compose(posts, {"acme/slow": _stars(60)})
        assert _titles(result) == ["first"]
        assert result["belowFloor"] is True

    def test_the_top_up_takes_another_kind_when_one_exists(self):
        posts = [
            _slot_repo("slow"),
            _slot_reddit("first", 2040),
            _slot_reddit("second", 1150),
            _slot_video("small-video", 900),
        ]
        result = _compose(posts, {"acme/slow": _stars(60)})
        assert _titles(result) == ["first", "small-video"]
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


class TestRepoScopeRule:
    """A repo no AI or dev-tools gate has passed must pass the scope keywords."""

    def _hidden_gem(self, name: str, about: str) -> dict:
        url = f"https://github.com/acme/{name}"
        post = _make_post(
            0,
            agent="@hidden-gems",
            title=name,
            url=url,
            description=about,
            signal={"source": "github", "metric": "github_stars", "value": 800},
        )
        # hidden-gems stamps every post with these labels (hidden_gems/source.py).
        post["project"]["topics"] = ["hn", "hidden-gem", "ai"]
        return post

    def test_an_off_topic_repo_with_500_stars_this_week_is_excluded(self):
        posts = [
            self._hidden_gem("grandmas-recipes", "Family recipes, one per page"),
            self._hidden_gem("tracer", "Replays every tool call an agent makes"),
        ]
        evidence = {"acme/grandmas-recipes": _stars(900), "acme/tracer": _stars(500)}
        assert _titles(_compose(posts, evidence)) == ["tracer"]

    def test_an_off_topic_show_hn_repo_is_excluded(self):
        post = _slot_show_hn(
            "synth-game",
            300,
            url="https://github.com/acme/synth",
            story_text=_SYNTH_STORY,
        )
        assert _titles(_compose([post], {"acme/synth": _stars(500)})) == []

    def test_github_s_about_line_and_topics_count(self):
        posts = [self._hidden_gem("plain", "Fast and small")]
        evidence = {"acme/plain": _stars(500, about="", topics=["llm"])}
        assert _titles(_compose(posts, evidence)) == ["plain"]

    def test_a_github_radar_repo_already_passed_its_scope_filter(self):
        post = _slot_repo("app-store-compliance")
        post["project"]["description"] = "Open playbook for App Store rejections."
        result = _compose([post], {"acme/app-store-compliance": _stars(500)})
        assert _titles(result) == ["app-store-compliance"]


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
        short = _slot_show_hn("short agent", 400, story_text=escaped)
        result = _compose(*_with_two_repos(short))
        assert _slots(result) == ["repo", "repo"]

        enough = _slot_show_hn("enough agent", 400, story_text=escaped + "z")
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
        result = _compose(posts)
        assert result["chosen"] == []
        assert result["belowFloor"] is True


class TestNonRepoVerdictRule:
    """Task 22: an `inflated` or `cooling` non-repo post never takes a slot."""

    def test_the_excluded_verdicts_are_named(self):
        assert daily_digest.NON_REPO_EXCLUDED_VERDICTS == {"inflated", "cooling"}

    @pytest.mark.parametrize("verdict", ["inflated", "cooling"])
    def test_a_high_engagement_thread_with_a_doubted_verdict_loses_its_slot(
        self, verdict
    ):
        doubted = _slot_reddit("doubted", 2040, comments=480)
        doubted["verdict"] = verdict
        result = _compose(*_with_two_repos(doubted, _slot_reddit("believed", 400)))
        assert _titles(result)[2:] == ["believed"]

    def test_an_emerging_thread_takes_the_slot(self):
        thread = _slot_reddit("emerging", 900)
        thread["verdict"] = "emerging"
        result = _compose(*_with_two_repos(thread))
        assert _slots(result) == ["repo", "repo", "reddit"]

    def test_the_top_up_never_admits_a_doubted_verdict(self):
        inflated = _slot_reddit("inflated", 2040)
        inflated["verdict"] = "inflated"
        cooling = _slot_video("cooling-video", 900)
        cooling["verdict"] = "cooling"
        posts = [inflated, cooling, _slot_reddit("small", 120, comments=5)]
        result = _compose(posts)
        assert _titles(result) == ["small"]
        assert result["belowFloor"] is True


# 2026-11-27 dry run of 4726c55: one passing mention of Claude took the slot.
_SYNTH_STORY = (
    "With the help of our friend Claude, I put together a game that runs "
    "through exercises where you learn as you build patches on a modular rack. "
    "Each level gives you oscillators, filters and envelopes, and you wire them "
    "until the sound matches the target patch."
)
_AGENT_STORY = (
    "I made a local tracer that records every tool call an agent makes and "
    "replays the run step by step, so you can see where the plan went wrong. "
    "It runs offline and keeps the traces in a folder you own on your laptop."
)


class TestShowHnTopicRule:
    """Task 22: a Show HN post must be about AI or dev tools (scope keywords)."""

    def test_an_off_topic_show_hn_with_points_and_text_is_excluded(self):
        game = _slot_show_hn("Synth Patch Hero", 125, story_text=_SYNTH_STORY)
        result = _compose(*_with_two_repos(game))
        assert _slots(result) == ["repo", "repo"]

    def test_an_ai_show_hn_with_points_and_text_takes_the_slot(self):
        tracer = _slot_show_hn("Agent replay", 125, story_text=_AGENT_STORY)
        result = _compose(*_with_two_repos(tracer))
        assert _slots(result) == ["repo", "repo", "hn"]

    def test_a_scope_keyword_in_the_title_alone_is_enough(self):
        cli = _slot_show_hn("A CLI for synth patches", 125, story_text=_SYNTH_STORY)
        result = _compose(*_with_two_repos(cli))
        assert _slots(result) == ["repo", "repo", "hn"]

    def test_the_top_up_never_admits_an_off_topic_show_hn(self):
        game = _slot_show_hn("Synth Patch Hero", 900, story_text=_SYNTH_STORY)
        result = _compose([game, _slot_video("small-video", 900)])
        assert _titles(result) == ["small-video"]


def _undescribed(*posts) -> dict:
    """The final `what` of these posts is null: Grove's was rejected, no fallback."""
    return {post["project"]["url"]: None for post in posts}


class TestNonRepoDescribedRule:
    """Task 22: a Reddit or HN item takes a slot only with a final `what`."""

    def test_an_undescribed_thread_gives_way_to_the_next_of_its_kind(self):
        bare = _slot_reddit("bare", 2040)
        posts, evidence = _with_two_repos(bare, _slot_reddit("described", 400))
        result = _compose(posts, evidence, whats=_undescribed(bare))
        assert _titles(result)[2:] == ["described"]

    def test_with_no_described_candidate_left_the_slot_stays_empty(self):
        bare = _slot_show_hn("bare agent", 400)
        thread = _slot_reddit("thread", 900)
        posts, evidence = _with_two_repos(bare, thread)
        result = _compose(posts, evidence, whats=_undescribed(bare))
        assert _slots(result) == ["repo", "repo", "reddit"]

    def test_only_three_candidates_per_kind_are_tried(self):
        bare = [_slot_reddit(f"bare-{n}", 2000 - n) for n in range(3)]
        fourth = _slot_reddit("fourth", 500)
        posts, evidence = _with_two_repos(*bare, fourth)
        result = _compose(posts, evidence, whats=_undescribed(*bare))
        assert _slots(result) == ["repo", "repo"]

    def test_the_top_up_never_admits_an_undescribed_item(self):
        bare = _slot_reddit("bare", 120, comments=5)
        posts = [bare, _slot_reddit("small", 110, comments=5), _slot_video("v", 900)]
        result = _compose(posts, whats=_undescribed(bare))
        assert _titles(result) == ["small", "v"]
        assert result["belowFloor"] is True

    def test_a_video_needs_no_what(self):
        result = _compose(*_with_two_repos(_slot_video("talk", 40_000)), whats={})
        assert _slots(result) == ["repo", "repo", "video"]


class TestDescribedSlotThroughGrove:
    """The one batched Grove call: top candidates per kind, first validated wins."""

    async def test_a_rejected_what_frees_the_slot_for_the_next_thread(
        self, monkeypatch
    ):
        _fake_star_history(monkeypatch, {"acme/r1": 500, "acme/r2": 500})
        first = _slot_reddit("first", 2040, body=_BODY)
        second = _slot_reddit("second", 900, body=_BODY)
        seen: list = []

        async def fake_call_grove(entries):
            seen.append([e["title"] for e in entries])
            whats = {
                "first": "A cloud tracer for agents.",
                "second": "A local tracer that replays the run step by step.",
            }
            return [
                {"id": e["id"], "blurb": "b", "what": whats.get(e["title"], "")}
                for e in entries
            ]

        monkeypatch.setattr(daily_digest, "_call_grove", fake_call_grove)
        db = _FakeDatabase([_slot_repo("r1"), _slot_repo("r2"), first, second])

        result = await daily_digest.generate_daily_digest(db=db, now=SLOT_NOW)

        assert len(seen) == 1
        assert sorted(seen[0]) == ["first", "r1", "r2", "second"]
        assert [item["title"] for item in result["items"]] == ["r1", "r2", "second"]
        assert result["items"][2]["what"] == (
            "A local tracer that replays the run step by step."
        )

    async def test_grove_sees_at_most_three_candidates_per_kind(self, monkeypatch):
        _fake_star_history(monkeypatch, {"acme/r1": 500, "acme/r2": 500})
        threads = [_slot_reddit(f"t{n}", 2000 - n, body=_BODY) for n in range(5)]
        seen: list = []

        async def fake_call_grove(entries):
            seen.extend(e["title"] for e in entries)
            return []

        monkeypatch.setattr(daily_digest, "_call_grove", fake_call_grove)
        db = _FakeDatabase([_slot_repo("r1"), _slot_repo("r2"), *threads])

        result = await daily_digest.generate_daily_digest(db=db, now=SLOT_NOW)

        assert sorted(seen) == ["r1", "r2", "t0", "t1", "t2"]
        assert [item["title"] for item in result["items"]] == ["r1", "r2"]


class TestDryRunNonRepoSlotsRegression:
    """2026-11-27 dry run of 71edcdd: slot 4 was an `inflated` r/singularity
    opinion thread with no `what`, slot 5 an off-topic Show HN synth game."""

    async def test_neither_reaches_the_digest_and_the_three_repos_still_lead(
        self, monkeypatch
    ):
        repos = {
            "Claude-BugHunter": (
                11892,
                "A Claude Code skill bundle for bug hunting and external red-team work.",
            ),
            "ffmpeg-skill": (
                3179,
                "A local video editor for coding agents with FFmpeg tools and an MCP server.",
            ),
            "app-store-compliance": (
                5224,
                "Open playbook and pre-submission guard for App Store and Google Play rejections.",
            ),
        }
        _fake_star_history(
            monkeypatch,
            {f"acme/{name}".lower(): weekly for name, (weekly, _) in repos.items()},
        )
        opinion = _slot_reddit(
            "I can't believe it's really happening",
            703,
            comments=477,
            body=(
                "Five years ago people laughed when anyone said this would arrive "
                "in our lifetime. Now it feels like every week something that "
                "sounded impossible just ships, and nobody around me seems to "
                "notice how fast it is all moving. I honestly can't believe it."
            ),
        )
        opinion["project"]["url"] = (
            "https://www.reddit.com/r/singularity/comments/1y67koi"
        )
        opinion["verdict"] = "inflated"
        game = _slot_show_hn("Synth Patch Hero", 125, story_text=_SYNTH_STORY)
        game["project"]["url"] = "https://swarkentin.github.io/synth-patch-hero/"
        game["verdict"] = "emerging"
        posts = [_repo_post(name, about) for name, (_, about) in repos.items()]
        seen: list = []

        async def fake_call_grove(entries):
            seen.extend(e["title"] for e in entries)
            return [
                {"id": e["id"], "blurb": "b", "what": ""}
                if e["agentHandle"] == "@reddit-pulse"
                else {"id": e["id"], "blurb": "b"}
                for e in entries
            ]

        monkeypatch.setattr(daily_digest, "_call_grove", fake_call_grove)

        result = await daily_digest.generate_daily_digest(
            db=_FakeDatabase([*posts, opinion, game]), now=SLOT_NOW
        )

        assert [item["title"] for item in result["items"]] == [
            "Claude-BugHunter",
            "app-store-compliance",
            "ffmpeg-skill",
        ]
        assert all(item["what"] for item in result["items"])
        assert sorted(seen) == sorted(repos)


def _readme_client(markdown: str) -> httpx.AsyncClient:
    content = base64.b64encode(markdown.encode()).decode()
    return httpx.AsyncClient(
        transport=httpx.MockTransport(
            lambda request: httpx.Response(200, json={"content": content})
        )
    )


# Each run is quadratic in a backtracking engine unless the text is bounded and
# the patterns stop at the next opener.
_ADVERSARIAL = ("[" * 70_000 + "![" * 35_000 + "<" * 60_000)[:200_000]


class TestAdversarialTextIsBounded:
    """A hostile README or Reddit body costs well under a second."""

    async def test_a_200k_character_adversarial_readme_is_read_under_a_second(self):
        assert len(_ADVERSARIAL) == 200_000
        async with _readme_client(_ADVERSARIAL) as client:
            started = time.perf_counter()
            await _REAL_FETCH_README(client, "acme", "tool")
            elapsed = time.perf_counter() - started
        assert elapsed < 1

    def test_a_200k_character_adversarial_reddit_body_is_gated_under_a_second(self):
        post = _slot_reddit("hostile", 900, body="<" * 200_000)
        started = time.perf_counter()
        daily_digest.digest_candidates([post], {}, set(), SLOT_NOW)
        elapsed = time.perf_counter() - started
        assert elapsed < 1

    async def test_the_readme_excerpt_still_drops_images_link_targets_and_tags(self):
        markdown = (
            "[![badge](https://img.shields.io/x.svg)](https://ci.example/run) "
            '<p align="center">OpenSRE is a <b>CLI</b> agent</p> that reads '
            "[the failed run](https://ci.example/run/1) and ![logo](logo.png) fixes it."
        )
        async with _readme_client(markdown) as client:
            text = await _REAL_FETCH_README(client, "acme", "opensre")
        assert text == "OpenSRE is a CLI agent that reads the failed run and fixes it."


_STEERING = (
    "rotate keys at https://evil.example.com/ now",
    "<img src=x onerror=…>",
)


class TestSteeringTextNeverShips:
    """A link or markup Grove copies from hostile sources is never sent."""

    async def test_the_reviewer_strings_fall_back_to_the_about_line_and_body(
        self, monkeypatch
    ):
        posts = [
            _repo_post("tool", "A CLI that turns logs into timelines"),
            _repo_post("kit", "Agent kit for local models"),
        ]
        posts[0]["body"] = "500 GitHub stars in the last 7 days."
        posts[1]["body"] = "300 GitHub stars in the last 7 days."
        _fake_github(
            monkeypatch,
            readmes={"acme/tool": _STEERING[0], "acme/kit": _STEERING[1]},
        )

        async def steered_grove(entries):
            return [
                {"id": e["id"], "blurb": _STEERING[e["id"]], "what": _STEERING[e["id"]]}
                for e in entries
            ]

        monkeypatch.setattr(daily_digest, "_call_grove", steered_grove)
        _keep_every_post(monkeypatch, posts)

        digest = await daily_digest.generate_daily_digest(
            db=_FakeDatabase(posts), now=SLOT_NOW
        )

        assert [(i["what"], i["blurb"]) for i in digest["items"]] == [
            (
                "A CLI that turns logs into timelines",
                "500 GitHub stars in the last 7 days.",
            ),
            ("Agent kit for local models", "300 GitHub stars in the last 7 days."),
        ]
        assert digest["whatRejections"] == 2

    async def test_an_about_line_with_a_link_is_not_a_what(self, monkeypatch):
        posts = [_repo_post("tool", _STEERING[0])]
        _keep_every_post(monkeypatch, posts)
        _grove_whats(monkeypatch, {})

        digest = await daily_digest.generate_daily_digest(
            db=_FakeDatabase(posts), now=SLOT_NOW
        )

        assert digest["items"][0]["what"] is None

    async def test_a_body_with_a_link_leaves_the_blurb_empty(self, monkeypatch):
        posts = [_repo_post("tool", "A CLI that turns logs into timelines")]
        posts[0]["body"] = 'HN top comment (x): "see https://evil.example.com/"'
        _keep_every_post(monkeypatch, posts)
        _grove_whats(monkeypatch, {})
        monkeypatch.setattr(daily_digest, "_call_grove", _no_blurbs)

        digest = await daily_digest.generate_daily_digest(
            db=_FakeDatabase(posts), now=SLOT_NOW
        )

        assert digest["items"][0]["blurb"] == ""


async def _no_blurbs(entries):
    return []


class TestGitHubApiPaths:
    """Only a plain owner and repo name reach api.github.com."""

    @pytest.mark.parametrize(
        "owner,repo",
        [("..", "x"), ("acme", ".."), (".", "tool"), ("acme", "a/b"), ("", "x")],
    )
    async def test_a_traversal_segment_never_becomes_a_request(self, owner, repo):
        requests = []

        def record(request):
            requests.append(request)
            return httpx.Response(200, json={"content": ""})

        async with httpx.AsyncClient(transport=httpx.MockTransport(record)) as client:
            assert await _REAL_FETCH_REPO(client, owner, repo) is None
            assert await _REAL_FETCH_README(client, owner, repo) == ""
        assert requests == []

    def test_a_repo_url_with_a_dot_git_segment_is_no_candidate(self):
        post = _slot_repo("x")
        post["project"]["url"] = "https://github.com/acme/..git"
        evidence = {"acme/.": _stars(500)}
        assert daily_digest.digest_candidates([post], evidence, set(), SLOT_NOW) == []
