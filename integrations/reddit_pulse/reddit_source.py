"""Reddit source — uses Bright Data SERP search (bdata search) to discover trending Reddit posts.

Reddit's public JSON API is now 403-blocked and bdata scrape is blocked by
robots.txt. bdata search (SERP) is the reliable path: it gives real Reddit
thread URLs with titles and descriptions via Google SERP ranking.

Requires: bdata CLI in PATH (installed via npm, part of Bright Data).
"""

import asyncio
import json
import logging
import os
import signal
import shutil
from urllib.parse import quote_plus

# Search queries targeting AI subreddits
SEARCH_QUERIES = [
    "site:reddit.com/r/LocalLLaMA AI agent framework 2026",
    "site:reddit.com/r/MachineLearning trending AI 2026",
    "site:reddit.com/r/singularity AI breakthrough 2026",
]
SOURCE_COMMAND_TIMEOUT_SECONDS = 120
SOURCE_COMMAND_CLEANUP_TIMEOUT_SECONDS = 5


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
    """Discover trending Reddit AI posts via bdata SERP search.

    Raises RuntimeError if bdata is not in PATH (fail fast, don't silently return []).
    """
    if not shutil.which("bdata"):
        raise RuntimeError(
            "bdata CLI not found in PATH — install with: npm install -g @brightdata/cli"
        )

    candidates = []
    for query in SEARCH_QUERIES[:2]:  # 2 queries per run
        try:
            proc = await asyncio.create_subprocess_exec(
                "bdata",
                "search",
                query,
                "--json",
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
                raise RuntimeError(
                    f"bdata search timed out after {SOURCE_COMMAND_TIMEOUT_SECONDS} seconds"
                ) from None
            except asyncio.CancelledError:
                await _stop_source_process(proc, communication)
                raise
            if proc.returncode:
                raise RuntimeError(
                    stderr.decode().strip() or "bdata search returned an error"
                )
            payload = json.loads(stdout)

            for fallback_rank, result in enumerate(payload.get("organic", []), start=1):
                rank = int(result.get("rank") or fallback_rank)
                title = str(result.get("title") or "").strip()
                url = str(result.get("link") or "").strip()
                desc = str(result.get("description") or "").strip()

                if "reddit.com/r/" not in url:
                    continue

                # Extract subreddit from URL
                subreddit = ""
                if "/r/" in url:
                    subreddit = url.split("/r/")[1].split("/")[0]

                candidates.append(
                    {
                        "url": url,
                        "title": title[:200],
                        "kind": "thread",
                        "description": desc[:500],
                        "topics": ["reddit", "ai", subreddit],
                        "subreddit": subreddit,
                        "serp_rank": rank,
                        "visibility_score": max(100 - rank * 10, 20),
                        "search_query": query,
                        "evidence_url": (
                            f"https://www.google.com/search?q={quote_plus(query)}"
                        ),
                    }
                )
        except Exception as e:
            logging.warning("reddit_source fetch failed for query '%s': %s", query, e)
            continue

    # Deduplicate by URL
    seen = set()
    unique = []
    for c in candidates:
        if c["url"] not in seen:
            seen.add(c["url"])
            unique.append(c)
    return unique[:max_results]
