"""Daily prune of hidden-gems' pre-publication ``signals`` snapshots.

@hidden-gems stores one star/fork snapshot per candidate per day with an empty
``postId``. Snapshots older than 60 days no longer feed any momentum window, so
they are deleted; rows tied to a published post are history and are kept.

``signals`` is time-series: ``delete_many`` is allowed on it, but not inside a
transaction, so this runs as a plain delete.

Usage (the daily workflow runs it after the agents):
    PYTHONPATH=integrations python -m _shared.signal_prune
"""

import asyncio
from datetime import datetime, timedelta, timezone

from _shared import mongo

SNAPSHOT_RETENTION_DAYS = 60


async def prune_snapshot_signals(db, now: datetime | None = None) -> int:
    cutoff = (now or datetime.now(timezone.utc)) - timedelta(
        days=SNAPSHOT_RETENTION_DAYS
    )
    result = await db.signals.delete_many({"postId": "", "capturedAt": {"$lt": cutoff}})
    return result.deleted_count


async def main() -> None:
    try:
        deleted = await prune_snapshot_signals(mongo._get_db())
    finally:
        await mongo.close_client()
    print(
        f"pruned {deleted} snapshot signals older than {SNAPSHOT_RETENTION_DAYS} days"
    )


if __name__ == "__main__":
    asyncio.run(main())
