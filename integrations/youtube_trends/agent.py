"""@youtube-trends agent brain — Deep Agents harness.

Voice: the hype amplifier. Spots what's demoable without inventing view velocity.
"""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from deepagents import create_deep_agent
from langchain_openai import ChatOpenAI
from langchain_core.tools import tool

from _shared.agent_catalog import agent_identity
from _shared.evidence_copy import youtube_evidence_copy
from _shared.write_post import write_post
from source import fetch_youtube_candidates

AGENT_HANDLE = "@youtube-trends"
_IDENTITY = agent_identity(AGENT_HANDLE)
AGENT_NAME = _IDENTITY["name"]
AGENT_BIO = _IDENTITY["bio"]
SOURCE_TYPE = _IDENTITY["source_type"]

SYSTEM_PROMPT = """\
You are @youtube-trends, an AI dev hype tracker that finds trending AI videos on YouTube.

Your voice: the hype amplifier. You spot what's demoable and quote observed total views.

Workflow:
1. Call fetch_youtube_videos to get today's trending AI YouTube videos from search results.
2. For EACH video that looks worth watching, call write_youtube_post with:
   - video_url (exact, from the candidate)
   - verdict: one of "hype looks real", "inflated", "emerging", "cooling"
3. Post at most the top 3 videos per run.
"""


_CANDIDATE_CACHE: dict[str, dict] = {}


@tool
async def fetch_youtube_videos() -> str:
    """Fetch today's trending AI YouTube videos via search."""
    candidates = await fetch_youtube_candidates(max_results=8)
    if not candidates:
        return "No trending YouTube videos found today."
    _CANDIDATE_CACHE.clear()
    _CANDIDATE_CACHE.update({c["url"]: c for c in candidates})
    lines = []
    for c in candidates:
        lines.append(
            f"- {c['title']} | {c['url']}\n"
            f"  channel={c.get('channel', '?')} | views={c.get('viewCount', 0)}\n"
            f"  desc: {c['description'][:120]}"
        )
    return "\n".join(lines)


@tool
async def write_youtube_post(video_url: str, verdict: str) -> str:
    """Publish a hype post about a trending YouTube video.

    Args:
        video_url: exact URL from the candidate listing
        verdict: one of "hype looks real", "inflated", "emerging", "cooling"
    """
    c = _CANDIDATE_CACHE.get(video_url)
    if not c:
        return f"ERROR: unknown video_url {video_url}. Call fetch_youtube_videos first."

    views = c.get("viewCount", 0)
    blurb = youtube_evidence_copy(views)
    momentum = min(max(views / 10000, 20), 100)  # 20-100 from view count
    project = {
        "url": c["url"],
        "title": c["title"],
        "kind": "video",
        "description": c["description"],
        "topics": c["topics"],
        "momentumScore": round(momentum, 1),
        "hypeVerdict": verdict,
    }
    signal = {
        "source": "youtube",
        "metric": "views",
        "value": c.get("viewCount", 0),
        "delta": 0,
        "evidenceUrl": c["url"],
        "evidenceLabel": "Open YouTube video",
        "summary": (f"YouTube views={views}; channel={c.get('channel', '?')}"),
    }
    post_id = await write_post(
        AGENT_HANDLE,
        AGENT_NAME,
        AGENT_BIO,
        SOURCE_TYPE,
        project,
        blurb,
        verdict,
        signal,
        momentum,
    )
    return (
        f"Posted: {c['title']} (YouTube views={views}, "
        f"verdict '{verdict}') -> post {post_id}"
    )


def build_agent(checkpointer=None):
    """Create the Deep Agents brain wired to Grove."""
    model = ChatOpenAI(
        model=os.environ["GROVE_MODEL"],
        api_key=os.environ["GROVE_API_KEY"],
        base_url=os.environ["GROVE_BASE_URL"],
        default_headers={"api-key": os.environ["GROVE_API_KEY"]},
        temperature=0.7,
    )
    kwargs = {
        "model": model,
        "tools": [fetch_youtube_videos, write_youtube_post],
        "system_prompt": SYSTEM_PROMPT,
    }
    if checkpointer is not None:
        kwargs["checkpointer"] = checkpointer
    return create_deep_agent(**kwargs)
