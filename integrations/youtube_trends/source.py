"""YouTube source — channel-scoped yt-dlp discovery for AI-dev channels.

Instead of generic `ytsearch` (which surfaces old popular videos), this scans
a curated allowlist of AI-dev YouTube channels for recent uploads, preserving
channel identity and view counts. View velocity is computed from daily view
snapshots stored in MongoDB (see view_velocity.py).

Channel-relative velocity normalizes by channel subscriber count so a
5K-view video from a 1K-subscriber channel scores higher than a 5K-view
video from a 100K-subscriber channel.
"""

import asyncio
import contextlib
import json
import logging
import os
import shutil
import signal
from datetime import datetime, timedelta, timezone

from _shared.heat import compute_heat_score, should_publish_heat


# Curated AI-dev channel allowlist (verified handles from research).
CHANNELS = [
    "https://www.youtube.com/@ChaseAI/videos",
    "https://www.youtube.com/@AIEngineer/videos",
    "https://www.youtube.com/@MatthewBerman/videos",
    "https://www.youtube.com/@IndyDevDan/videos",
    "https://www.youtube.com/@ShawTalebi/videos",
    "https://www.youtube.com/@HaykSimonyan/videos",
    "https://www.youtube.com/@MattPocock/videos",
    "https://www.youtube.com/@DevOpsRoundup/videos",
    "https://www.youtube.com/@PeterYang/videos",
    "https://www.youtube.com/@AlbertOlgaard/videos",
    "https://www.youtube.com/@AICodeKing/videos",
    "https://www.youtube.com/@freeCodeCamp/videos",
    "https://www.youtube.com/@PlatformEngineering/videos",
    "https://www.youtube.com/@RezaDorrani/videos",
    "https://www.youtube.com/@LangChain/videos",
    "https://www.youtube.com/@ColeMedin/videos",
    "https://www.youtube.com/@AndrejKarpathy/videos",
    "https://www.youtube.com/@AssemblyAI/videos",
    "https://www.youtube.com/@LexFridman/videos",
    "https://www.youtube.com/@TwoMinutePapers/videos",
    "https://www.youtube.com/@WesRoth/videos",
    "https://www.youtube.com/@MattWolfe/videos",
    "https://www.youtube.com/@AILuke/videos",
]
SOURCE_COMMAND_TIMEOUT_SECONDS = 120
SOURCE_COMMAND_CLEANUP_TIMEOUT_SECONDS = 5
YOUTUBE_FETCH_CONCURRENCY = 8


async def _stop_source_process(proc, communication) -> None:
    if proc.returncode is None:
        pid = getattr(proc, "pid", None)
        if isinstance(pid, int):
            try:
                os.killpg(pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
            except PermissionError:
                with contextlib.suppress(ProcessLookupError):
                    proc.kill()
        else:
            with contextlib.suppress(ProcessLookupError):
                proc.kill()
    try:
        await asyncio.wait_for(
            asyncio.shield(communication),
            timeout=SOURCE_COMMAND_CLEANUP_TIMEOUT_SECONDS,
        )
    except TimeoutError:
        logging.error("yt-dlp process cleanup exceeded its deadline")
    except Exception as error:
        logging.error("yt-dlp process cleanup failed: %s", error)


async def _fetch_one_channel(channel_url: str, cutoff: str) -> list[dict]:
    """Fetch recent videos from a single channel. Soft-fail: returns [] on error."""
    try:
        proc = await asyncio.create_subprocess_exec(
            "yt-dlp",
            "--dump-json",
            "--dateafter",
            cutoff,  # only videos from the last 14 days
            "--playlist-end",
            "10",  # 10 most recent per channel (full metadata, not flat)
            "--no-warnings",
            channel_url,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            start_new_session=True,
        )
        communication = asyncio.create_task(proc.communicate())
        try:
            stdout, _ = await asyncio.wait_for(
                asyncio.shield(communication),
                timeout=SOURCE_COMMAND_TIMEOUT_SECONDS,
            )
        except TimeoutError:
            await _stop_source_process(proc, communication)
            logging.warning("yt-dlp timed out for %s", channel_url)
            return []
        except asyncio.CancelledError:
            await _stop_source_process(proc, communication)
            raise
        output = stdout.decode()

        results = []
        for line in output.strip().split("\n"):
            line = line.strip()
            if not line:
                continue
            try:
                metadata = json.loads(line)
            except json.JSONDecodeError:
                continue
            if not isinstance(metadata, dict):
                continue
            vid_id = str(metadata.get("id") or "").strip()
            title = str(metadata.get("title") or "").strip()
            channel = str(
                metadata.get("channel") or metadata.get("uploader") or "Unknown channel"
            ).strip()
            if not vid_id or not title:
                continue
            views = metadata.get("view_count")
            try:
                view_count = int(views or 0)
            except (TypeError, ValueError):
                view_count = 0
            if view_count == 0:
                continue

            upload_date = str(metadata.get("upload_date") or "").strip()
            subs = metadata.get("channel_subscriber_count")
            try:
                channel_subscribers = int(subs or 0)
            except (TypeError, ValueError):
                channel_subscribers = 0
            likes = metadata.get("like_count")
            try:
                like_count = int(likes or 0)
            except (TypeError, ValueError):
                like_count = 0

            results.append(
                {
                    "url": f"https://www.youtube.com/watch?v={vid_id}",
                    "title": title[:200],
                    "kind": "video",
                    "description": f"By {channel} · {view_count:,} views",
                    "topics": [
                        "youtube",
                        "ai",
                        "video",
                        channel.lower().replace(" ", "-"),
                    ],
                    "channel": channel,
                    "viewCount": view_count,
                    "uploadDate": upload_date,
                    "channel_url": channel_url,
                    "channel_subscribers": channel_subscribers,
                    "like_count": like_count,
                }
            )
        return results
    except Exception as e:
        logging.warning("youtube_source fetch failed for %s: %s", channel_url, e)
        return []


async def fetch_youtube_candidates(max_results: int = 8) -> list[dict]:
    """Discover recent AI-dev videos from a curated channel allowlist.

    Scans each channel's /videos page for recent uploads via yt-dlp, preserving
    channel identity, view counts, and channel subscriber count. Raises
    RuntimeError if yt-dlp is not in PATH.
    """
    if not shutil.which("yt-dlp"):
        raise RuntimeError(
            "yt-dlp not found in PATH — install with: brew install yt-dlp"
        )
    cutoff = (datetime.now(timezone.utc) - timedelta(days=14)).strftime("%Y%m%d")
    # Bounded-async fetch: all channels concurrently (up to the concurrency
    # limit), soft-fail per channel (one timeout does not blank the run).
    sem = asyncio.Semaphore(YOUTUBE_FETCH_CONCURRENCY)

    async def _bounded(channel_url: str) -> list[dict]:
        async with sem:
            return await _fetch_one_channel(channel_url, cutoff)

    tasks = [asyncio.ensure_future(_bounded(url)) for url in CHANNELS]
    candidates: list[dict] = []
    for coro in asyncio.as_completed(tasks):
        videos = await coro
        candidates.extend(videos)

    # Deduplicate by URL, prioritize by view count.
    seen = set()
    unique = []
    for c in candidates:
        if c["url"] not in seen:
            seen.add(c["url"])
            unique.append(c)
    unique.sort(key=lambda x: x.get("viewCount", 0), reverse=True)
    return unique[:max_results]


async def fetch_youtube_candidates_with_velocity(
    max_results: int = 8,
) -> list[dict]:
    """Discover recent AI-dev videos, filtering for view velocity > 0.

    Wraps fetch_youtube_candidates with view velocity tracking:
    1. Fetch raw candidates via yt-dlp.
    2. Save a daily view snapshot for each discovered video.
    3. Compute view velocity (views gained in last 7 days) from snapshots.
    4. Compute channel-relative velocity (normalized by subscriber count).
    5. Only include videos with velocity > 0.

    First discovery (no prior snapshots) passes through — the snapshot is
    saved as a baseline for future velocity computation.
    """
    from _shared.mongo import _get_db
    from view_velocity import (
        channel_relative_velocity,
        compute_view_velocity,
        save_view_snapshot,
    )

    candidates = await fetch_youtube_candidates(max_results=max_results)
    if not candidates:
        return []

    result = []
    for c in candidates:
        url = c["url"]
        view_count = c.get("viewCount", 0)
        channel_subs = c.get("channel_subscribers", 0)
        # Read prior snapshots BEFORE saving today's so today's snapshot
        # does not become the "current" baseline.
        db = _get_db()
        cursor = (
            db.youtube_view_snapshots.find({"url": url})
            .sort("capturedAt", -1)
            .limit(100)
        )
        prior_snapshots = await cursor.to_list(length=100)
        velocity = compute_view_velocity(view_count, prior_snapshots)
        # Save today's snapshot for every discovered video.
        await save_view_snapshot(url, view_count)
        # Keep all non-zero-view videos — the publish gate filters, not the
        # 7-day velocity. The day-1 heat scorer (views/hour/subscriber) works
        # from the first fetch, so we no longer drop videos that lack 7-day
        # snapshot history.
        c["viewVelocity"] = velocity
        c["channelRelativeVelocity"] = channel_relative_velocity(velocity, channel_subs)
        # Delta-based discovery: views gained since the last run's snapshot.
        # The most recent snapshot (before saving today's) is the last-run baseline.
        last_views = prior_snapshots[0].get("viewCount", 0) if prior_snapshots else 0
        c["views_delta"] = max(0, view_count - last_views)
        result.append(c)

    attach_youtube_heat(result)

    # Publish gate: cooldown (14d) + threshold + noise floor + outperform.
    # This is the fix for the re-post-every-run bug — a video already published
    # within the cooldown is skipped, and only genuine breakouts surface.
    gate_db = _get_db()
    gated: list[dict] = []
    for c in result:
        heat_result = {
            "heat_score": c["heat_score"],
            "outperform_ratio": c["outperform_ratio"],
            "baseline_confidence": c["baseline_confidence"],
        }
        item = {
            "view_count": c.get("viewCount", 0),
            "age_hours": c.get("age_hours", 0.0),
            "channel_subscribers": c.get("channel_subscribers", 0),
        }
        last_days = await _last_posted_days(gate_db, c["url"])
        if should_publish_heat("youtube", heat_result, item, last_days):
            gated.append(c)

    gated.sort(key=lambda c: (c["heat_score"], c.get("views_delta", 0)), reverse=True)
    return gated[:max_results]


_DATE_FORMAT = "%Y%m%d"


def _upload_date_to_age_hours(upload_date: str, now: datetime | None = None) -> float:
    """Parse a yt-dlp upload_date (YYYYMMDD) to the video's age in hours.

    Returns 0.0 when the date is missing or unparseable — the shared scorer's
    min-age guard then zeroes the velocity component, so a bad date degrades
    gracefully rather than crashing the run.
    """
    if not upload_date or len(upload_date) < 8:
        return 0.0
    now = now or datetime.now(timezone.utc)
    try:
        parsed = datetime.strptime(upload_date[:8], _DATE_FORMAT).replace(
            tzinfo=timezone.utc
        )
    except ValueError:
        return 0.0
    return max(0.0, (now - parsed).total_seconds() / 3600)


def attach_youtube_heat(
    candidates: list[dict], now: datetime | None = None
) -> list[dict]:
    """Attach a shared heat_score to each candidate (beside existing fields).

    Expand phase: adds ``heat_score``, ``outperform_ratio``, and
    ``baseline_confidence`` without removing the existing view-velocity fields.
    The baseline for each video is the other recent videos from the same
    channel (their ``viewCount`` + age), passed to the shared scorer.
    """
    if not candidates:
        return candidates
    now = now or datetime.now(timezone.utc)
    # Precompute age_hours for the baseline + item payloads.
    ages = {
        id(c): _upload_date_to_age_hours(c.get("uploadDate", ""), now)
        for c in candidates
    }
    by_channel: dict[str, list[dict]] = {}
    for c in candidates:
        by_channel.setdefault(c.get("channel", ""), []).append(c)
    for c in candidates:
        channel = c.get("channel", "")
        baseline = [
            {
                "view_count": other.get("viewCount", 0),
                "age_hours": ages[id(other)],
            }
            for other in by_channel.get(channel, [])
            if other is not c
        ]
        item = {
            "view_count": c.get("viewCount", 0),
            "age_hours": ages[id(c)],
            "channel_subscribers": c.get("channel_subscribers", 0),
            "like_count": c.get("like_count"),
        }
        result = compute_heat_score("youtube", item, baseline, prior_posts=0)
        c["heat_score"] = result["heat_score"]
        c["outperform_ratio"] = result["outperform_ratio"]
        c["baseline_confidence"] = result["baseline_confidence"]
        c["age_hours"] = ages[id(c)]
    return candidates


async def _last_posted_days(db, project_url: str) -> int:
    """Days since the most recent post for this URL. 999 if never posted."""
    post = await db.posts.find_one(
        {"project.url": project_url},
        {"postedAt": 1},
        sort=[("postedAt", -1)],
    )
    if not post or not post.get("postedAt"):
        return 999
    posted_at = post["postedAt"]
    if posted_at.tzinfo is None:
        posted_at = posted_at.replace(tzinfo=timezone.utc)
    delta = datetime.now(timezone.utc) - posted_at
    return max(0, delta.days)
