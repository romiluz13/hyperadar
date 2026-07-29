"""YouTube source — channel-scoped discovery for AI-dev channels via the
YouTube Data API v3.

Instead of generic `ytsearch` (which surfaces old popular videos), this scans
a curated allowlist of AI-dev YouTube channels for recent uploads, preserving
channel identity and view counts. View velocity is computed from daily view
snapshots stored in MongoDB (see view_velocity.py).

Channel-relative velocity normalizes by channel subscriber count so a
5K-view video from a 1K-subscriber channel scores higher than a 5K-view
video from a 100K-subscriber channel.

The YouTube Data API v3 (channels.list -> search.list -> videos.list) is used
instead of yt-dlp because YouTube's anti-bot returns 0 results on datacenter
IPs (the GHA runner). The API is a REST endpoint, so it works from any IP and
needs only a YOUTUBE_API_KEY (free, 10k units/day quota, ~2.3k used for 23
channels).
"""

import asyncio
import logging
import os
from datetime import datetime, timedelta, timezone
from urllib.parse import urlparse

import httpx

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
YOUTUBE_FETCH_CONCURRENCY = 8
YOUTUBE_API_BASE = "https://www.googleapis.com/youtube/v3"
YOUTUBE_LOOKBACK_DAYS = 14
YOUTUBE_API_TIMEOUT_SECONDS = 30
_AUTH_ERROR_MSG = "YouTube API auth failed — check YOUTUBE_API_KEY (401/403)"


def _is_auth_error(error: Exception) -> bool:
    """A 401/403 from the YouTube API means the key is bad/expired — a global
    failure, not a per-channel one, so the caller should raise rather than
    soft-fail (a silent [] would mask key-rotation issues)."""
    return isinstance(error, httpx.HTTPStatusError) and error.response.status_code in (
        401,
        403,
    )


def _safe_error_detail(error: Exception) -> str:
    """A short, secret-safe description of an API error for logs.

    Returns the HTTP status code for HTTP errors — NOT the full exception,
    whose message embeds the request URL and would leak the API key
    (httpx's raise_for_status() puts ``?key=...`` in the error string).
    """
    if isinstance(error, httpx.HTTPStatusError):
        return f"HTTP {error.response.status_code}"
    return type(error).__name__


def _handle_from_channel_url(channel_url: str) -> str:
    """Extract the @handle from a https://www.youtube.com/@handle/videos URL."""
    parts = [p for p in urlparse(channel_url).path.split("/") if p]
    return parts[0] if parts else ""


async def _youtube_api_get(path: str, params: dict) -> dict:
    """GET one YouTube Data API v3 endpoint. Returns the parsed JSON.

    Raises RuntimeError if YOUTUBE_API_KEY is unset.
    """
    key = os.environ.get("YOUTUBE_API_KEY", "").strip()
    if not key:
        raise RuntimeError(
            "YOUTUBE_API_KEY not set — required for the YouTube Data API v3 source"
        )
    async with httpx.AsyncClient(timeout=YOUTUBE_API_TIMEOUT_SECONDS) as client:
        resp = await client.get(YOUTUBE_API_BASE + path, params={**params, "key": key})
        resp.raise_for_status()
        return resp.json()


async def _fetch_one_channel_via_api(channel_url: str, max_results: int) -> list[dict]:
    """Resolve a channel + its recent uploads via the YouTube Data API v3.

    Returns a list of dicts (one per recent video) with video_id, title,
    publishedAt, channel, channel_url, channel_subscribers — the view/like
    counts are fetched in a batched videos.list call by the caller. Soft-fail:
    returns [] if the channel can't be resolved or the search is empty.
    """
    handle = _handle_from_channel_url(channel_url)
    if not handle:
        return []
    try:
        ch = await _youtube_api_get(
            "/channels", {"part": "snippet,statistics", "forHandle": handle}
        )
        items = ch.get("items", [])
        if not items:
            return []
        channel_id = items[0]["id"]
        channel_subscribers = int(
            items[0].get("statistics", {}).get("subscriberCount", 0) or 0
        )
        channel_title = items[0].get("snippet", {}).get("title", handle)

        published_after = (
            datetime.now(timezone.utc) - timedelta(days=YOUTUBE_LOOKBACK_DAYS)
        ).strftime("%Y-%m-%dT%H:%M:%SZ")
        sr = await _youtube_api_get(
            "/search",
            {
                "part": "snippet",
                "channelId": channel_id,
                "type": "video",
                "order": "date",
                "publishedAfter": published_after,
                "maxResults": str(max_results),
            },
        )
        videos = [
            i
            for i in sr.get("items", [])
            if i.get("id", {}).get("kind") == "youtube#video"
        ]
        return [
            {
                "video_id": v["id"]["videoId"],
                "title": v["snippet"]["title"],
                "publishedAt": v["snippet"]["publishedAt"],
                "channel": v["snippet"].get("channelTitle", channel_title),
                "channel_url": channel_url,
                "channel_subscribers": channel_subscribers,
            }
            for v in videos
        ]
    except Exception as e:
        if _is_auth_error(e):
            raise RuntimeError(_AUTH_ERROR_MSG) from None
        logging.warning(
            "youtube_source fetch failed for %s: %s",
            channel_url,
            _safe_error_detail(e),
        )
        return []


async def fetch_youtube_candidates(max_results: int = 8) -> list[dict]:
    """Discover recent AI-dev videos from a curated channel allowlist.

    Uses the YouTube Data API v3 (channels.list -> search.list -> videos.list)
    to scan each channel for recent uploads (last YOUTUBE_LOOKBACK_DAYS days),
    preserving channel identity, view counts, and channel subscriber count.
    """
    # Global prerequisite: the YouTube Data API v3 needs a key. A missing key
    # is a global failure (not per-channel), so raise immediately rather than
    # letting _youtube_api_get's RuntimeError get swallowed by the per-channel
    # except and mask as "no videos found".
    if not os.environ.get("YOUTUBE_API_KEY", "").strip():
        raise RuntimeError(
            "YOUTUBE_API_KEY not set — required for the YouTube Data API v3 source"
        )
    sem = asyncio.Semaphore(YOUTUBE_FETCH_CONCURRENCY)

    async def _bounded(channel_url: str) -> list[dict]:
        async with sem:
            return await _fetch_one_channel_via_api(channel_url, max_results)

    tasks = [asyncio.ensure_future(_bounded(url)) for url in CHANNELS]
    raw: list[dict] = []
    for coro in asyncio.as_completed(tasks):
        raw.extend(await coro)
    if not raw:
        return []

    # Batch videos.list for all discovered video IDs (up to 50 per call).
    # Soft-fail on non-auth errors (no stats → all candidates filter as
    # zero-view → returns []); raise on auth errors (bad key is global).
    all_video_ids = [v["video_id"] for v in raw]
    stats: dict[str, dict] = {}
    try:
        for i in range(0, len(all_video_ids), 50):
            batch = all_video_ids[i : i + 50]
            vl = await _youtube_api_get(
                "/videos", {"part": "snippet,statistics", "id": ",".join(batch)}
            )
            for v in vl.get("items", []):
                stats[v["id"]] = v
    except Exception as e:
        if _is_auth_error(e):
            raise RuntimeError(_AUTH_ERROR_MSG) from None
        logging.warning("youtube_source videos.list failed: %s", _safe_error_detail(e))

    # Assemble candidate dicts in the shape the gate + velocity layer expect.
    candidates: list[dict] = []
    for v in raw:
        stat = stats.get(v["video_id"], {})
        st = stat.get("statistics", {})
        try:
            view_count = int(st.get("viewCount", 0) or 0)
        except (TypeError, ValueError):
            view_count = 0
        if view_count == 0:
            continue
        try:
            like_count = int(st.get("likeCount", 0) or 0)
        except (TypeError, ValueError):
            like_count = 0
        # Convert ISO 8601 publishedAt -> YYYYMMDD for the existing
        # _upload_date_to_age_hours + view-velocity layer.
        published_at = stat.get("snippet", {}).get("publishedAt", v["publishedAt"])
        upload_date = str(published_at or "")[:10].replace("-", "")
        channel = v["channel"]
        candidates.append(
            {
                "url": f"https://www.youtube.com/watch?v={v['video_id']}",
                "title": v["title"][:200],
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
                "channel_url": v["channel_url"],
                "channel_subscribers": v["channel_subscribers"],
                "like_count": like_count,
            }
        )

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
    """Discover recent AI-dev videos and gate by breakout heat.

    Wraps fetch_youtube_candidates with view velocity tracking + the
    unified heat gate:
    1. Fetch raw candidates via the YouTube Data API v3 (all non-zero-view videos).
    2. Save a per-run view snapshot for each discovered video.
    3. Compute view velocity (views gained in last 7 days) from snapshots.
    4. Compute channel-relative velocity (normalized by subscriber count).
    5. Compute the inter-run delta (views gained since the last run).
    6. Attach a shared heat_score (views/hour/subscriber + baseline).
    7. Apply the publish gate (cooldown + threshold + noise floor +
       outperform) — only gated candidates are returned.
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

    candidates_returned = len(candidates)

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
    print(
        f"@youtube-trends source: candidates_returned={candidates_returned} "
        f"gate_passed={len(gated)}",
        flush=True,
    )
    return gated[:max_results]


_DATE_FORMAT = "%Y%m%d"


def _upload_date_to_age_hours(upload_date: str, now: datetime | None = None) -> float:
    """Parse a YYYYMMDD upload date to the video's age in hours.

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
