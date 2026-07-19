"""YouTube source — channel-scoped yt-dlp discovery for AI-dev channels.

Instead of generic `ytsearch` (which surfaces old popular videos), this scans
a curated allowlist of AI-dev YouTube channels for recent uploads, preserving
channel identity and view counts. View velocity is approximated by
views/days-since-upload when no snapshot history exists.
"""

import asyncio
import json
import logging
import os
import signal
import shutil
from datetime import datetime, timedelta, timezone

# Curated AI-dev channel allowlist (verified handles from research).
CHANNELS = [
    "https://www.youtube.com/@AndrejKarpathy/videos",
    "https://www.youtube.com/@YannicKilcher/videos",
    "https://www.youtube.com/@AIJasonZ/videos",
    "https://www.youtube.com/@matthew_berman/videos",
    "https://www.youtube.com/@Fireship/videos",
    "https://www.youtube.com/@samwitteveen/videos",
    "https://www.youtube.com/@ColeMedin/videos",
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
    """Discover recent AI-dev videos from a curated channel allowlist.

        Scans each channel's /videos page for recent uploads via yt-dlp, preserving
    channel identity and real view counts. Raises RuntimeError if yt-dlp is not
    in PATH.
    """
    if not shutil.which("yt-dlp"):
        raise RuntimeError(
            "yt-dlp not found in PATH — install with: brew install yt-dlp"
        )
    candidates = []
    cutoff = (datetime.now(timezone.utc) - timedelta(days=14)).strftime("%Y%m%d")
    for channel_url in CHANNELS:
        if len(candidates) >= max_results:
            break
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
                continue
            except asyncio.CancelledError:
                await _stop_source_process(proc, communication)
                raise
            output = stdout.decode()

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
                    metadata.get("channel")
                    or metadata.get("uploader")
                    or "Unknown channel"
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
                        "channel_url": channel_url,
                    }
                )
        except Exception as e:
            logging.warning("youtube_source fetch failed for %s: %s", channel_url, e)
            continue

    # Deduplicate by URL, prioritize by view count.
    seen = set()
    unique = []
    for c in candidates:
        if c["url"] not in seen:
            seen.add(c["url"])
            unique.append(c)
    unique.sort(key=lambda x: x.get("viewCount", 0), reverse=True)
    return unique[:max_results]
