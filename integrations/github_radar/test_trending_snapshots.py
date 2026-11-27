"""DB tests for the trending_snapshots writer (needs MONGODB_URI).

The 30-day TTL index belongs to setup (scripts/test_setup_mongodb.py).
"""

from datetime import datetime, timezone


def _rows():
    import trending_source

    daily = trending_source.TrendingPage(language=None, since="daily")
    python = trending_source.TrendingPage(language="python", since="daily")
    return [
        trending_source.TrendingRow(
            page=daily,
            rank=2,
            slug="obra/superpowers",
            description="An agentic skills framework & software development methodology that works.",
            language="Shell",
            stars=327_351,
            forks=29_029,
            window_stars=853,
            window="daily",
        ),
        trending_source.TrendingRow(
            page=python,
            rank=1,
            slug="elementalsouls/Claude-BugHunter",
            description="",
            language=None,
            stars=24_333,
            forks=2_476,
            window_stars=1_600,
            window="daily",
        ),
    ]


async def test_each_row_is_written_with_its_capture_time(db):
    import trending_source
    from _shared import mongo

    captured_at = datetime(2026, 11, 26, 0, 23, 7, tzinfo=timezone.utc)
    db.trending_snapshots.delete_many({"capturedAt": captured_at})
    try:
        written = await trending_source.write_trending_snapshots(
            mongo._get_db(), _rows(), captured_at=captured_at
        )

        assert written == 2
        docs = list(
            db.trending_snapshots.find({"capturedAt": captured_at}, {"_id": 0}).sort(
                "rank", -1
            )
        )
        assert docs == [
            {
                "date": "2026-11-26",
                "page": "all/daily",
                "rank": 2,
                "slug": "obra/superpowers",
                "windowStars": 853,
                "description": "An agentic skills framework & software development methodology that works.",
                "language": "Shell",
                "capturedAt": captured_at.replace(tzinfo=None),
            },
            {
                "date": "2026-11-26",
                "page": "python/daily",
                "rank": 1,
                "slug": "elementalsouls/Claude-BugHunter",
                "windowStars": 1_600,
                "description": "",
                "language": None,
                "capturedAt": captured_at.replace(tzinfo=None),
            },
        ]
    finally:
        db.trending_snapshots.delete_many({"capturedAt": captured_at})


async def test_an_empty_run_writes_nothing(db):
    import trending_source
    from _shared import mongo

    captured_at = datetime(2026, 11, 26, 0, 23, 9, tzinfo=timezone.utc)

    written = await trending_source.write_trending_snapshots(
        mongo._get_db(), [], captured_at=captured_at
    )

    assert written == 0
    assert db.trending_snapshots.count_documents({"capturedAt": captured_at}) == 0
