"""YouTube source — uses yt-dlp search to discover high-view AI videos.

yt-dlp searches YouTube directly and returns real metadata (view counts,
channel, duration) without any API key or bdata dependency.
"""

import asyncio
import json
import logging
import os
import signal
import shutil

SEARCH_QUERIES = [
    "AI agent framework demo 2026",
    "LLM tutorial trending 2026",
    "AI dev tools showcase 2026",
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
        logging.error("yt-dlp process cleanup exceeded its deadline")
    except Exception as error:
        logging.error("yt-dlp process cleanup failed: %s", error)


async def fetch_youtube_candidates(max_results: int = 8) -> list[dict]:
    """Discover AI YouTube videos and preserve search-order evidence.

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
                "--dump-json",
                f"ytsearch5:{query}",
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
                raise RuntimeError(
                    f"yt-dlp search timed out after {SOURCE_COMMAND_TIMEOUT_SECONDS} seconds"
                ) from None
            except asyncio.CancelledError:
                await _stop_source_process(proc, communication)
                raise
            output = stdout.decode()

            for search_position, line in enumerate(output.strip().split("\n"), start=1):
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
                        "youtube_search_position": search_position,
                        "search_query": query,
                    }
                )
        except Exception as e:
            logging.warning("youtube_source fetch failed for query '%s': %s", query, e)
            continue

    # Deduplicate by URL, then prioritize observed total views for review.
    seen = set()
    unique = []
    for c in candidates:
        if c["url"] not in seen:
            seen.add(c["url"])
            unique.append(c)
    unique.sort(key=lambda x: x.get("viewCount", 0), reverse=True)
    return unique[:max_results]
