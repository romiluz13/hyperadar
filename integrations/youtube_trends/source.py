"""YouTube source — uses yt-dlp to discover trending AI videos.

yt-dlp searches YouTube directly and returns real metadata (view counts,
channel, duration) without any API key or bdata dependency.
"""

import asyncio
import logging
import shutil

SEARCH_QUERIES = [
    "AI agent framework demo 2026",
    "LLM tutorial trending 2026",
    "AI dev tools showcase 2026",
]


async def fetch_youtube_candidates(max_results: int = 8) -> list[dict]:
    """Discover trending AI YouTube videos via yt-dlp search.

    Raises RuntimeError if yt-dlp is not in PATH (fail fast, don't silently return []).
    """
    if not shutil.which("yt-dlp"):
        raise RuntimeError(
            "yt-dlp not found in PATH — install with: brew install yt-dlp"
        )
    candidates = []
    # Use 2 queries per run, get 5 results each
    for query in SEARCH_QUERIES[:2]:
        try:
            proc = await asyncio.create_subprocess_exec(
                "yt-dlp",
                "--flat-playlist",
                "--print",
                "%(id)s|%(title)s|%(channel)s|%(view_count)s|%(duration)s",
                f"ytsearch5:{query}",
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            stdout, _ = await proc.communicate()
            output = stdout.decode()

            for line in output.strip().split("\n"):
                line = line.strip()
                if not line:
                    continue
                parts = line.split("|")
                if len(parts) < 5:
                    continue
                vid_id, title, channel, views, duration = parts[:5]
                try:
                    view_count = int(views) if views != "NA" else 0
                except ValueError:
                    view_count = 0

                candidates.append(
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
                        "serp_rank": len(candidates) + 1,
                        "stars": min(view_count // 1000, 100),  # rough momentum proxy
                    }
                )
        except Exception as e:
            logging.warning("youtube_source fetch failed for query '%s': %s", query, e)
            continue

    # Deduplicate by URL, sort by views (most viewed = most hyped)
    seen = set()
    unique = []
    for c in candidates:
        if c["url"] not in seen:
            seen.add(c["url"])
            unique.append(c)
    unique.sort(key=lambda x: x.get("viewCount", 0), reverse=True)
    return unique[:max_results]
