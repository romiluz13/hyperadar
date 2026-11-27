"""DB test for the daily signals prune (ticket 09, decision Q19). Needs MONGODB_URI."""

import uuid
from datetime import datetime, timedelta, timezone

import pytest

from _shared import mongo, signal_prune

_NOW = datetime(2026, 11, 27, 1, tzinfo=timezone.utc)


@pytest.fixture
def signals(db):
    """The session's ``signals`` as production has it: time-series (setup step 1)."""
    (info,) = list(db.list_collections(filter={"name": "signals"})) or [None]
    if info is not None and info["type"] != "timeseries":
        db.signals.drop()
        info = None
    if info is None:
        db.create_collection(
            "signals",
            timeseries={
                "timeField": "capturedAt",
                "metaField": "projectId",
                "granularity": "hours",
            },
        )
    project = f"https://github.com/prune-test/{uuid.uuid4().hex[:8]}"
    yield db.signals, project
    db.signals.delete_many({"projectId": project})


async def test_prune_deletes_old_snapshots_and_keeps_recent_and_published_rows(
    signals,
):
    collection, project = signals
    collection.insert_many(
        [
            {
                "projectId": project,
                "capturedAt": _NOW - timedelta(days=61),
                "postId": "",
                "label": "old snapshot",
            },
            {
                "projectId": project,
                "capturedAt": _NOW - timedelta(days=59),
                "postId": "",
                "label": "recent snapshot",
            },
            {
                "projectId": project,
                "capturedAt": _NOW - timedelta(days=200),
                "postId": "post-a",
                "label": "old published",
            },
            {
                "projectId": project,
                "capturedAt": _NOW - timedelta(days=1),
                "postId": "post-b",
                "label": "recent published",
            },
        ]
    )

    deleted = await signal_prune.prune_snapshot_signals(mongo._get_db(), now=_NOW)

    kept = sorted(doc["label"] for doc in collection.find({"projectId": project}))
    assert kept == ["old published", "recent published", "recent snapshot"]
    assert deleted >= 1
