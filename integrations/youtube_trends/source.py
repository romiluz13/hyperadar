"""YouTube source — uses Bright Data CLI (bdata search) to discover trending AI videos.

bdata search gives SERP results (title + URL). We filter for YouTube URLs and
use SERP ranking as a trending proxy. For view counts, the YouTube Data API
videos.list (1 unit) could be added later with an API key.
"""
import asyncio
import logging
import re

SEARCH_QUERIES = [
    "AI agent framework demo 2026 youtube",
    "LLM tutorial trending 2026 youtube",
    "AI dev tools showcase 2026 youtube",
]


async def fetch_youtube_candidates(max_results: int = 8) -> list[dict]:
    """Discover trending AI YouTube videos via bdata SERP search."""
    candidates = []
    for query in SEARCH_QUERIES[:2]:  # 2 queries per run
        try:
            proc = await asyncio.create_subprocess_exec(
                "bdata", "search", query,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            stdout, _ = await proc.communicate()
            output = stdout.decode()

            # Parse bdata table output: rank | title | url | description
            for line in output.split("\n"):
                line = line.strip()
                if not line or not line[0].isdigit():
                    continue
                parts = line.split("|")
                if len(parts) < 3:
                    continue
                url = parts[2].strip()
                title = parts[1].strip()
                if "youtube.com/watch" in url or "youtu.be" in url:
                    # Extract video ID for a cleaner URL
                    vid_match = re.search(r"(?:v=|youtu\.be/)([\w-]{11})", url)
                    clean_url = f"https://www.youtube.com/watch?v={vid_match.group(1)}" if vid_match else url
                    candidates.append({
                        "url": clean_url,
                        "title": title[:200],
                        "kind": "video",
                        "description": parts[3].strip()[:300] if len(parts) > 3 else title,
                        "topics": ["youtube", "ai", "video"],
                        "serp_rank": int(parts[0].strip().split()[0]) if parts[0].strip() else 99,
                        "stars": max(100 - int(parts[0].strip().split()[0] or 99), 10),
                    })
        except Exception as e:
            logging.warning("youtube_source fetch failed for query '%s': %s", query, e)
            continue

    # Deduplicate by URL
    seen = set()
    unique = []
    for c in candidates:
        if c["url"] not in seen:
            seen.add(c["url"])
            unique.append(c)
    return unique[:max_results]
