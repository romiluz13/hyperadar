"""GitHub star-history backfill — kill the momentum cold-start gap.

A repo is invisible to the Momentum Score gate until it has ≥7 of our own
daily snapshots (and full 30-day scoring needs 31), so every newly discovered
repo waits a week before it is even scoreable — the opposite of "before
consensus". GitHub's official star-history endpoint
(``GET /repos/{owner}/{repo}/stargazers/history``, API version 2026-03-10)
returns real weekly star deltas with a per-day breakdown, so the tracker can
seed a repo's missing history from GitHub's own records the day it is first
seen.

Contract notes (verified live from this runtime, 200 OK):
- Response: newest-first ``[{week: <unix ts of Sunday>, total, days[Sun..Sat]}]``
  where ``days`` are per-day star deltas (``total`` = sum of ``days``).
- ``per_page`` counts weeks (max 30). Six weeks covers the largest momentum
  window (30 days + current) with margin.
- Week/day boundaries are not guaranteed UTC-aligned; day granularity is an
  acceptable approximation for momentum scoring.
- No auth required for public repos; ``GITHUB_TOKEN`` raises the rate limit.

Authority rule: our own daily snapshots stay the source of truth. Backfill
only inserts days the signals collection does not already have, deduplicated
by calendar date, and never overwrites an organic snapshot.
"""

import logging
import os
from collections.abc import Sequence
from datetime import date, datetime, time, timedelta, timezone

import httpx

from _shared.engagement import repo_slug

_API_URL = "https://api.github.com/repos/{slug}/stargazers/history"
_API_VERSION = "2026-03-10"
# Six weeks of history (42 days): the largest momentum window is the 30-day
# consistency check, so six weeks covers it plus partial-week margin.
_WEEKS_PER_REQUEST = 6
# A repo with this many snapshots has full 30-day scoring coverage; below it,
# backfill can still add real evidence (young repos, or gaps from days the
# tracker missed). Self-extinguishing: after one backfill the repo clears the
# threshold and the API is never called for it again.
_MIN_DENSE_SNAPSHOTS = 31
# Mid-day UTC: the momentum math only uses capturedAt for ordering, so any
# consistent time-of-day keeps backfilled docs sorted between their neighbors.
_CAPTURE_TIME = time(12, 0)

_github_token = os.environ.get("GITHUB_TOKEN", "")
_headers = {
    "Accept": "application/vnd.github+json",
    "X-GitHub-Api-Version": _API_VERSION,
}
if _github_token:
    _headers["Authorization"] = f"token {_github_token}"


def reconstruct_daily_counts(
    entries: Sequence[dict], current_stars: int, today: date | None = None
) -> list[tuple[date, int]]:
    """Reconstruct absolute per-day star counts from star-history entries.

    ``entries`` is the API's newest-first list of weekly buckets. ``days``
    are per-day deltas, so the count at the end of day D is
    ``current_stars - sum(deltas for days after D)``. Returns
    ``[(date, stars_at_end_of_day)]`` ordered oldest→newest, containing only
    complete days (``date < today``); today's partial delta belongs to the
    tracker's own snapshot. Counts are clamped at 0 so un-star noise cannot
    fabricate a non-monotonic series.
    """
    if today is None:
        today = datetime.now(timezone.utc).date()

    daily: list[tuple[date, int]] = []
    for entry in entries or []:
        week_start = datetime.fromtimestamp(entry["week"], tz=timezone.utc).date()
        for i, delta in enumerate(entry.get("days") or []):
            daily.append((week_start + timedelta(days=i), int(delta)))
    if not daily:
        return []

    daily.sort(key=lambda pair: pair[0], reverse=True)  # newest first
    points: list[tuple[date, int]] = []
    after = 0  # sum of deltas for days strictly after the day being recorded
    for day, delta in daily:
        if day < today:
            points.append((day, max(0, current_stars - after)))
        after += delta
    points.reverse()  # oldest first
    return points


async def fetch_star_history(
    slug: str, client: httpx.AsyncClient | None = None
) -> list[dict]:
    """Fetch the repo's star-history weekly buckets (newest first)."""
    owns_client = client is None
    if client is None:
        client = httpx.AsyncClient(timeout=20)
    try:
        r = await client.get(
            _API_URL.format(slug=slug),
            params={"per_page": _WEEKS_PER_REQUEST},
            headers=_headers,
        )
        r.raise_for_status()
        data = r.json()
        if not isinstance(data, list):
            raise ValueError(f"unexpected star-history shape: {type(data).__name__}")
        return data
    finally:
        if owns_client:
            await client.aclose()


async def backfill_star_history(
    db,
    project_id: str,
    current_stars: int,
    current_forks: int,
    client: httpx.AsyncClient | None = None,
) -> int:
    """Seed a repo's missing daily snapshots from its real GitHub star history.

    Skips repos that already have dense history (``_MIN_DENSE_SNAPSHOTS``),
    skips non-GitHub project ids, and inserts only calendar days the signals
    collection does not already have for the repo — organic snapshots remain
    the authority. Degrades to 0 (with a warning) on any API or parse failure:
    backfill is enrichment and must never fail the tracker run. Returns the
    number of snapshots inserted.
    """
    slug = repo_slug(project_id)
    if not slug:
        return 0

    existing = await db.signals.find(
        {"projectId": project_id}, {"capturedAt": 1}
    ).to_list(None)
    if len(existing) >= _MIN_DENSE_SNAPSHOTS:
        return 0
    existing_dates = {
        doc["capturedAt"].date()
        if doc["capturedAt"].tzinfo is None
        else doc["capturedAt"].astimezone(timezone.utc).date()
        for doc in existing
    }

    try:
        entries = await fetch_star_history(slug, client=client)
    except Exception as e:
        logging.warning("Star-history backfill skipped for '%s': %s", project_id, e)
        return 0

    today = datetime.now(timezone.utc).date()
    points = reconstruct_daily_counts(entries, current_stars, today)
    docs = [
        {
            "capturedAt": datetime.combine(
                day, _CAPTURE_TIME, tzinfo=timezone.utc
            ),
            "projectId": project_id,
            "postId": "",
            "github_stars": stars,
            "github_forks": current_forks,
        }
        for day, stars in points
        if day not in existing_dates
    ]
    if not docs:
        return 0

    await db.signals.insert_many(docs)
    logging.info(
        "Star-history backfill: seeded %d daily snapshots for %s",
        len(docs),
        project_id,
    )
    return len(docs)
