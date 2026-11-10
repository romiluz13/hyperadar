"""Tests for fetch_breakout_candidates and hidden_gem_momentum_copy."""

from datetime import datetime, timedelta, timezone

import pytest

from _shared.evidence_copy import hidden_gem_momentum_copy
from _shared import mongo


def _snapshots(stars_sequence, forks=None):
    """Build N daily signal snapshots from a star-count sequence."""
    base = datetime.now(timezone.utc).replace(
        hour=0, minute=0, second=0, microsecond=0
    ) - timedelta(days=len(stars_sequence) - 1)
    snapshots = []
    for i, stars in enumerate(stars_sequence):
        snapshots.append(
            {
                "capturedAt": base + timedelta(days=i),
                "projectId": "https://github.com/test/breakout-repo",
                "postId": "",
                "github_stars": stars,
                "github_forks": forks if forks is not None else max(1, stars // 10),
            }
        )
    return snapshots


# ─── hidden_gem_momentum_copy ───


def test_momentum_copy_includes_score():
    text = hidden_gem_momentum_copy(78, 12, 3)
    assert "78" in text
    assert "Momentum" in text or "momentum" in text


def test_momentum_copy_includes_velocity():
    text = hidden_gem_momentum_copy(78, 12, 3)
    assert "12" in text
    assert "star" in text.lower()


def test_momentum_copy_includes_acceleration():
    text = hidden_gem_momentum_copy(78, 12, 5)
    assert "accelerat" in text.lower() or "breakout" in text.lower()


# ─── fetch_breakout_candidates ───


@pytest.mark.asyncio
async def test_breakout_returns_only_passing_repos(db, monkeypatch):
    """A repo with accelerating growth passes the gate; a flat repo does not."""
    from hidden_gems import source

    # Mock track_daily_snapshots — no network during tests.
    async def fake_track(db):
        return 0

    monkeypatch.setattr(source, "track_daily_snapshots", fake_track)

    # No HN story → no engagement boost → the raw gate score is asserted below.
    async def fake_engagement(repo_url, client=None):
        return None

    monkeypatch.setattr(source, "fetch_hn_engagement", fake_engagement)

    async_db = mongo._get_db()

    # Accelerating repo: 14 days, stars growing from 20→72 (4/day)
    accel_stars = [20 + i * 4 for i in range(14)]
    accel_snaps = _snapshots(accel_stars, forks=10)
    for s in accel_snaps:
        s["projectId"] = "https://github.com/test/accelerating-repo"

    # Flat repo: 14 days, no growth
    flat_stars = [100] * 14
    flat_snaps = _snapshots(flat_stars, forks=10)
    for s in flat_snaps:
        s["projectId"] = "https://github.com/test/flat-repo"

    # Short-history repo: only 3 days — should be excluded (< 7 days)
    short_snaps = _snapshots([10, 15, 20], forks=2)
    for s in short_snaps:
        s["projectId"] = "https://github.com/test/short-repo"

    all_urls = [
        "https://github.com/test/accelerating-repo",
        "https://github.com/test/flat-repo",
        "https://github.com/test/short-repo",
    ]

    # Seed signals
    db.signals.delete_many({"projectId": {"$in": all_urls}})
    db.posts.delete_many({"project.url": {"$in": all_urls}})
    db.signals.insert_many(accel_snaps + flat_snaps + short_snaps)

    try:
        results = await source.fetch_breakout_candidates(async_db)

        # The accelerating repo should pass; the flat and short repos should not
        result_urls = [r["url"] for r in results]
        assert "https://github.com/test/accelerating-repo" in result_urls
        assert "https://github.com/test/flat-repo" not in result_urls
        assert "https://github.com/test/short-repo" not in result_urls

        # The passing candidate should include momentum score and velocity
        passing = [
            r
            for r in results
            if r["url"] == "https://github.com/test/accelerating-repo"
        ][0]
        assert "momentumScore" in passing
        assert "velocity" in passing
        assert passing["momentumScore"] >= 55
        assert passing["velocity"] > 0
    finally:
        db.signals.delete_many({"projectId": {"$in": all_urls}})
        db.posts.delete_many({"project.url": {"$in": all_urls}})


@pytest.mark.asyncio
async def test_breakout_excludes_recently_published(db, monkeypatch):
    """A repo published <14 days ago should not be returned even if it passes."""
    from hidden_gems import source

    async def fake_track(db):
        return 0

    monkeypatch.setattr(source, "track_daily_snapshots", fake_track)

    # Hermetic even if the gate order changes: no live HN in tests.
    async def fake_engagement(repo_url, client=None):
        return None

    monkeypatch.setattr(source, "fetch_hn_engagement", fake_engagement)

    async_db = mongo._get_db()
    repo_url = "https://github.com/test/recently-published"

    # Accelerating history
    accel_stars = [20 + i * 3 for i in range(14)]
    accel_snaps = _snapshots(accel_stars, forks=10)
    for s in accel_snaps:
        s["projectId"] = repo_url

    # Insert a post from 3 days ago
    recent_post = {
        "agentHandle": "@hidden-gems",
        "body": "test post",
        "postedAt": datetime.now(timezone.utc) - timedelta(days=3),
        "project": {"url": repo_url, "title": "Recent Repo"},
        "portSyncStatus": "synced",
    }

    db.signals.delete_many({"projectId": repo_url})
    db.posts.delete_many({"project.url": repo_url})
    db.signals.insert_many(accel_snaps)
    db.posts.insert_one(recent_post)

    try:
        results = await source.fetch_breakout_candidates(async_db)
        result_urls = [r["url"] for r in results]
        assert repo_url not in result_urls, (
            "Recently published repo should be excluded by cooldown gate"
        )
    finally:
        db.signals.delete_many({"projectId": repo_url})
        db.posts.delete_many({"project.url": repo_url})


@pytest.mark.asyncio
async def test_breakout_cooldown_counts_from_newest_post(db, monkeypatch):
    """THE regression: an OLD post plus a RECENT post excludes the repo.

    The pre-fix unsorted ``find_one`` returned the old post, so recycled repos
    sailed through the cooldown daily.
    """
    from hidden_gems import source

    async def fake_track(db):
        return 0

    monkeypatch.setattr(source, "track_daily_snapshots", fake_track)

    # Hermetic even if the gate order changes: no live HN in tests.
    async def fake_engagement(repo_url, client=None):
        return None

    monkeypatch.setattr(source, "fetch_hn_engagement", fake_engagement)

    async_db = mongo._get_db()
    repo_url = "https://github.com/test/gems-newest-post-cooldown"

    accel_stars = [20 + i * 3 for i in range(14)]
    accel_snaps = _snapshots(accel_stars, forks=10)
    for s in accel_snaps:
        s["projectId"] = repo_url

    db.signals.delete_many({"projectId": repo_url})
    db.posts.delete_many({"project.url": repo_url})
    db.signals.insert_many(accel_snaps)
    db.posts.insert_many(
        [
            {
                "agentHandle": "@hidden-gems",
                "body": "old post",
                "postedAt": datetime.now(timezone.utc) - timedelta(days=45),
                "project": {"url": repo_url, "title": "Recycled Gem"},
                "portSyncStatus": "synced",
            },
            {
                "agentHandle": "@github-radar",
                "body": "recent post",
                "postedAt": datetime.now(timezone.utc) - timedelta(days=1),
                "project": {"url": repo_url, "title": "Recycled Gem"},
                "portSyncStatus": "synced",
            },
        ]
    )

    try:
        results = await source.fetch_breakout_candidates(async_db)
        assert repo_url not in [r["url"] for r in results], (
            "Repo posted yesterday by another agent must be excluded even "
            "though it also has a 45-day-old post"
        )
    finally:
        db.signals.delete_many({"projectId": repo_url})
        db.posts.delete_many({"project.url": repo_url})


@pytest.mark.asyncio
async def test_breakout_returns_empty_when_no_history(db, monkeypatch):
    """No signals → empty list, no errors."""
    from hidden_gems import source

    async def fake_track(db):
        return 0

    monkeypatch.setattr(source, "track_daily_snapshots", fake_track)

    async_db = mongo._get_db()
    results = await source.fetch_breakout_candidates(async_db)
    assert results == []


@pytest.mark.asyncio
async def test_breakout_engagement_boost_applied_post_gate(db, monkeypatch):
    """HN engagement raises the PUBLISHED score, never the gate decision.

    The pool-scaled threshold is computed from raw gate scores; the boost is
    applied after, so a repo cannot gate-crash via HN points — but a repo
    that passed carries engagement-weighted momentum and HN evidence fields.
    """
    from hidden_gems import source

    async def fake_track(db):
        return 0

    monkeypatch.setattr(source, "track_daily_snapshots", fake_track)

    repo_url = "https://github.com/test/engagement-boosted"
    holder = {"engagement": None}

    async def fake_engagement(url, client=None):
        assert url == repo_url
        return holder["engagement"]

    monkeypatch.setattr(source, "fetch_hn_engagement", fake_engagement)

    async_db = mongo._get_db()
    accel_stars = [20 + i * 4 for i in range(14)]
    accel_snaps = _snapshots(accel_stars, forks=10)
    for s in accel_snaps:
        s["projectId"] = repo_url

    db.signals.delete_many({"projectId": repo_url})
    db.posts.delete_many({"project.url": repo_url})
    db.signals.insert_many(accel_snaps)

    try:
        # Pass 1: no HN story — the raw gate score.
        raw = [
            r
            for r in await source.fetch_breakout_candidates(async_db)
            if r["url"] == repo_url
        ]
        assert len(raw) == 1
        base_score = raw[0]["momentumScore"]
        assert "hn_points" not in raw[0]

        # Pass 2: front-page HN story — published score rises by the boost.
        holder["engagement"] = {
            "points": 200,
            "comments": 100,
            "story_url": "https://news.ycombinator.com/item?id=1",
            "story_title": "Show HN: Engagement boosted",
            "top_comment": {"author": "alice", "text": "genuinely useful"},
        }
        boosted = [
            r
            for r in await source.fetch_breakout_candidates(async_db)
            if r["url"] == repo_url
        ]
        assert len(boosted) == 1
        expected = min(100, base_score + 15)  # full points (10) + comments (5)
        assert boosted[0]["momentumScore"] == expected
        assert boosted[0]["hn_points"] == 200
        assert boosted[0]["hn_comments"] == 100
        assert boosted[0]["hn_top_comment"]["author"] == "alice"
    finally:
        db.signals.delete_many({"projectId": repo_url})
        db.posts.delete_many({"project.url": repo_url})


@pytest.mark.asyncio
async def test_breakout_ignores_repos_no_longer_tracked(db, monkeypatch):
    """A repo whose last snapshot is older than a week cannot be breaking out now."""
    from hidden_gems import source

    async def fake_track(db):
        return 0

    async def fake_engagement(repo_url, client=None):
        return None

    monkeypatch.setattr(source, "track_daily_snapshots", fake_track)
    monkeypatch.setattr(source, "fetch_hn_engagement", fake_engagement)

    live_url = "https://github.com/test/live-breakout"
    stale_url = "https://github.com/test/stale-breakout"
    stars = [20 + i * 4 for i in range(14)]
    live = _snapshots(stars, forks=10)
    stale = _snapshots(stars, forks=10)
    for s in live:
        s["projectId"] = live_url
    for s in stale:
        s["projectId"] = stale_url
        s["capturedAt"] -= timedelta(days=10)

    urls = [live_url, stale_url]
    db.signals.delete_many({"projectId": {"$in": urls}})
    db.posts.delete_many({"project.url": {"$in": urls}})
    db.signals.insert_many(live + stale)

    try:
        result_urls = [
            r["url"] for r in await source.fetch_breakout_candidates(mongo._get_db())
        ]
        assert live_url in result_urls
        assert stale_url not in result_urls
    finally:
        db.signals.delete_many({"projectId": {"$in": urls}})
        db.posts.delete_many({"project.url": {"$in": urls}})


def _seed_breakout(db, repo_url, base_stars, forks=None):
    """An accelerating 14-day history starting at ``base_stars``, growth to scale."""
    scale = max(1, base_stars // 20)
    stars = [base_stars + scale * (i * 4 + i * i) for i in range(14)]
    snaps = _snapshots(stars, forks=forks if forks is not None else max(10, stars[-1] // 10))
    for s in snaps:
        s["projectId"] = repo_url
    db.signals.delete_many({"projectId": repo_url})
    db.posts.delete_many({"project.url": repo_url})
    db.signals.insert_many(snaps)


async def _breakout_urls(source, monkeypatch):
    async def fake_track(db):
        return 0

    async def fake_engagement(repo_url, client=None):
        return None

    monkeypatch.setattr(source, "track_daily_snapshots", fake_track)
    monkeypatch.setattr(source, "fetch_hn_engagement", fake_engagement)
    return [r["url"] for r in await source.fetch_breakout_candidates(mongo._get_db())]


@pytest.mark.asyncio
async def test_breakout_skips_repos_above_the_hidden_star_ceiling(db, monkeypatch):
    """A gem is hidden: a repo already past 2,000 stars is not one."""
    from hidden_gems import source

    small = "https://github.com/test/small-gem"
    famous = "https://github.com/test/famous-repo"
    # One repo per pool: the pool-scaled threshold must not decide this test.
    try:
        _seed_breakout(db, small, 20)
        assert small in await _breakout_urls(source, monkeypatch)
        db.signals.delete_many({"projectId": small})

        _seed_breakout(db, famous, 5000)
        assert famous not in await _breakout_urls(source, monkeypatch)
    finally:
        db.signals.delete_many({"projectId": {"$in": [small, famous]}})


@pytest.mark.asyncio
async def test_breakout_never_recycles_a_repo_any_agent_already_posted(db, monkeypatch):
    """Outside the 14-day cooldown a repo is still old news for @hidden-gems."""
    from hidden_gems import source

    repo_url = "https://github.com/test/already-posted-gem"
    _seed_breakout(db, repo_url, 20)
    db.posts.insert_one(
        {
            "agentHandle": "@github-radar",
            "body": "posted long ago",
            "postedAt": datetime.now(timezone.utc) - timedelta(days=45),
            "project": {"url": repo_url, "title": "Old news"},
            "portSyncStatus": "synced",
        }
    )
    try:
        assert repo_url not in await _breakout_urls(source, monkeypatch)
    finally:
        db.signals.delete_many({"projectId": repo_url})
        db.posts.delete_many({"project.url": repo_url})


def test_select_hidden_gems_caps_a_run_at_five_strongest():
    from hidden_gems.source import select_hidden_gems

    breakout = [
        {"url": f"https://github.com/t/r{i}", "momentumScore": score}
        for i, score in enumerate([60, 95, 70, 88, 91, 77])
    ]
    hn = [{"url": f"https://news.ycombinator.com/item?id={i}"} for i in range(4)]

    picked = select_hidden_gems(breakout, hn)

    assert [c["url"] for c in picked] == [
        "https://github.com/t/r1",
        "https://github.com/t/r4",
        "https://github.com/t/r3",
        "https://news.ycombinator.com/item?id=0",
        "https://news.ycombinator.com/item?id=1",
    ]
    # With no breakouts, Show HN fills the run up to the same cap.
    assert len(select_hidden_gems([], hn)) == 4
    assert len(select_hidden_gems(breakout, [])) == 5


@pytest.mark.asyncio
async def test_show_hn_keeps_only_unposted_repos_under_the_star_ceiling():
    """Show HN obeys the same gem rules: never posted by any agent, at most 2,000 stars."""
    import httpx

    from hidden_gems.source import keep_hidden_show_hn

    posted = {"https://github.com/acme/old"}

    class _Posts:
        async def find_one(self, query, *args, **kwargs):
            if query["project.url"] in posted:
                return {"postedAt": datetime.now(timezone.utc) - timedelta(days=60)}
            return None

    class _Db:
        posts = _Posts()

    stars = {"acme/small": 150, "acme/famous": 30000}

    def handler(request: httpx.Request) -> httpx.Response:
        slug = request.url.path.removeprefix("/repos/")
        if slug not in stars:
            return httpx.Response(404)
        return httpx.Response(200, json={"stargazers_count": stars[slug]})

    candidates = [
        {"url": "https://github.com/acme/small"},
        {"url": "https://github.com/acme/famous"},
        {"url": "https://github.com/acme/old"},
        {"url": "https://example.com/launch"},
        {"url": "https://github.com/acme/unknown"},
        {"url": "https://example.com/second-launch"},
    ]
    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        kept = await keep_hidden_show_hn(_Db(), candidates, limit=2, client=client)

    # acme/unknown: GitHub could not say, so the 2,000-star rule cannot be shown to hold.
    # The limit stops the scan once two gems are kept.
    assert [c["url"] for c in kept] == [
        "https://github.com/acme/small",
        "https://example.com/launch",
    ]


@pytest.mark.asyncio
async def test_show_hn_asks_only_for_launches_from_the_last_72_hours():
    """The all-time Show HN index returned 2012 classics; launches are news only when fresh."""
    import httpx

    from hidden_gems.source import fetch_hn_candidates

    seen = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(
            200,
            json={
                "hits": [
                    {"objectID": "1", "title": "Show HN: Small", "url": "https://x.dev", "points": 40, "num_comments": 3},
                    {"objectID": "2", "title": "Show HN: Big", "url": "https://github.com/a/b", "points": 300, "num_comments": 90},
                ]
            },
        )

    before = int(datetime.now(timezone.utc).timestamp()) - 72 * 3600
    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        candidates = await fetch_hn_candidates(max_results=5, client=client)

    (request,) = seen
    assert request.url.path == "/api/v1/search_by_date"
    assert request.url.params["tags"] == "show_hn"
    filters = request.url.params["numericFilters"].split(",")
    assert "points>30" in filters
    since = int(next(f for f in filters if f.startswith("created_at_i>")).split(">")[1])
    assert before - 5 <= since <= before + 5
    # Strongest launch first.
    assert [c["title"] for c in candidates] == ["Show HN: Big", "Show HN: Small"]
