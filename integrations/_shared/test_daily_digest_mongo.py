"""generate_daily_digest against a real MongoDB test database.

Seam: ``generate_daily_digest(db, now)`` with the shared ``db`` fixture, so the
posts query, the 30-day memory and the ``digests`` upsert run on real queries.
Grove and GitHub are faked. Runs only with ``MONGODB_URI`` set (conftest).
"""

from datetime import datetime, timedelta, timezone

from _shared import daily_digest, mongo

# Far from any other suite's posts, so the 24h window holds only these.
NOW = datetime(2031, 1, 15, 8, 45, tzinfo=timezone.utc)
TAG = "test-daily-digest-mongo"
REPO_URL = "https://github.com/acme/slot-test-repo"
THREAD_URL = "https://www.reddit.com/r/LocalLLaMA/comments/slottest"
THREAD_BODY = ("A local agent tracer that replays every tool call. " * 5).strip()
THREAD_WHAT = "A local agent tracer that replays every tool call."


def _post(handle: str, kind: str, url: str, signal: dict, **project) -> dict:
    return {
        "testTag": TAG,
        "agentHandle": handle,
        "body": "body",
        "verdict": "emerging",
        "rankScore": 70,
        "postedAt": NOW - timedelta(hours=2),
        "portSyncStatus": "synced",
        "evidenceContractVersion": 2,
        "signal": signal,
        "project": {
            "title": url.rsplit("/", 1)[1],
            "url": url,
            "kind": kind,
            "description": "x",
            **project,
        },
    }


def _seed(db, monkeypatch):
    _cleanup(db)
    db.posts.insert_many(
        [
            _post(
                "@reddit-pulse",
                "thread",
                THREAD_URL,
                {"source": "reddit", "metric": "upvotes", "value": 2040, "delta": 63},
                bodyText=THREAD_BODY,
                contentHref="",
            ),
            _post(
                "@github-radar",
                "repo",
                REPO_URL,
                {"source": "github", "metric": "github_stars", "value": 567},
            ),
        ]
    )

    async def fake_batch(slugs, now=None, client=None):
        weekly = {"starsThisWeek": 280, "starsToday": 30, "days": []}
        return {"acme/slot-test-repo": weekly}, {}

    async def no_lookup(*_args, **_kwargs):
        return None

    async def fake_grove(entries):
        # Only the thread gets a `what`, which a Reddit slot needs (task 22).
        return [
            {"id": e["id"], "what": THREAD_WHAT}
            for e in entries
            if e["agentHandle"] == "@reddit-pulse"
        ]

    monkeypatch.setattr(daily_digest, "fetch_stars_this_week_batch", fake_batch)
    async def no_readme(*_args, **_kwargs):
        return ""

    monkeypatch.setattr(daily_digest, "_fetch_github_repo", no_lookup)
    monkeypatch.setattr(daily_digest, "_fetch_github_readme", no_readme)
    monkeypatch.setattr(daily_digest, "fetch_hn_engagement", no_lookup)
    monkeypatch.setattr(daily_digest, "fetch_hn_story", no_lookup)
    monkeypatch.setattr(daily_digest, "_call_grove", fake_grove)


def _cleanup(db):
    db.posts.delete_many({"testTag": TAG})
    db.trending_snapshots.delete_many({"testTag": TAG})
    db.digests.delete_many({"testTag": TAG})
    db.digests.delete_many({"date": NOW.strftime("%Y-%m-%d"), "digestType": "daily"})


async def test_the_stored_digest_leads_with_the_repo_and_reruns_upsert(db, monkeypatch):
    _seed(db, monkeypatch)
    try:
        await daily_digest.generate_daily_digest(db=mongo._get_db(), now=NOW)
        await daily_digest.generate_daily_digest(db=mongo._get_db(), now=NOW)

        stored = list(
            db.digests.find({"date": NOW.strftime("%Y-%m-%d"), "digestType": "daily"})
        )
        assert len(stored) == 1
        assert [item["url"] for item in stored[0]["items"]] == [REPO_URL, THREAD_URL]
        assert stored[0]["belowFloor"] is False
    finally:
        _cleanup(db)


async def test_a_repo_featured_10_days_ago_is_not_repeated(db, monkeypatch):
    _seed(db, monkeypatch)
    db.digests.insert_one(
        {
            "testTag": TAG,
            "date": (NOW - timedelta(days=10)).strftime("%Y-%m-%d"),
            "digestType": "daily",
            "createdAt": NOW - timedelta(days=10),
            "items": [{"url": REPO_URL}],
        }
    )
    try:
        result = await daily_digest.generate_daily_digest(db=mongo._get_db(), now=NOW)

        assert [item["url"] for item in result["items"]] == [THREAD_URL]
        assert result["belowFloor"] is True
    finally:
        _cleanup(db)


async def test_the_items_carry_the_stat_fields_and_today_s_trending_rank(
    db, monkeypatch
):
    _seed(db, monkeypatch)
    day_rows = [
        ("acme/Slot-Test-Repo", 7, "all/daily", NOW - timedelta(hours=3)),
        ("acme/slot-test-repo", 1, "all/daily", NOW - timedelta(days=1)),
    ]
    db.trending_snapshots.insert_many(
        [
            {"testTag": TAG, "slug": slug, "rank": rank, "page": page, "capturedAt": at}
            for slug, rank, page, at in day_rows
        ]
    )
    try:
        result = await daily_digest.generate_daily_digest(db=mongo._get_db(), now=NOW)

        repo, thread = result["items"]
        assert (repo["trendingRank"], repo["trendingSince"]) == (7, "daily")
        assert (repo["starsThisWeek"], repo["starsToday"]) == (280, 30)
        assert (repo["weeklyGain"], repo["weeklyGainWindowDays"]) == (280, 7)
        assert (thread["upvotes"], thread["comments"]) == (2040, 63)
        assert thread["trendingRank"] is None
        assert thread["what"] == THREAD_WHAT
        assert result["whatRejections"] == 1
    finally:
        _cleanup(db)
