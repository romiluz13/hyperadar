"""Reddit source — uses Bright Data SERP search (bdata search) to discover trending Reddit posts.

Reddit's public JSON API is now 403-blocked and bdata scrape is blocked by
robots.txt. bdata search (SERP) is the reliable path: it gives real Reddit
thread URLs with titles and descriptions via Google SERP ranking.

Requires: bdata CLI in PATH (installed via npm, part of Bright Data).
"""

import asyncio
import logging
import shutil

# Search queries targeting AI subreddits
SEARCH_QUERIES = [
    "site:reddit.com/r/LocalLLaMA AI agent framework 2026",
    "site:reddit.com/r/MachineLearning trending AI 2026",
    "site:reddit.com/r/singularity AI breakthrough 2026",
]


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
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            stdout, _ = await proc.communicate()
            output = stdout.decode()

            for line in output.split("\n"):
                line = line.strip()
                if not line or not line[0].isdigit():
                    continue
                parts = line.split("|")
                if len(parts) < 3:
                    continue
                rank = int(parts[0].strip().split()[0])
                title = parts[1].strip()
                url = parts[2].strip()
                desc = parts[3].strip() if len(parts) > 3 else ""

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
                        "upvotes": max(100 - rank, 10),  # SERP rank as momentum proxy
                        "num_comments": 0,  # not available from SERP
                        "serp_rank": rank,
                        "stars": max(100 - rank, 10),
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
