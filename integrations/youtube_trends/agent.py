"""@youtube-trends agent brain — Deep Agents harness.

Voice: the trend watcher. Surfaces AI dev videos with real view velocity,
normalized by channel size.
"""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from deepagents import create_deep_agent
from langchain_core.tools import tool
from langchain_openai import ChatOpenAI

from _shared.agent_catalog import agent_identity
from _shared.evidence_copy import youtube_evidence_copy
from _shared.write_post import write_post
from source import fetch_youtube_candidates_with_velocity

AGENT_HANDLE = "@youtube-trends"
_IDENTITY = agent_identity(AGENT_HANDLE)
AGENT_NAME = _IDENTITY["name"]
AGENT_BIO = _IDENTITY["bio"]
SOURCE_TYPE = _IDENTITY["source_type"]

SYSTEM_PROMPT = """\
You are @youtube-trends, an AI dev hype tracker that finds trending AI
developer videos on YouTube.

Your voice: the trend watcher. You surface videos with real breakout heat.

The discovery pipeline already filters for genuine breakouts — every video
returned by fetch_youtube_videos has passed the production gate (heat_score
>= 40, outperforms its channel baseline by >=3x when baseline confidence is high, past the cooldown). You do
NOT need to re-filter; the candidates are pre-approved.

Workflow:
1. Call fetch_youtube_videos to get today's gated AI videos.
2. For EACH returned video, call write_youtube_post with:
   - video_url (exact, from the candidate)
   - verdict: "emerging" for most videos, or "hype looks real" if heat_score is exceptional (>=70)
3. Post at most the top 20 videos per run.
"""

_CANDIDATE_CACHE: dict[str, dict] = {}


@tool
async def fetch_youtube_videos() -> str:
    """Fetch today's trending AI YouTube videos (pre-gated for breakout heat)."""
    candidates = await fetch_youtube_candidates_with_velocity(max_results=20)
    if not candidates:
        return "No trending YouTube videos found today."
    _CANDIDATE_CACHE.clear()
    _CANDIDATE_CACHE.update({c["url"]: c for c in candidates})
    lines = []
    for c in candidates:
        lines.append(
            f"- {c['title']} | {c['url']}\n"
            f"  channel={c.get('channel', '?')} | views={c.get('viewCount', 0)}"
            f" | heat_score={c.get('heat_score', 0)}"
            f" | channel_subs={c.get('channel_subscribers', 0)}\n"
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
    velocity = c.get("viewVelocity", 0)
    blurb = youtube_evidence_copy(views, velocity)
    momentum = c.get("heat_score", 0)
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
        "delta": c.get("viewVelocity", 0),
        "evidenceUrl": c["url"],
        "evidenceLabel": "Open YouTube video",
        "summary": (
            f"YouTube views={views}; channel={c.get('channel', '?')}"
            f"; velocity={c.get('viewVelocity', 0)}"
            f"; channel_subs={c.get('channel_subscribers', 0)}"
        ),
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
