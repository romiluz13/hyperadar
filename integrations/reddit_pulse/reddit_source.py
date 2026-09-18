"""Reddit source — Bright Data structured Reddit Scraper API via bdata pipelines.

Replaces SERP guessing (bdata search) with bdata pipelines reddit_posts, which
returns structured JSON: num_upvotes, num_comments, title, url, community_name.
This gives real engagement metrics, not a Google visibility proxy.

Requires: bdata CLI in PATH (installed via npm, part of Bright Data).
"""

import asyncio
import contextlib
import json
import logging
import os
import shutil
import signal
from datetime import datetime, timezone

from _shared.heat import compute_heat_score, should_publish_heat
from _shared.mongo import _get_db

# Target subreddits for AI developer discourse.
SUBREDDITS = [
    "https://www.reddit.com/r/LocalLLaMA/rising/",
    "https://www.reddit.com/r/LLMDevs/rising/",
    "https://www.reddit.com/r/AI_Agents/rising/",
    "https://www.reddit.com/r/ClaudeAI/rising/",
    "https://www.reddit.com/r/ChatGPTCoding/rising/",
    "https://www.reddit.com/r/artificial/rising/",
    "https://www.reddit.com/r/ExperiencedDevs/rising/",
    "https://www.reddit.com/r/softwarearchitecture/rising/",
    "https://www.reddit.com/r/devops/rising/",
    "https://www.reddit.com/r/mongodb/rising/",
    "https://www.reddit.com/r/LangChain/rising/",
    "https://www.reddit.com/r/Rag/rising/",
    "https://www.reddit.com/r/SaaS/rising/",
    "https://www.reddit.com/r/startups/rising/",
    "https://www.reddit.com/r/singularity/rising/",
    "https://www.reddit.com/r/MachineLearning/rising/",
]
SOURCE_COMMAND_TIMEOUT_SECONDS = 180
SOURCE_COMMAND_CLEANUP_TIMEOUT_SECONDS = 5
COOLDOWN_DAYS = 7
REDDIT_FETCH_CONCURRENCY = 5


def _visibility_from_upvotes(upvotes: int, comments: int) -> float:
    """Map upvotes + comments to a 0-100 visibility score.

    Upvotes dominate; comments add discourse signal. Capped at 100.
    """
    score = min(upvotes / 10, 80) + min(comments / 5, 20)
    return round(max(score, 20), 1)


def _normalize_reddit_url(url: str) -> str:
    """Normalize a Reddit URL for dedup/cooldown matching.

    Strips query params and trailing slashes so that:
    - https://www.reddit.com/r/LocalLLaMA/comments/abc/
    - https://www.reddit.com/r/LocalLLaMA/comments/abc/?utm_source=share
    - https://www.reddit.com/r/LocalLLaMA/comments/abc
    all match the same post.
    """
    # Strip query params
    if "?" in url:
        url = url.split("?")[0]
    # Strip trailing slash
    url = url.rstrip("/")
    return url


def _reddit_permalink(post: dict) -> str:
    """Build the canonical thread URL from Bright Data's post identity fields."""
    post_id = str(post.get("post_id") or "").strip()
    subreddit = str(post.get("community_name") or "").strip().strip("/")
    if post_id.startswith("t3_"):
        post_id = post_id[3:]
    if not post_id or not subreddit:
        return ""
    return _normalize_reddit_url(
        f"https://www.reddit.com/r/{subreddit}/comments/{post_id}/"
    )


def _post_age_hours(post: dict) -> float:
    """Hours since the Reddit post was created. Falls back to 1 if unknown.

    If Bright Data doesn't return created_utc/created_at, engagement_velocity
    defaults to upvotes/1 = upvotes. This is a known limitation — the ranking
    becomes sort-by-upvotes, which is still better than no sorting.
    """
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
    normalized = _normalize_reddit_url(project_url)
    post = await db.posts.find_one(
        {"project.url": normalized},
        {"postedAt": 1},
        sort=[("postedAt", -1)],
    )
    if not post or not post.get("postedAt"):
        return 999
    posted = post["postedAt"]
    if posted.tzinfo is None:
        posted = posted.replace(tzinfo=timezone.utc)
    delta = datetime.now(timezone.utc) - posted
    return max(0, delta.days)


async def _save_reddit_snapshot(
    db,
    url: str,
    upvotes: int,
    comments: int,
    title: str = "",
    description: str = "",
    subreddit: str = "",
) -> None:
    """Save a per-run engagement snapshot for delta-based discovery.

    The snapshot doubles as the cross-source corroboration corpus: title +
    description let the @github-radar gate match repos mentioned in threads
    (including threads that never became posts), so the text fields are part
    of the contract, not decoration.
    """
    await db.reddit_post_snapshots.insert_one(
        {
            "url": _normalize_reddit_url(url),
            "upvotes": upvotes,
            "comments": comments,
            "title": title[:200],
            "description": description[:500],
            "subreddit": subreddit,
            "capturedAt": datetime.now(timezone.utc),
        }
    )


async def _last_reddit_snapshot(db, url: str) -> dict | None:
    """Get the most recent per-run snapshot for a URL."""
    return await db.reddit_post_snapshots.find_one(
        {"url": _normalize_reddit_url(url)},
        sort=[("capturedAt", -1)],
    )


async def _stop_source_process(proc, communication) -> None:
    if proc.returncode is None:
        pid = getattr(proc, "pid", None)
        if isinstance(pid, int):
            try:
                with contextlib.suppress(ProcessLookupError):
                    os.killpg(pid, signal.SIGKILL)
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
        logging.error("bdata process cleanup exceeded its deadline")
    except Exception as error:
        logging.error("bdata process cleanup failed: %s", error)


async def _fetch_one_subreddit(subreddit_url: str) -> list[dict]:
    """Fetch posts from a single subreddit. Soft-fail: returns [] on error."""
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
            return []
        except asyncio.CancelledError:
            await _stop_source_process(proc, communication)
            raise
        if proc.returncode:
            logging.warning(
                "bdata pipelines error for %s: %s",
                subreddit_url,
                stderr.decode().strip()[:200],
            )
            return []
        payload = json.loads(stdout)
        # bdata pipelines may return a list or {"results": [...]}
        posts = payload if isinstance(payload, list) else payload.get("results", [])

        results = []
        for post in posts:
            url = _reddit_permalink(post)
            title = str(post.get("title") or "").strip()
            if not url:
                logging.warning(
                    "reddit_source skipped post with missing canonical identity: post_id=%r community_name=%r",
                    post.get("post_id"),
                    post.get("community_name"),
                )
                continue
            if not title:
                continue
            subreddit = str(post.get("community_name") or "").strip()
            upvotes = int(post.get("num_upvotes") or 0)
            comments = int(post.get("num_comments") or 0)
            if upvotes < 10:  # filter noise
                continue
            age_hours = _post_age_hours(post)
            results.append(
                {
                    "url": url,
                    "title": title[:200],
                    "kind": "thread",
                    "description": str(post.get("description") or title)[:500],
                    "topics": ["reddit", "ai", subreddit],
                    "subreddit": subreddit,
                    "num_upvotes": upvotes,
                    "num_comments": comments,
                    "age_hours": age_hours,
                    "serp_rank": 1,  # back-compat: no longer SERP, keep field
                    "visibility_score": _visibility_from_upvotes(upvotes, comments),
                    "engagement_velocity": round(upvotes / max(age_hours, 1), 2),
                    "evidence_url": url,
                }
            )
        return results
    except Exception as e:
        logging.warning("reddit_source fetch failed for %s: %s", subreddit_url, e)
        return []


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

    # Bounded-async fetch: all subreddits concurrently (up to the concurrency
    # limit), soft-fail per subreddit (one timeout does not blank the run).
    tasks = [asyncio.ensure_future(_fetch_one_subreddit(s)) for s in SUBREDDITS]
    all_posts: list[dict] = []
    for coro in asyncio.as_completed(tasks):
        posts = await coro
        all_posts.extend(posts)

    # Deduplicate by URL
    seen = set()
    unique = []
    for c in all_posts:
        if c["url"] not in seen:
            seen.add(c["url"])
            unique.append(c)

    # Delta-based discovery: compute the upvote delta since the last run's
    # snapshot. Items that gained the most since last run surface higher — this
    # is what makes day-to-day results actually change. Save the current
    # snapshot for the next run's delta.
    for c in unique:
        prior = await _last_reddit_snapshot(db, c["url"])
        prior_upvotes = prior.get("upvotes", 0) if prior else 0
        c["upvotes_delta"] = max(0, c["num_upvotes"] - prior_upvotes)
        await _save_reddit_snapshot(
            db,
            c["url"],
            c["num_upvotes"],
            c["num_comments"],
            title=c["title"],
            description=c["description"],
            subreddit=c.get("subreddit", ""),
        )

    # Attach heat scores to all unique candidates (before the gate).
    attach_reddit_heat(unique)

    # Publish gate: cooldown + threshold + noise floor + outperform.
    # Replaces the old simple cooldown check with the full gate.
    gated: list[dict] = []
    for c in unique:
        heat_result = {
            "heat_score": c["heat_score"],
            "outperform_ratio": c["outperform_ratio"],
            "baseline_confidence": c["baseline_confidence"],
        }
        item = {
            "upvotes": c["num_upvotes"],
            "comments": c["num_comments"],
            "age_hours": c["age_hours"],
        }
        last_days = await _last_posted_days(db, c["url"])
        if should_publish_heat("reddit", heat_result, item, last_days):
            gated.append(c)

    # Sort by heat_score (highest first), with upvotes_delta as a tiebreaker.
    gated.sort(key=lambda c: (c["heat_score"], c.get("upvotes_delta", 0)), reverse=True)
    print(
        f"@reddit-pulse source: candidates_returned={len(unique)} "
        f"gate_passed={len(gated)}",
        flush=True,
    )
    return gated[:max_results]


def attach_reddit_heat(candidates: list[dict]) -> list[dict]:
    """Attach a shared heat_score to each candidate (beside existing fields).

    Expand phase: adds ``heat_score``, ``outperform_ratio``, and
    ``baseline_confidence`` without removing the existing visibility_score /
    engagement_velocity fields. The baseline for each post is the other recent
    posts from the same subreddit (their ``num_upvotes`` + age), passed to the
    shared scorer. The scored post is excluded from its own baseline.
    """
    if not candidates:
        return candidates
    by_subreddit: dict[str, list[dict]] = {}
    for c in candidates:
        by_subreddit.setdefault(c.get("subreddit", ""), []).append(c)
    for c in candidates:
        subreddit = c.get("subreddit", "")
        baseline = [
            {
                "upvotes": other.get("num_upvotes", 0),
                "age_hours": other.get("age_hours", 0.0),
            }
            for other in by_subreddit.get(subreddit, [])
            if other is not c
        ]
        item = {
            "upvotes": c.get("num_upvotes", 0),
            "comments": c.get("num_comments", 0),
            "age_hours": c.get("age_hours", 0.0),
        }
        result = compute_heat_score("reddit", item, baseline, prior_posts=0)
        c["heat_score"] = result["heat_score"]
        c["outperform_ratio"] = result["outperform_ratio"]
        c["baseline_confidence"] = result["baseline_confidence"]
    return candidates
