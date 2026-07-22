"""Reddit source — Bright Data structured Reddit Scraper API via bdata pipelines.

Replaces SERP guessing (bdata search) with bdata pipelines reddit_posts, which
returns structured JSON: num_upvotes, num_comments, title, url, community_name.
This gives real engagement metrics, not a Google visibility proxy.

Requires: bdata CLI in PATH (installed via npm, part of Bright Data).
"""

import asyncio
import json
import logging
import os
import signal
import shutil
from datetime import datetime, timezone

from _shared.mongo import _get_db

# Target subreddits for AI developer discourse.
SUBREDDITS = [
    "https://www.reddit.com/r/LocalLLaMA/rising/",
    "https://www.reddit.com/r/MachineLearning/rising/",
    "https://www.reddit.com/r/singularity/rising/",
    "https://www.reddit.com/r/artificial/rising/",
    "https://www.reddit.com/r/OpenAI/rising/",
]
SOURCE_COMMAND_TIMEOUT_SECONDS = 180
SOURCE_COMMAND_CLEANUP_TIMEOUT_SECONDS = 5
COOLDOWN_DAYS = 7


def _visibility_from_upvotes(upvotes: int, comments: int) -> float:
    """Map upvotes + comments to a 0-100 visibility score.

    Upvotes dominate; comments add discourse signal. Capped at 100.
    """
    score = min(upvotes / 10, 80) + min(comments / 5, 20)
    return round(max(score, 20), 1)


def _post_age_hours(post: dict) -> float:
    """Hours since the Reddit post was created. Falls back to 1 if unknown."""
    for key in ("created_utc", "created_at"):
        raw = post.get(key)
        if raw is None:
            continue
        try:
            if isinstance(raw, (int, float)):
                created = datetime.fromtimestamp(raw, tz=timezone.utc)
            else:
                created = datetime.fromisoformat(str(raw))
                if created.tzinfo is None:
                    created = created.replace(tzinfo=timezone.utc)
        except (ValueError, OSError, OverflowError):
            continue
        return max(1.0, (datetime.now(timezone.utc) - created).total_seconds() / 3600)
    return 1.0


async def _last_posted_days(db, project_url: str) -> int:
    """Days since the most recent post for this project URL. 999 if never posted."""
    post = await db.posts.find_one(
        {"project.url": project_url},
        {"postedAt": 1},
    )
    if not post or not post.get("postedAt"):
        return 999
    posted = post["postedAt"]
    if posted.tzinfo is None:
        posted = posted.replace(tzinfo=timezone.utc)
    delta = datetime.now(timezone.utc) - posted
    return max(0, delta.days)


async def _stop_source_process(proc, communication) -> None:
    if proc.returncode is None:
        pid = getattr(proc, "pid", None)
        if isinstance(pid, int):
            try:
                os.killpg(pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
            except PermissionError:
                try:
                    proc.kill()
                except ProcessLookupError:
                    pass
        else:
            try:
                proc.kill()
            except ProcessLookupError:
                pass
    try:
        await asyncio.wait_for(
            asyncio.shield(communication),
            timeout=SOURCE_COMMAND_CLEANUP_TIMEOUT_SECONDS,
        )
    except TimeoutError:
        logging.error("bdata process cleanup exceeded its deadline")
    except Exception as error:
        logging.error("bdata process cleanup failed: %s", error)


async def fetch_reddit_candidates(max_results: int = 10, db=None) -> list[dict]:
    """Discover trending Reddit AI posts via bdata pipelines reddit_posts.

    Pulls rising posts from curated AI subreddits with structured upvote/comment
    data. Raises RuntimeError if bdata is not in PATH.

    Skips Reddit threads that were posted (published as HypeRadar posts) in the
    last ``COOLDOWN_DAYS`` days. When *db* is not provided, connects via
    ``_get_db()``.
    """
    if not shutil.which("bdata"):
        raise RuntimeError(
            "bdata CLI not found in PATH — install with: npm install -g @brightdata/cli"
        )

    if db is None:
        db = _get_db()

    candidates = []
    for subreddit_url in SUBREDDITS[:3]:  # 3 subreddits per run
        if len(candidates) >= max_results:
            break
        try:
            proc = await asyncio.create_subprocess_exec(
                "bdata",
                "pipelines",
                "reddit_posts",
                subreddit_url,
                "--format",
                "json",
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                start_new_session=True,
            )
            communication = asyncio.create_task(proc.communicate())
            try:
                stdout, stderr = await asyncio.wait_for(
                    asyncio.shield(communication),
                    timeout=SOURCE_COMMAND_TIMEOUT_SECONDS,
                )
            except TimeoutError:
                await _stop_source_process(proc, communication)
                logging.warning("bdata pipelines timed out for %s", subreddit_url)
                continue
            except asyncio.CancelledError:
                await _stop_source_process(proc, communication)
                raise
            if proc.returncode:
                logging.warning(
                    "bdata pipelines error for %s: %s",
                    subreddit_url,
                    stderr.decode().strip()[:200],
                )
                continue
            payload = json.loads(stdout)
            # bdata pipelines may return a list or {"results": [...]}
            posts = payload if isinstance(payload, list) else payload.get("results", [])

            for post in posts:
                if len(candidates) >= max_results:
                    break
                url = str(post.get("url") or "").strip()
                title = str(post.get("title") or "").strip()
                if not url or not title:
                    continue
                subreddit = str(post.get("community_name") or "").strip()
                if not subreddit and "/r/" in url:
                    subreddit = url.split("/r/")[1].split("/")[0]
                upvotes = int(post.get("num_upvotes") or 0)
                comments = int(post.get("num_comments") or 0)
                if upvotes < 10:  # filter noise
                    continue
                age_hours = _post_age_hours(post)
                candidates.append(
                    {
                        "url": url,
                        "title": title[:200],
                        "kind": "thread",
                        "description": str(post.get("description") or title)[:500],
                        "topics": ["reddit", "ai", subreddit],
                        "subreddit": subreddit,
                        "num_upvotes": upvotes,
                        "num_comments": comments,
                        "serp_rank": 1,  # back-compat: no longer SERP, keep field
                        "visibility_score": _visibility_from_upvotes(upvotes, comments),
                        "engagement_velocity": round(upvotes / max(age_hours, 1), 2),
                        "evidence_url": url,
                    }
                )
        except Exception as e:
            logging.warning("reddit_source fetch failed for %s: %s", subreddit_url, e)
            continue

    # Deduplicate by URL
    seen = set()
    unique = []
    for c in candidates:
        if c["url"] not in seen:
            seen.add(c["url"])
            unique.append(c)

    # Cooldown: skip Reddit threads posted in the last COOLDOWN_DAYS days
    cooled: list[dict] = []
    for c in unique:
        if await _last_posted_days(db, c["url"]) >= COOLDOWN_DAYS:
            cooled.append(c)

    # Sort by engagement_velocity (highest first)
    cooled.sort(key=lambda c: c["engagement_velocity"], reverse=True)
    return cooled[:max_results]
