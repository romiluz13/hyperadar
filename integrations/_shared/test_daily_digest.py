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


# ---- Shared helpers -----------------------------------------------------------


@pytest.fixture(autouse=True)
def _no_live_hn(monkeypatch):
    """Digest HN lookups stay offline unless a test supplies a story."""

    async def no_story(url, client=None):
        return None

    monkeypatch.setattr(daily_digest, "fetch_hn_engagement", no_story)
    monkeypatch.setattr(daily_digest, "fetch_hn_story", no_story)


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
        self.history: list[dict] = []
        self.last_find_query = None

    def find(self, query, projection=None):
        self.last_find_query = query
        return _FakeCursor(
            [doc for doc in self.history if doc.get("date") != query["date"]["$ne"]]
        )

    async def update_one(self, query, update, upsert=False):
        self.upserted = (query, update, upsert)


class _FakeSignalCursor:
    def __init__(self, docs):
        self._docs = list(docs)

    def sort(self, key, direction=1):
        self._docs.sort(key=lambda doc: doc[key], reverse=direction < 0)
        return self

    async def to_list(self, length):
        return list(self._docs)


class _FakeSignals:
    """Equality-filtered time-series collection, like the producers query it."""

    def __init__(self, docs):
        self._docs = docs

    def find(self, query, projection=None):
        return _FakeSignalCursor(
            doc
            for doc in self._docs
            if all(doc.get(key) == value for key, value in query.items())
        )


class _FakeDatabase:
    def __init__(self, posts, signals=None):
        self.posts = _FakePosts(posts)
        self.digests = _FakeDigests()
        self.signals = _FakeSignals(signals or [])


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

        async def fake_about_fetch(client, owner, repo):
            return None

        monkeypatch.setattr(daily_digest, "_call_grove", fake_call_grove)
        monkeypatch.setattr(
            daily_digest, "_fetch_github_about_description", fake_about_fetch
        )

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

    async def test_reddit_item_keeps_rank_score_and_exposes_raw_signal(
        self, monkeypatch
    ):
        now = datetime(2026, 7, 23, 10, 0, 0, tzinfo=timezone.utc)
        post = _make_post(
            0,
            agent="@reddit-pulse",
            rank_score=73,
            signal={
                "source": "reddit",
                "metric": "upvotes",
                "value": 2109,
                "delta": 724,
            },
        )
        db = _FakeDatabase([post])

        async def fake_call_grove(posts):
            return [{"id": 0, "blurb": "A Reddit discussion is taking off."}]

        monkeypatch.setattr(daily_digest, "_call_grove", fake_call_grove)

        result = await daily_digest.generate_daily_digest(db=db, now=now)

        assert result is not None
        item = result["items"][0]
        assert item["score"] == 73
        assert item["signalSource"] == "reddit"
        assert item["signalMetric"] == "upvotes"
        assert item["signalValue"] == 2109
        assert item["signalDelta"] == 724

    async def test_no_posts_returns_none(self, monkeypatch):
        """When there are no posts, no digest is generated."""
        now = datetime(2026, 7, 23, 10, 0, 0, tzinfo=timezone.utc)
        db = _FakeDatabase([])

        result = await daily_digest.generate_daily_digest(db=db, now=now)
        assert result is None

    async def test_max_5_items_and_diversity(self, monkeypatch):
        """The digest has at most 5 items and at most 3 per agent."""
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
            assert count <= 3, f"More than 3 items from same agent: {agent_counts}"

    async def test_grove_picks_no_longer_decide_what_goes_out(self, monkeypatch):
        """Grove naming two ids neither shrinks nor reorders the digest."""
        now = datetime(2026, 7, 23, 10, 0, 0, tzinfo=timezone.utc)
        sample_posts = [
            _make_post(0, agent="@github-radar", rank_score=90),
            _make_post(
                1,
                agent="@reddit-pulse",
                rank_score=72,
                signal={"source": "reddit", "metric": "upvotes", "value": 2109, "delta": 724},
            ),
            _make_post(
                2,
                agent="@reddit-pulse",
                rank_score=70,
                signal={"source": "reddit", "metric": "upvotes", "value": 900, "delta": 80},
            ),
        ]
        db = _FakeDatabase(sample_posts)

        async def fake_call_grove(posts):
            return [{"id": 1, "blurb": "Grove blurb for the second item"}]

        monkeypatch.setattr(daily_digest, "_call_grove", fake_call_grove)

        result = await daily_digest.generate_daily_digest(db=db, now=now)

        # Both Reddit threads clear the floor; the evidence-free repo does not.
        first, second = result["items"]
        assert first["signalValue"] == 2109
        assert first["score"] == 73  # rankScore stays the agent's own score
        assert first["signalSource"] == "reddit"
        assert first["signalMetric"] == "upvotes"
        assert first["signalDelta"] == 724
        assert first["blurb"] == "blurb"  # no Grove blurb for id 0: body stands in
        assert second["blurb"] == "Grove blurb for the second item"

    async def test_backfill_uses_body_as_blurb(self, monkeypatch):
        """Items without a Grove blurb use the post body, truncated to 100."""
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
        assert result["items"][0]["blurb"] == "grove blurb"
        assert result["items"][1]["blurb"] == "B" * 100

    async def test_invalid_json_from_grove_still_sends_the_digest(self, monkeypatch):
        """When Grove returns nothing usable, the chosen items go out with body blurbs."""
        now = datetime(2026, 7, 23, 10, 0, 0, tzinfo=timezone.utc)
        sample_posts = [
            _make_post(i, agent=agent)
            for i, agent in enumerate(
                ["@github-radar", "@github-radar", "@youtube-trends", "@reddit-pulse"]
            )
        ]
        db = _FakeDatabase(sample_posts)

        async def fake_call_grove(posts):
            return []

        monkeypatch.setattr(daily_digest, "_call_grove", fake_call_grove)
        result = await daily_digest.generate_daily_digest(db=db, now=now)

        assert len(result["items"]) == daily_digest.DAILY_DIGEST_MIN_ITEMS
        assert all(item["blurb"] == "blurb" for item in result["items"])


class TestDigestItemWhat:
    """`what`: one plain line saying what the project is, for the RomBot digest."""

    NOW = datetime(2026, 7, 23, 10, 0, 0, tzinfo=timezone.utc)

    async def _digest(self, monkeypatch, posts, grove_picks):
        async def fake_call_grove(_posts):
            return grove_picks

        monkeypatch.setattr(daily_digest, "_call_grove", fake_call_grove)
        # These posts carry no evidence; keep every one so `what` is what's tested.
        monkeypatch.setattr(daily_digest, "DAILY_DIGEST_MIN_ITEMS", len(posts))
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


PROJECT_URL = "https://example.com/repo-0"
DAY = timedelta(days=1)


def _snapshot(stars: int, captured: datetime, project_url: str = PROJECT_URL) -> dict:
    return {
        "projectId": project_url,
        "postId": "",
        "capturedAt": captured,
        "github_stars": stars,
        "github_forks": 3,
    }


def _daily_snapshots(star_counts: list[int], first_day: datetime) -> list[dict]:
    return [
        _snapshot(stars, first_day + n * DAY) for n, stars in enumerate(star_counts)
    ]


class TestWeeklyStarGain:
    """Raw star gain over the last 8 daily snapshots, plus the window it covers."""

    FIRST_DAY = datetime(2026, 7, 10, 0, 23, tzinfo=timezone.utc)

    async def _gain(self, docs):
        return await daily_digest._weekly_star_gain(
            _FakeDatabase([], signals=docs), PROJECT_URL
        )

    async def test_no_snapshots_or_one_snapshot_is_unknown(self):
        assert await self._gain([]) == (None, None)
        assert await self._gain(_daily_snapshots([500], self.FIRST_DAY)) == (None, None)

    async def test_eight_daily_snapshots_give_the_gain_over_seven_days(self):
        docs = _daily_snapshots(
            [1000, 1010, 1030, 1035, 1050, 1090, 1100, 1130], self.FIRST_DAY
        )
        assert await self._gain(docs) == (130, 7)

    async def test_only_the_last_eight_snapshots_count_whatever_the_storage_order(self):
        counts = [100 * n for n in range(12)]
        docs = _daily_snapshots(counts, self.FIRST_DAY)
        docs.reverse()
        assert await self._gain(docs) == (700, 7)

    async def test_fewer_snapshots_give_the_shorter_window_they_cover(self):
        docs = _daily_snapshots([200, 230, 260], self.FIRST_DAY)
        assert await self._gain(docs) == (60, 2)

    async def test_sparse_snapshots_report_the_wider_window_they_really_span(self):
        docs = [
            _snapshot(100, self.FIRST_DAY),
            _snapshot(180, self.FIRST_DAY + 10 * DAY),
        ]
        assert await self._gain(docs) == (80, 10)

    async def test_documents_that_are_not_star_snapshots_are_ignored(self):
        docs = _daily_snapshots([100, 130, 160], self.FIRST_DAY)
        docs.append(
            {
                "projectId": PROJECT_URL,
                "postId": "",
                "capturedAt": self.FIRST_DAY + 3 * DAY,
                "note": "no star count",
            }
        )
        docs.append(
            {
                "projectId": PROJECT_URL,
                "postId": "post9",
                "capturedAt": self.FIRST_DAY + 4 * DAY,
                "source": "github",
                "metric": "github_stars",
                "value": 99999,
                "delta": 0,
            }
        )
        docs.append(
            _snapshot(777, self.FIRST_DAY + 5 * DAY, "https://example.com/other")
        )
        assert await self._gain(docs) == (60, 2)

    async def test_backfilled_noon_and_organic_midnight_snapshots_round_the_window(
        self,
    ):
        noon = datetime(2026, 7, 10, 12, 0, tzinfo=timezone.utc)
        docs = _daily_snapshots([100, 110, 120, 130, 140, 150, 160], noon)
        docs.append(_snapshot(175, datetime(2026, 7, 17, 0, 23, tzinfo=timezone.utc)))
        assert await self._gain(docs) == (75, 7)

    async def test_star_loss_is_a_measured_zero_not_unknown(self):
        docs = _daily_snapshots([500, 480, 470], self.FIRST_DAY)
        assert await self._gain(docs) == (0, 2)


class TestDigestItemWeeklyGain:
    """The item carries the gain so RomBot can say how fast a repo is growing."""

    NOW = datetime(2026, 7, 23, 10, 0, 0, tzinfo=timezone.utc)

    async def test_picked_and_backfilled_repo_items_carry_gain_and_window(
        self, monkeypatch
    ):
        posts = [_make_post(0), _make_post(1, agent="@hidden-gems")]
        first_day = datetime(2026, 7, 16, 0, 23, tzinfo=timezone.utc)
        signals = _daily_snapshots(
            [1000, 1020, 1050, 1080, 1100, 1150, 1180, 1240], first_day
        ) + _daily_snapshots(
            [50, 52, 60, 61, 61, 70, 80, 90],
            first_day,
        )
        for doc in signals[8:]:
            doc["projectId"] = "https://example.com/repo-1"

        async def fake_call_grove(_posts):
            return [{"id": 0, "blurb": "b0"}]

        monkeypatch.setattr(daily_digest, "_call_grove", fake_call_grove)
        result = await daily_digest.generate_daily_digest(
            db=_FakeDatabase(posts, signals=signals), now=self.NOW
        )

        assert result is not None
        picked, backfilled = result["items"]
        assert (picked["weeklyGain"], picked["weeklyGainWindowDays"]) == (240, 7)
        assert (backfilled["weeklyGain"], backfilled["weeklyGainWindowDays"]) == (40, 7)

    async def test_a_failing_snapshot_read_leaves_the_gain_unknown_not_the_digest_lost(
        self, monkeypatch
    ):
        class _BrokenSignals:
            def find(self, *args, **kwargs):
                raise RuntimeError("signals unavailable")

        db = _FakeDatabase([_make_post(0)])
        db.signals = _BrokenSignals()

        async def fake_call_grove(_posts):
            return [{"id": 0, "blurb": "b0"}]

        monkeypatch.setattr(daily_digest, "_call_grove", fake_call_grove)
        result = await daily_digest.generate_daily_digest(db=db, now=self.NOW)

        assert result is not None
        assert db.digests.upserted is not None
        item = result["items"][0]
        assert item["weeklyGain"] is None
        assert item["weeklyGainWindowDays"] is None

    async def test_item_without_snapshots_has_null_gain_and_window(self, monkeypatch):
        posts = [_make_post(0, agent="@youtube-trends", kind="video")]

        async def fake_call_grove(_posts):
            return [{"id": 0, "blurb": "b0"}]

        monkeypatch.setattr(daily_digest, "_call_grove", fake_call_grove)
        result = await daily_digest.generate_daily_digest(
            db=_FakeDatabase(posts), now=self.NOW
        )

        assert result is not None
        item = result["items"][0]
        assert item["weeklyGain"] is None
        assert item["weeklyGainWindowDays"] is None


class TestDailyDigestAgentHandles:
    """The agent handle list excludes internal agents."""

    def test_community_radar_excluded(self):
        assert "@community-radar" not in DAILY_DIGEST_AGENT_HANDLES

    def test_weekly_digest_excluded(self):
        assert "@weekly-digest" not in DAILY_DIGEST_AGENT_HANDLES

    def test_all_four_external_agents_present(self):
        assert len(DAILY_DIGEST_AGENT_HANDLES) == 4


# ---- Evidence-ranked selection (audit 2026-11-09, items 2, 5, 6) --------------


def _repo_post(i: int, stars: int, agent: str = "@github-radar") -> dict:
    url = f"https://github.com/acme/repo-{i}"
    return _make_post(
        i,
        agent=agent,
        url=url,
        description=f"Tool number {i}",
        signal={"source": "github", "metric": "github_stars", "value": stars},
    )


def _gain_snapshots(url: str, start: int, end: int, days: int = 7) -> list[dict]:
    first = datetime(2026, 7, 16, 0, 23, tzinfo=timezone.utc)
    step = (end - start) / days
    return [
        _snapshot(round(start + n * step), first + n * DAY, url) for n in range(days + 1)
    ]


class TestEvidenceRankedDigest:
    NOW = datetime(2026, 7, 23, 10, 0, 0, tzinfo=timezone.utc)

    @staticmethod
    def _grove(monkeypatch, seen=None):
        async def fake_call_grove(posts):
            if seen is not None:
                seen.extend(posts)
            return [{"id": i, "blurb": f"blurb {i}"} for i in range(len(posts))]

        monkeypatch.setattr(daily_digest, "_call_grove", fake_call_grove)

    async def test_strongest_evidence_leads_whatever_the_rank_score(self, monkeypatch):
        """A capped rankScore of 100 for everyone cannot decide the order."""
        posts = [
            _repo_post(0, 600),  # +500 in a week
            _repo_post(1, 20000),  # +9,000 in a week
            _repo_post(2, 4000),  # +3,000 in a week
        ]
        for post in posts:
            post["rankScore"] = 100
        signals = (
            _gain_snapshots(posts[0]["project"]["url"], 100, 600)
            + _gain_snapshots(posts[1]["project"]["url"], 11000, 20000)
            + _gain_snapshots(posts[2]["project"]["url"], 1000, 4000)
        )
        seen = []
        self._grove(monkeypatch, seen)

        result = await daily_digest.generate_daily_digest(
            db=_FakeDatabase(posts, signals=signals), now=self.NOW
        )

        assert [item["title"] for item in result["items"]] == [
            "repo-1",
            "repo-2",
            "repo-0",
        ]
        assert result["items"][0]["digestScore"] > result["items"][1]["digestScore"]
        # Grove writes for the chosen items only, in the chosen order.
        assert [p["project"]["title"] for p in seen] == ["repo-1", "repo-2", "repo-0"]
        assert result["items"][0]["blurb"] == "blurb 0"

    async def test_hn_attention_is_carried_and_counted(self, monkeypatch):
        posts = [_repo_post(0, 1200), _repo_post(1, 1200)]
        signals = _gain_snapshots(
            posts[0]["project"]["url"], 900, 1200
        ) + _gain_snapshots(posts[1]["project"]["url"], 900, 1200)

        async def fake_hn(url, client=None):
            if url.endswith("repo-1"):
                return {"points": 292, "comments": 139, "story_url": "https://news.ycombinator.com/item?id=9"}
            return None

        monkeypatch.setattr(daily_digest, "fetch_hn_engagement", fake_hn)
        self._grove(monkeypatch)

        result = await daily_digest.generate_daily_digest(
            db=_FakeDatabase(posts, signals=signals), now=self.NOW
        )

        first, second = result["items"]
        assert first["title"] == "repo-1"
        assert (first["hnPoints"], first["hnComments"]) == (292, 139)
        assert (second["hnPoints"], second["hnComments"]) == (None, None)

    async def test_an_hn_item_is_scored_on_its_own_story_comments(self, monkeypatch):
        """A Show HN post scores points and comments of the story it came from."""
        show_hn = _make_post(
            0,
            agent="@hidden-gems",
            kind="thread",
            url="https://example.com/launch",
            signal={
                "metric": "hn_points",
                "value": 100,
                "evidenceUrl": "https://news.ycombinator.com/item?id=777",
            },
        )
        looked_up = []

        async def fake_story(story_id, client=None):
            looked_up.append(story_id)
            return {"points": 100, "comments": 80}

        async def no_repo_story(url, client=None):
            assert url != "https://example.com/launch", "an HN item uses its own story"
            return None

        monkeypatch.setattr(daily_digest, "fetch_hn_story", fake_story)
        monkeypatch.setattr(daily_digest, "fetch_hn_engagement", no_repo_story)
        self._grove(monkeypatch)

        result = await daily_digest.generate_daily_digest(
            db=_FakeDatabase([show_hn, _repo_post(1, 30000)]), now=self.NOW
        )

        item = next(i for i in result["items"] if i["title"] == "repo-0")
        assert looked_up == ["777"]
        assert item["hnComments"] == 80
        assert item["digestScore"] == 59.2  # 20*log10(101) + 10*log10(81)

    async def test_the_same_url_from_two_agents_keeps_each_posts_own_hn_evidence(self, monkeypatch):
        url = "https://github.com/acme/tool"
        stars_post = _make_post(
            0, url=url, rank_score=99, signal={"metric": "github_stars", "value": 900}
        )  # ranked first, so its evidence is looked up first
        show_hn_post = _make_post(
            1,
            agent="@hidden-gems",
            url=url,
            signal={
                "metric": "hn_points",
                "value": 400,
                "evidenceUrl": "https://news.ycombinator.com/item?id=555",
            },
        )

        async def fake_story(story_id, client=None):
            return {"points": 400, "comments": 300}

        async def fake_repo_story(repo_url, client=None):
            return {"points": 5, "comments": 1}

        monkeypatch.setattr(daily_digest, "fetch_hn_story", fake_story)
        monkeypatch.setattr(daily_digest, "fetch_hn_engagement", fake_repo_story)
        self._grove(monkeypatch)

        result = await daily_digest.generate_daily_digest(
            db=_FakeDatabase([stars_post, show_hn_post, _repo_post(2, 30000)]), now=self.NOW
        )

        item = next(i for i in result["items"] if i["url"] == url)
        assert item["agentHandle"] == "@hidden-gems"
        assert (item["hnPoints"], item["hnComments"]) == (400, 300)

    async def test_weak_items_stay_out_but_a_thin_day_keeps_two(self, monkeypatch):
        strong = [_repo_post(i, 30000) for i in range(2)]
        weak = _make_post(
            5,
            agent="@youtube-trends",
            kind="video",
            signal={"source": "youtube", "metric": "views", "value": 900},
        )
        signals = [
            doc
            for post in strong
            for doc in _gain_snapshots(post["project"]["url"], 20000, 30000)
        ]
        self._grove(monkeypatch)

        result = await daily_digest.generate_daily_digest(
            db=_FakeDatabase(strong + [weak], signals=signals), now=self.NOW
        )
        assert [item["title"] for item in result["items"]] == ["repo-0", "repo-1"]

        # Nothing clears the floor: the two best still go out, never zero.
        thin = await daily_digest.generate_daily_digest(
            db=_FakeDatabase([weak, _make_post(6, agent="@hidden-gems")]), now=self.NOW
        )
        assert len(thin["items"]) == 2

    async def test_at_most_one_video_and_three_per_agent(self, monkeypatch):
        repos = [_repo_post(i, 50000) for i in range(5)]
        signals = [
            doc
            for post in repos
            for doc in _gain_snapshots(post["project"]["url"], 40000, 50000)
        ]
        videos = [
            _make_post(
                10 + i,
                agent="@youtube-trends",
                kind="video",
                signal={"source": "youtube", "metric": "views", "value": 5_000_000},
            )
            for i in range(2)
        ]
        self._grove(monkeypatch)

        result = await daily_digest.generate_daily_digest(
            db=_FakeDatabase(repos + videos, signals=signals), now=self.NOW
        )

        handles = [item["agentHandle"] for item in result["items"]]
        assert handles.count("@github-radar") == 3
        assert handles.count("@youtube-trends") == 1
        assert len(result["items"]) == 4

    async def test_items_featured_in_the_last_30_daily_digests_are_not_repeated(
        self, monkeypatch
    ):
        posts = [_repo_post(i, 30000) for i in range(3)]
        signals = [
            doc
            for post in posts
            for doc in _gain_snapshots(post["project"]["url"], 20000, 30000)
        ]
        db = _FakeDatabase(posts, signals=signals)
        db.digests.history = [
            {"date": "2026-07-01", "items": [{"url": posts[0]["project"]["url"]}]},
            # Today's own earlier run must not hide today's picks on a re-run.
            {"date": "2026-07-23", "items": [{"url": posts[1]["project"]["url"]}]},
        ]
        self._grove(monkeypatch)

        result = await daily_digest.generate_daily_digest(db=db, now=self.NOW)

        urls = [item["url"] for item in result["items"]]
        assert posts[0]["project"]["url"] not in urls
        assert posts[1]["project"]["url"] in urls
        query = db.digests.last_find_query
        assert query["digestType"] == "daily"
        assert query["createdAt"] == {"$gte": self.NOW - timedelta(days=30)}

    async def test_growth_is_hidden_for_a_repo_that_is_all_new_stars(self, monkeypatch):
        """247 new stars on a 255-star repo is a birth, not a breakout."""
        posts = [_repo_post(0, 255)]
        signals = _gain_snapshots(posts[0]["project"]["url"], 8, 255)
        self._grove(monkeypatch)

        result = await daily_digest.generate_daily_digest(
            db=_FakeDatabase(posts, signals=signals), now=self.NOW
        )

        item = result["items"][0]
        assert (item["weeklyGain"], item["weeklyGainWindowDays"]) == (None, None)

    async def test_growth_is_kept_for_a_real_breakout_from_an_existing_base(
        self, monkeypatch
    ):
        """1.1K -> 11K stars in a week is the headline, not a birth."""
        posts = [_repo_post(0, 10954)]
        signals = _gain_snapshots(posts[0]["project"]["url"], 1096, 10954)
        self._grove(monkeypatch)

        result = await daily_digest.generate_daily_digest(
            db=_FakeDatabase(posts, signals=signals), now=self.NOW
        )

        item = result["items"][0]
        assert (item["weeklyGain"], item["weeklyGainWindowDays"]) == (9858, 7)

    async def test_a_grove_failure_costs_the_blurbs_not_the_digest(self, monkeypatch):
        posts = [_repo_post(0, 30000)]
        signals = _gain_snapshots(posts[0]["project"]["url"], 20000, 30000)

        async def broken_grove(_posts):
            raise RuntimeError("grove down")

        monkeypatch.setattr(daily_digest, "_call_grove", broken_grove)

        result = await daily_digest.generate_daily_digest(
            db=_FakeDatabase(posts, signals=signals), now=self.NOW
        )

        assert result["items"][0]["blurb"] == "blurb"


class TestDigestScore:
    """The uncapped evidence score, per source, on worked examples."""

    def test_repo_score_rewards_absolute_and_relative_growth_and_hn(self):
        score = daily_digest._digest_score
        auto_ontology = score({"github_stars": 10954}, 9858, 7, 292, 139)
        mega_repo = score({"github_stars": 319522}, 32883, 28, None, None)
        small_new = score({"github_stars": 255}, 247, 7, None, None)
        assert round(auto_ontology, 1) == 139.5
        assert auto_ontology > mega_repo > small_new

    def test_a_repo_born_this_week_gets_no_relative_growth_bonus(self):
        """255 stars, 247 of them this week: being born, not breaking out."""
        newborn = daily_digest._digest_score({"github_stars": 255}, 247, 7, None, None)
        assert round(newborn, 1) == 59.9  # 25*log10(248), no relative bonus

    def test_unknown_stars_earn_no_relative_growth_bonus(self):
        unknown = daily_digest._digest_score({}, 247, 7, None, None)
        assert round(unknown, 1) == 59.9

    def test_sparse_snapshot_windows_are_normalized_to_a_week(self):
        score = daily_digest._digest_score
        assert score({"github_stars": 5000}, 2000, 14, None, None) == score(
            {"github_stars": 5000}, 1000, 7, None, None
        )

    def test_reddit_hn_and_video_scores(self):
        score = daily_digest._digest_score
        reddit = score({"upvotes": 1413, "comments": 400}, None, None, None, None)
        hn = score({"hn_points": 150}, None, None, None, None)
        video = score({"views": 212704}, None, None, None, None)
        assert round(reddit, 1) == 89.0
        assert round(hn, 1) == 43.6
        assert round(video, 1) == 63.9
