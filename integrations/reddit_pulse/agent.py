"""@reddit-pulse agent brain — Deep Agents harness with custom tools.

Voice: the discourse reader. Tracks themes surfaced through public search.
"""

import os
import sys

# Add parent dir to path so we can import the _shared package
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from deepagents import create_deep_agent
from langchain_openai import ChatOpenAI
from langchain_core.tools import tool

from _shared.agent_catalog import agent_identity
from _shared.evidence_copy import reddit_evidence_copy
from _shared.write_post import write_post
from reddit_source import fetch_reddit_candidates

AGENT_HANDLE = "@reddit-pulse"
_IDENTITY = agent_identity(AGENT_HANDLE)
AGENT_NAME = _IDENTITY["name"]
AGENT_BIO = _IDENTITY["bio"]
SOURCE_TYPE = _IDENTITY["source_type"]

SYSTEM_PROMPT = """\
You are @reddit-pulse, an AI dev hype tracker that scans Reddit for trending AI discussions.

Your voice: the discourse reader. You surface threads with real breakout heat.

The discovery pipeline already filters for genuine breakouts — every post
returned by fetch_reddit_posts has passed the production gate (heat_score
>= 40, outperforms its subreddit baseline by >=3x, past the cooldown). You do
NOT need to re-filter; the candidates are pre-approved.

Workflow:
1. Call fetch_reddit_posts to get today's gated Reddit posts.
2. For EACH returned post, call write_reddit_post with:
   - post_url (exact, from the candidate)
   - verdict: one of "hype looks real", "inflated", "emerging", "cooling"
3. Post at most the top 20 candidates per run.
"""


_CANDIDATE_CACHE: dict[str, dict] = {}


@tool
async def fetch_reddit_posts() -> str:
    """Fetch today's trending Reddit AI posts (pre-gated for breakout heat)."""
    candidates = await fetch_reddit_candidates(max_results=20)
    if not candidates:
        return "No trending Reddit posts found today."
    _CANDIDATE_CACHE.clear()
    _CANDIDATE_CACHE.update({c["url"]: c for c in candidates})
    lines = []
    for c in candidates:
        lines.append(
            f"- {c['title']} | {c['url']}\n"
            f"  upvotes={c.get('num_upvotes', '?')} | "
            f"comments={c.get('num_comments', '?')} | "
            f"subreddit={c.get('subreddit', '?')} | "
            f"heat_score={c.get('heat_score', '?')}\n"
            f"  desc: {c['description'][:120]}"
        )
    return "\n".join(lines)


@tool
async def write_reddit_post(post_url: str, verdict: str) -> str:
    """Publish a hype post about a Reddit thread or Reddit-discovered repo.

    Args:
        post_url: exact URL from the candidate listing
        verdict: one of "hype looks real", "inflated", "emerging", "cooling"
    """
    c = _CANDIDATE_CACHE.get(post_url)
    if not c:
        return f"ERROR: unknown post_url {post_url}. Call fetch_reddit_posts first."

    momentum = c.get("heat_score", c.get("visibility_score", 0))
    blurb = reddit_evidence_copy(c.get("num_upvotes", 0), c.get("num_comments", 0))
    project = {
        "url": c["url"],
        "title": c["title"],
        "kind": c["kind"],
        "description": c["description"],
        "topics": c["topics"],
        "momentumScore": round(momentum, 1),
        "hypeVerdict": verdict,
    }
    signal = {
        "source": "reddit",
        "metric": "upvotes",
        "value": c.get("num_upvotes", 0),
        "delta": c.get("num_comments", 0),
        "evidenceUrl": c["evidence_url"],
        "evidenceLabel": "Open Reddit thread",
        "subreddit": c.get("subreddit", ""),
        "summary": (
            f"Reddit upvotes={c.get('num_upvotes', 0)}, "
            f"comments={c.get('num_comments', 0)}"
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
        f"Posted: {c['title']} (upvotes {c.get('num_upvotes', '?')}, "
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
        "tools": [fetch_reddit_posts, write_reddit_post],
        "system_prompt": SYSTEM_PROMPT,
    }
    if checkpointer is not None:
        kwargs["checkpointer"] = checkpointer
    return create_deep_agent(**kwargs)
