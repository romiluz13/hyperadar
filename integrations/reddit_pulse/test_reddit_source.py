"""Tests for the Reddit source: Monid free fetch transport, parsing, cooldown, gate."""

import html
import importlib.util
import json
from datetime import datetime, timedelta, timezone
from pathlib import Path
from urllib.parse import parse_qs, urlparse

import httpx
import pytest


def load_source():
    path = Path(__file__).parent / "reddit_source.py"
    spec = importlib.util.spec_from_file_location("reddit_test_source", path)
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture(autouse=True)
def _monid_key(monkeypatch):
    monkeypatch.setenv("MONID_API_KEY", "monid_test_key")


def shreddit_post(
    post_id: str,
    title: str = "Test post",
    score: int = 100,
    comments: int = 10,
    subreddit: str = "LocalLLaMA",
    created: datetime | None = None,
    content_href: str | None = None,
    body: str | None = None,
) -> str:
    """One feed entry the way Reddit's rising feed renders it (2026-11-10 probe)."""
    attrs = {
        "author": "someone",
        "author-id": "t2_author",
        "comment-count": str(comments),
        "id": f"t3_{post_id}",
        "permalink": f"/r/{subreddit}/comments/{post_id}/some_slug/",
        "post-title": title,
        "score": str(score),
        "subreddit-name": subreddit,
    }
    if created is not None:
        attrs["created-timestamp"] = created.strftime("%Y-%m-%dT%H:%M:%S.%f+0000")
    if content_href is not None:
        attrs["content-href"] = content_href
    rendered = " ".join(f'{k}="{html.escape(v)}"' for k, v in attrs.items())
    # The real markup repeats post identity on nested helper elements; the
    # parser must count each post once.
    text_body = (
        f'<shreddit-post-text-body post-id="t3_{post_id}" slot="text-body"><a slot="text-body">'
        f'<div class="overflow-hidden"><div about="t3_{post_id}" property="schema:articleBody">'
        f"<p>{html.escape(body)}</p><div><p>(nested)</p></div></div></div></a>"
        "</shreddit-post-text-body>"
        if body is not None
        else ""
    )
    return (
        f"<shreddit-post {rendered}><shreddit-post-overflow-menu "
        f'post-id="t3_{post_id}" permalink="{attrs["permalink"]}" score="{score}">'
        f"</shreddit-post-overflow-menu>{text_body}</shreddit-post>"
    )


def monid_client(page_for, calls: list | None = None, run_status=None):
    """A Monid API double: POST /v1/run answers the free TinyFish fetch synchronously.

    ``page_for(subreddit)`` returns feed markup, or a dict like
    ``{"error": "target_http_error", "status": 403}`` for a per-URL failure.
    """

    def handler(request: httpx.Request) -> httpx.Response:
        if calls is not None:
            calls.append(request)
        if run_status is not None:
            return run_status(request)
        body = json.loads(request.content)
        results, errors = [], []
        for url in body["input"]["urls"]:
            name = parse_qs(urlparse(url).query)["name"][0]
            page = page_for(name)
            if isinstance(page, dict):
                errors.append({"url": url, **page})
            else:
                results.append({"url": url, "text": page})
        return httpx.Response(
            200,
            json={
                "runId": "run-1",
                "status": "COMPLETED",
                "providerResponse": {"httpStatus": 200},
                "output": {"results": results, "errors": errors},
            },
        )

    return httpx.AsyncClient(transport=httpx.MockTransport(handler))


def fresh(hours: float = 1) -> datetime:
    return datetime.now(timezone.utc) - timedelta(hours=hours)


def test_normalize_reddit_url_strips_query_params():
    source = load_source()
    assert (
        source._normalize_reddit_url(
            "https://www.reddit.com/r/LocalLLaMA/comments/abc/?utm_source=share"
        )
        == "https://www.reddit.com/r/LocalLLaMA/comments/abc"
    )
    assert (
        source._normalize_reddit_url("https://www.reddit.com/r/LocalLLaMA/comments/abc/")
        == "https://www.reddit.com/r/LocalLLaMA/comments/abc"
    )


def test_feed_parser_reads_each_post_once_with_engagement():
    source = load_source()
    created = datetime(2026, 11, 9, 22, 22, 35, tzinfo=timezone.utc)
    markup = shreddit_post(
        "1xs5pkl", title='Is it "biased" & open?', score=254, comments=167,
        created=created, content_href="https://github.com/acme/tool",
    ) + shreddit_post("1xs3y17", score=238, comments=82, created=created)

    posts = source.parse_rising_feed(markup)

    assert [p["url"] for p in posts] == [
        "https://www.reddit.com/r/LocalLLaMA/comments/1xs5pkl",
        "https://www.reddit.com/r/LocalLLaMA/comments/1xs3y17",
    ]
    first = posts[0]
    assert first["title"] == 'Is it "biased" & open?'
    assert first["num_upvotes"] == 254
    assert first["num_comments"] == 167
    assert first["subreddit"] == "LocalLLaMA"
    assert first["evidence_url"] == first["url"]
    # Link posts carry their target into the corroboration corpus text.
    assert "https://github.com/acme/tool" in first["description"]


def test_feed_parser_skips_posts_without_identity():
    source = load_source()
    markup = (
        '<shreddit-post id="t3_x" post-title="No permalink" score="50"></shreddit-post>'
        '<shreddit-post id="t3_y" permalink="/r/a/comments/y/s/" score="50"></shreddit-post>'
        + shreddit_post("good", created=fresh())
    )
    assert [p["url"] for p in source.parse_rising_feed(markup)] == [
        "https://www.reddit.com/r/LocalLLaMA/comments/good"
    ]


def test_feed_parser_keeps_the_post_body_for_the_corpus():
    source = load_source()
    markup = shreddit_post(
        "b1", title="New tool", body="Built on acme/agentkit, try it", created=fresh()
    )

    [post] = source.parse_rising_feed(markup)

    assert post["description"].startswith("New tool")
    assert "Built on acme/agentkit, try it" in post["description"]
    assert "(nested)" in post["description"]


def test_feed_parser_keeps_every_post_when_a_body_is_left_open():
    """A truncated body must not swallow the posts after it."""
    source = load_source()
    unclosed = (
        '<shreddit-post id="t3_a" post-title="First" permalink="/r/LocalLLaMA/comments/a/s/" '
        'score="50"><div about="t3_a" property="schema:articleBody"><p>cut off'
    )
    markup = unclosed + shreddit_post("b", title="Second", body="own body", created=fresh())

    posts = {p["title"]: p for p in source.parse_rising_feed(markup)}

    assert set(posts) == {"First", "Second"}
    assert "Second" not in posts["First"]["description"]
    assert "own body" in posts["Second"]["description"]


def test_feed_parser_rejects_a_feed_whose_posts_lost_their_attributes():
    """Reddit renaming score/title/permalink must read as a broken source, not a quiet day."""
    source = load_source()
    markup = '<shreddit-post id="t3_a" title="Renamed" href="/r/a/comments/a/s/"></shreddit-post>'
    with pytest.raises(ValueError, match="no posts in the feed markup"):
        source.parse_rising_feed(markup)


def test_feed_parser_keeps_a_quiet_feed_below_the_noise_floor():
    source = load_source()
    assert source.parse_rising_feed(shreddit_post("q", score=3, created=fresh())) == []


def test_parse_created_tolerates_a_short_timestamp():
    source = load_source()
    assert source._parse_created("2026") == "2026"


@pytest.mark.asyncio
async def test_fetch_asks_monid_free_fetch_for_every_rising_feed():
    source = load_source()
    calls = []
    client = monid_client(
        lambda name: shreddit_post(f"p{name}", subreddit=name, created=fresh()), calls
    )

    await source.fetch_reddit_candidates(max_results=50, db=FakeDb(), client=client)

    assert len(calls) == 2  # 16 subreddits, at most 10 URLs per free call
    requested = []
    for request in calls:
        assert str(request.url) == "https://api.monid.ai/v1/run"
        assert request.headers["authorization"] == "Bearer monid_test_key"
        body = json.loads(request.content)
        assert body["provider"] == "tinyfish"
        assert body["endpoint"] == "/free/fetch"
        assert body["input"]["ttl"] == 0
        assert body["input"]["format"] == "html"
        assert body["input"]["include_selectors"] == ["shreddit-post"]
        assert len(body["input"]["urls"]) <= 10
        requested.extend(body["input"]["urls"])
    assert sorted(requested) == sorted(
        f"https://www.reddit.com/svc/shreddit/community-more-posts/rising/?name={s}"
        for s in source.SUBREDDITS
    )


@pytest.mark.asyncio
async def test_fetch_polls_an_async_run_until_it_completes():
    source = load_source()
    monkey_page = shreddit_post("async1", score=300, comments=40, created=fresh())
    polls = []

    def run_status(request: httpx.Request) -> httpx.Response:
        if request.method == "POST":
            return httpx.Response(202, json={"runId": "r-async", "status": "RUNNING"})
        polls.append(str(request.url))
        if len(polls) < 2:
            return httpx.Response(200, json={"runId": "r-async", "status": "RUNNING"})
        return httpx.Response(
            200,
            json={
                "runId": "r-async",
                "status": "COMPLETED",
                "providerResponse": {"httpStatus": 200},
                "output": {"results": [{"url": "u", "text": monkey_page}], "errors": []},
            },
        )

    source.MONID_POLL_INTERVAL_SECONDS = 0
    client = monid_client(None, run_status=run_status)
    candidates = await source.fetch_reddit_candidates(
        max_results=10, db=FakeDb(), client=client
    )

    assert polls[0] == "https://api.monid.ai/v1/runs/r-async"
    assert [c["url"] for c in candidates] == [
        "https://www.reddit.com/r/LocalLLaMA/comments/async1"
    ]


@pytest.mark.asyncio
async def test_fetch_gives_up_on_a_run_that_never_finishes():
    source = load_source()
    source.MONID_POLL_INTERVAL_SECONDS = 0
    source.MONID_RUN_DEADLINE_SECONDS = 0.05

    def run_status(request: httpx.Request) -> httpx.Response:
        if request.method == "POST":
            return httpx.Response(202, json={"runId": "r-slow", "status": "RUNNING"})
        return httpx.Response(200, json={"runId": "r-slow", "status": "RUNNING"})

    with pytest.raises(RuntimeError, match="timed out"):
        await source.fetch_reddit_candidates(
            max_results=10, db=FakeDb(), client=monid_client(None, run_status=run_status)
        )


@pytest.mark.asyncio
async def test_fetch_requires_the_monid_key(monkeypatch):
    source = load_source()
    monkeypatch.delenv("MONID_API_KEY")
    with pytest.raises(RuntimeError, match="MONID_API_KEY"):
        await source.fetch_reddit_candidates(max_results=10, db=FakeDb())


@pytest.mark.asyncio
async def test_fetch_raises_when_every_subreddit_fetch_fails():
    source = load_source()
    client = monid_client(lambda _name: {"error": "target_http_error", "status": 403})
    with pytest.raises(RuntimeError, match="Reddit source down.*target_http_error"):
        await source.fetch_reddit_candidates(max_results=10, db=FakeDb(), client=client)


@pytest.mark.asyncio
async def test_fetch_raises_when_monid_refuses_the_call():
    source = load_source()

    def run_status(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(402, json={"code": 402, "message": "Insufficient balance"})

    with pytest.raises(RuntimeError, match="Reddit source down.*402"):
        await source.fetch_reddit_candidates(
            max_results=10, db=FakeDb(), client=monid_client(None, run_status=run_status)
        )


@pytest.mark.asyncio
async def test_fetch_raises_when_no_feed_parses_any_post():
    """Reddit changing its markup must fail loudly, not look like a quiet day."""
    source = load_source()
    client = monid_client(lambda _name: "<div>new markup</div>")
    with pytest.raises(RuntimeError, match="Reddit source down"):
        await source.fetch_reddit_candidates(max_results=10, db=FakeDb(), client=client)


@pytest.mark.asyncio
async def test_fetch_raises_when_every_feed_lost_its_attributes():
    source = load_source()
    client = monid_client(
        lambda name: f'<shreddit-post id="t3_{name}" title="Renamed"></shreddit-post>'
    )
    with pytest.raises(RuntimeError, match="Reddit source down"):
        await source.fetch_reddit_candidates(db=FakeDb(), client=client)


@pytest.mark.asyncio
async def test_fetch_raises_when_monid_returns_no_result_for_any_url():
    source = load_source()

    def empty(request):
        return httpx.Response(
            200,
            json={"runId": "r", "status": "COMPLETED", "output": {"results": [], "errors": []}},
        )

    client = monid_client(None, run_status=empty)
    with pytest.raises(RuntimeError, match="Reddit source down"):
        await source.fetch_reddit_candidates(db=FakeDb(), client=client)


@pytest.mark.asyncio
async def test_fetch_counts_a_malformed_monid_reply_as_a_failed_fetch():
    source = load_source()

    def malformed(request):
        # 202 without a run id must count as a failed fetch, not crash the run.
        return httpx.Response(202, json={"status": "READY"})

    client = monid_client(None, run_status=malformed)
    with pytest.raises(RuntimeError, match="Reddit source down"):
        await source.fetch_reddit_candidates(db=FakeDb(), client=client)


@pytest.mark.asyncio
async def test_fetch_accepts_a_completed_run_with_a_null_provider_status():
    source = load_source()

    def null_status(request):
        body = json.loads(request.content)
        results = [
            {"url": u, "text": shreddit_post(f"n{i}", created=fresh())}
            for i, u in enumerate(body["input"]["urls"])
        ]
        return httpx.Response(
            200,
            json={
                "runId": "r",
                "status": "COMPLETED",
                "providerResponse": {"httpStatus": None},
                "output": {"results": results, "errors": []},
            },
        )

    client = monid_client(None, run_status=null_status)
    posts = await source.fetch_reddit_candidates(max_results=50, db=FakeDb(), client=client)
    assert posts


@pytest.mark.asyncio
async def test_fetch_tolerates_some_subreddits_failing():
    source = load_source()

    def page_for(name: str):
        if name == "LocalLLaMA":
            return shreddit_post("ok", score=200, comments=20, created=fresh())
        return {"error": "timeout"}

    candidates = await source.fetch_reddit_candidates(
        max_results=10, db=FakeDb(url_to_post={}), client=monid_client(page_for)
    )

    assert [c["url"] for c in candidates] == [
        "https://www.reddit.com/r/LocalLLaMA/comments/ok"
    ]


@pytest.mark.asyncio
async def test_cooldown_skips_recently_posted_threads():
    source = load_source()
    page = shreddit_post("old", title="Old thread", score=200, created=fresh()) + shreddit_post(
        "new", title="Recent thread", score=500, created=fresh()
    )
    recent_permalink = "https://www.reddit.com/r/LocalLLaMA/comments/new"
    db = FakeDb(
        url_to_post={
            recent_permalink: {"postedAt": datetime.now(timezone.utc) - timedelta(days=2)}
        }
    )

    candidates = await source.fetch_reddit_candidates(
        max_results=10, db=db, client=monid_client(lambda _n: page)
    )

    urls = [c["url"] for c in candidates]
    assert "https://www.reddit.com/r/LocalLLaMA/comments/old" in urls
    assert recent_permalink not in urls


@pytest.mark.asyncio
async def test_cooldown_keeps_threads_posted_7_or_more_days_ago():
    source = load_source()
    page = shreddit_post("abc", score=150, subreddit="MachineLearning", created=fresh())
    permalink = "https://www.reddit.com/r/MachineLearning/comments/abc"
    db = FakeDb(
        url_to_post={permalink: {"postedAt": datetime.now(timezone.utc) - timedelta(days=7)}}
    )

    candidates = await source.fetch_reddit_candidates(
        max_results=10, db=db, client=monid_client(lambda _n: page)
    )

    assert [c["url"] for c in candidates] == [permalink]


@pytest.mark.asyncio
async def test_engagement_velocity_is_computed_and_sorted():
    source = load_source()
    page = shreddit_post("a", score=100, comments=50, created=fresh(1)) + shreddit_post(
        "b", score=500, comments=50, created=fresh(2)
    )

    candidates = await source.fetch_reddit_candidates(
        max_results=10, db=FakeDb(), client=monid_client(lambda _n: page)
    )

    assert len(candidates) == 2
    assert "engagement_velocity" in candidates[0]
    assert candidates[0]["heat_score"] >= candidates[1]["heat_score"]


@pytest.mark.asyncio
async def test_engagement_velocity_falls_back_to_1_hour():
    source = load_source()
    page = shreddit_post("fb", score=42, created=None)

    candidates = await source.fetch_reddit_candidates(
        max_results=10, db=FakeDb(), client=monid_client(lambda _n: page)
    )

    assert len(candidates) == 1
    assert candidates[0]["engagement_velocity"] == 42.0


@pytest.mark.asyncio
async def test_gate_rejects_below_noise_floor():
    """A 15-upvote post passes the fetch's 10-upvote filter but fails the gate."""
    source = load_source()
    page = shreddit_post("hot", score=5000, comments=500, created=fresh()) + shreddit_post(
        "noisy", score=15, comments=0, created=fresh()
    )

    candidates = await source.fetch_reddit_candidates(
        max_results=10, db=FakeDb(), client=monid_client(lambda _n: page)
    )

    urls = [c["url"] for c in candidates]
    assert "https://www.reddit.com/r/LocalLLaMA/comments/hot" in urls
    assert "https://www.reddit.com/r/LocalLLaMA/comments/noisy" not in urls


@pytest.mark.asyncio
async def test_gate_rejects_below_heat_threshold():
    source = load_source()
    page = shreddit_post("lw", title="Lukewarm", score=20, comments=1, created=fresh())

    candidates = await source.fetch_reddit_candidates(
        max_results=10, db=FakeDb(), client=monid_client(lambda _n: page)
    )

    assert candidates == []


class FakePosts:
    """Mock posts collection. Returns canned find_one results by URL."""

    def __init__(self, url_to_post: dict | None = None):
        self._url_to_post = url_to_post or {}

    async def find_one(self, query, *_args, **_kwargs):
        url = query.get("project.url", "")
        # Normalize keys so trailing-slash / query-param variants match
        for stored_url, value in self._url_to_post.items():
            normalized_stored = stored_url.split("?")[0].rstrip("/")
            if normalized_stored == url:
                return value
        return None


class FakeSnapshots:
    """Mock reddit_post_snapshots collection."""

    def __init__(self):
        self._docs = []

    async def insert_one(self, doc):
        self._docs.append(doc)
        return type("R", (), {"inserted_id": "fake"})()

    async def find_one(self, query, *_args, **_kwargs):
        url = query.get("url", "")
        docs = [d for d in self._docs if d.get("url") == url]
        if not docs:
            return None
        return max(
            docs,
            key=lambda d: d.get(
                "capturedAt", datetime.min.replace(tzinfo=timezone.utc)
            ),
        )


class FakeDb:
    def __init__(self, url_to_post: dict | None = None):
        self.posts = FakePosts(url_to_post)
        self.reddit_post_snapshots = FakeSnapshots()


@pytest.mark.asyncio
async def test_cooldown_matches_despite_trailing_slash(db):
    """Cooldown should match even if stored URL has trailing slash.

    Regression test for asymmetric URL normalization: the candidate dict
    stores the raw URL while the cooldown lookup normalizes it. After the fix,
    the URL is normalized at construction so both stored and lookup sides match.
    """
    from _shared.mongo import _get_db

    source = load_source()
    url_with_slash = "https://www.reddit.com/r/LocalLLaMA/comments/abc123/"
    # Insert a post with the normalized URL posted 2 days ago
    db.posts.insert_one(
        {
            "project": {"url": source._normalize_reddit_url(url_with_slash)},
            "postedAt": datetime.now(timezone.utc) - timedelta(days=2),
        }
    )

    # The cooldown should find this post and skip it (< COOLDOWN_DAYS)
    test_db = _get_db()
    days = await source._last_posted_days(test_db, url_with_slash)
    assert days < source.COOLDOWN_DAYS, (
        f"Should be < {source.COOLDOWN_DAYS} days, got {days}"
    )

    # Cleanup
    db.posts.delete_one({"project.url": source._normalize_reddit_url(url_with_slash)})


# ---------------------------------------------------------------------------
# Shared heat score attachment (ticket 02 — expand: heat_score beside fields)
# ---------------------------------------------------------------------------


def test_attach_reddit_heat_adds_heat_score_field():
    """Each candidate gets a heat_score (int, 0-100) beside existing fields."""
    source = load_source()
    candidates = [
        {
            "url": "https://www.reddit.com/r/LocalLLaMA/comments/a",
            "title": "A",
            "subreddit": "LocalLLaMA",
            "num_upvotes": 5000,
            "num_comments": 500,
            "age_hours": 6.0,
        },
        {
            "url": "https://www.reddit.com/r/LocalLLaMA/comments/b",
            "title": "B",
            "subreddit": "LocalLLaMA",
            "num_upvotes": 50,
            "num_comments": 5,
            "age_hours": 6.0,
        },
    ]
    result = source.attach_reddit_heat(candidates)
    for c in result:
        assert "heat_score" in c
        assert isinstance(c["heat_score"], int)
        assert 0 <= c["heat_score"] <= 100
        assert "outperform_ratio" in c
        assert "baseline_confidence" in c
    a = next(c for c in result if c["url"].endswith("/a"))
    b = next(c for c in result if c["url"].endswith("/b"))
    assert a["heat_score"] > b["heat_score"], (
        f"5K upvotes ({a['heat_score']}) should outrank 50 ({b['heat_score']})"
    )


def test_attach_reddit_heat_empty_list_returns_empty():
    source = load_source()
    assert source.attach_reddit_heat([]) == []


def test_attach_reddit_heat_preserves_existing_fields():
    """Expand: heat_score is added beside existing fields, not replacing them."""
    source = load_source()
    candidates = [
        {
            "url": "https://www.reddit.com/r/LocalLLaMA/comments/a",
            "title": "A",
            "subreddit": "LocalLLaMA",
            "num_upvotes": 5000,
            "num_comments": 500,
            "age_hours": 6.0,
            "visibility_score": 80.0,
            "engagement_velocity": 833.3,
        },
    ]
    result = source.attach_reddit_heat(candidates)
    assert result[0]["visibility_score"] == 80.0
    assert result[0]["engagement_velocity"] == 833.3
    assert "heat_score" in result[0]


def test_attach_reddit_heat_missing_age_hours_is_handled():
    """A candidate without age_hours must not crash (velocity stays 0)."""
    source = load_source()
    candidates = [
        {
            "url": "https://www.reddit.com/r/LocalLLaMA/comments/x",
            "title": "X",
            "subreddit": "LocalLLaMA",
            "num_upvotes": 5000,
            "num_comments": 500,
        },
    ]
    result = source.attach_reddit_heat(candidates)
    assert isinstance(result[0]["heat_score"], int)


# ---------------------------------------------------------------------------
# Regression: cooldown query bug (fix A — same-content-every-day root cause)
# ---------------------------------------------------------------------------


async def test_last_posted_days_uses_most_recent_post_not_oldest(db):
    """_last_posted_days must measure from the MOST RECENT post, not the oldest.

    Regression: the old find_one had no sort=[("postedAt", -1)], so it
    returned the oldest post's age. Once the oldest post was >7 days old, the
    URL was permanently eligible and re-posted every single day.
    """
    from _shared.mongo import _get_db

    source = load_source()
    url = "https://www.reddit.com/r/LocalLLaMA/comments/regression_sort123"
    normalized = source._normalize_reddit_url(url)
    # An OLD post (30 days ago) and a RECENT post (2 days ago) for the same URL.
    db.posts.insert_one(
        {
            "project": {"url": normalized},
            "postedAt": datetime.now(timezone.utc) - timedelta(days=30),
        }
    )
    db.posts.insert_one(
        {
            "project": {"url": normalized},
            "postedAt": datetime.now(timezone.utc) - timedelta(days=2),
        }
    )
    test_db = _get_db()
    days = await source._last_posted_days(test_db, url)
    # Must measure from the MOST RECENT (2 days), not the oldest (30 days).
    assert days <= 3, f"Expected ~2 (most recent), got {days} — oldest-post bug"
    assert days < source.COOLDOWN_DAYS, f"Should be within cooldown, got {days}"
    # Cleanup
    db.posts.delete_many({"project.url": normalized})


@pytest.mark.asyncio
async def test_snapshot_stores_thread_text_for_corpus():
    """Snapshots double as the @github-radar corroboration corpus.

    The title/description text is what the cross-source gate boundary-matches
    repos against — losing it would silently blind the gate's Reddit leg.
    """
    source = load_source()
    db = FakeDb()
    await source._save_reddit_snapshot(
        db,
        "https://www.reddit.com/r/LocalLLaMA/comments/abc/?utm_source=share",
        50,
        10,
        title="Check out github.com/owner/repo",
        description="Really solid new tool",
        subreddit="LocalLLaMA",
    )
    (doc,) = db.reddit_post_snapshots._docs
    assert doc["title"] == "Check out github.com/owner/repo"
    assert doc["description"] == "Really solid new tool"
    assert doc["subreddit"] == "LocalLLaMA"
    assert doc["url"] == "https://www.reddit.com/r/LocalLLaMA/comments/abc"
    assert doc["upvotes"] == 50
    assert doc["comments"] == 10
    assert "capturedAt" in doc

