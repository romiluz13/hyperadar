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

# Target subreddits for AI developer discourse.
SUBREDDITS = [
    "https://www.reddit.com/r/LocalLLaMA/hot/",
    "https://www.reddit.com/r/MachineLearning/hot/",
    "https://www.reddit.com/r/singularity/hot/",
    "https://www.reddit.com/r/artificial/hot/",
    "https://www.reddit.com/r/OpenAI/hot/",
]
SOURCE_COMMAND_TIMEOUT_SECONDS = 180
SOURCE_COMMAND_CLEANUP_TIMEOUT_SECONDS = 5


def _visibility_from_upvotes(upvotes: int, comments: int) -> float:
    """Map upvotes + comments to a 0-100 visibility score.

    Upvotes dominate; comments add discourse signal. Capped at 100.
    """
    score = min(upvotes / 10, 80) + min(comments / 5, 20)
    return round(max(score, 20), 1)


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


async def fetch_reddit_candidates(max_results: int = 10) -> list[dict]:
    """Discover trending Reddit AI posts via bdata pipelines reddit_posts.

        Pulls hot posts from curated AI subreddits with structured upvote/comment
    data. Raises RuntimeError if bdata is not in PATH.
    """
    if not shutil.which("bdata"):
        raise RuntimeError(
            "bdata CLI not found in PATH — install with: npm install -g @brightdata/cli"
        )

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
    return unique[:max_results]
