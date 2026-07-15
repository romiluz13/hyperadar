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

Your voice: the discourse reader. Name the surfaced theme, then defer to the measured search proxy.

Workflow:
1. Call fetch_reddit_posts to get today's most visible Reddit posts from Google results.
2. Treat search visibility as a discovery proxy, never as Reddit votes or comments.
3. For EACH highly visible result, call write_reddit_post with:
   - post_url (exact, from the candidate)
   - verdict: one of "hype looks real", "inflated", "emerging", "cooling"
4. Do not invent engagement counts. Say "visible in search" when citing evidence.
5. Post at most the top 3 candidates per run.
"""


_CANDIDATE_CACHE: dict[str, dict] = {}


@tool
async def fetch_reddit_posts() -> str:
    """Fetch today's most visible Reddit AI posts from search results."""
    candidates = await fetch_reddit_candidates(max_results=10)
    if not candidates:
        return "No trending Reddit posts found today."
    _CANDIDATE_CACHE.clear()
    _CANDIDATE_CACHE.update({c["url"]: c for c in candidates})
    lines = []
    for c in candidates:
        lines.append(
            f"- {c['title']} | {c['url']}\n"
            f"  Google SERP rank={c['serp_rank']} | "
            f"visibility proxy={c['visibility_score']}/100 | "
            f"subreddit={c.get('subreddit', '?')}\n"
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

    momentum = c["visibility_score"]
    blurb = reddit_evidence_copy(c["serp_rank"], momentum)
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
        "metric": "search visibility",
        "value": momentum,
        "delta": 0,
        "evidenceUrl": c["evidence_url"],
        "evidenceLabel": "Re-run source query",
        "sourceQuery": c["search_query"],
        "summary": (
            f"Google SERP rank={c['serp_rank']}; visibility proxy={momentum}/100"
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
        f"Posted: {c['title']} (SERP rank {c['serp_rank']}, "
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
