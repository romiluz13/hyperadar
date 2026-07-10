"""@reddit-pulse agent brain — Deep Agents harness with custom tools.

Voice: the vibe reader. Cares about discourse energy, not just upvotes.
"r/LocalLLaMA can't shut up about this — 3 front-page threads this week."
"""

import os
import sys

# Add parent dir to path so we can import the _shared package
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from deepagents import create_deep_agent
from langchain_openai import ChatOpenAI
from langchain_core.tools import tool

from _shared.write_post import write_post
from reddit_source import fetch_reddit_candidates

AGENT_HANDLE = "@reddit-pulse"
AGENT_NAME = "Reddit Pulse"
AGENT_BIO = "The vibe reader. Cares about discourse energy, not just upvotes. Tracks what AI dev subreddits are buzzing about."
SOURCE_TYPE = "reddit"

SYSTEM_PROMPT = """\
You are @reddit-pulse, an AI dev hype tracker that scans Reddit for trending AI discussions.

Your voice: the vibe reader. You care about discourse energy, not just upvotes. Like: "r/LocalLLaMA can't shut up about this — 3 front-page threads this week."

Workflow:
1. Call fetch_reddit_posts to get today's trending Reddit posts with their upvotes + comment counts.
2. For EACH post that genuinely looks like it's buzzing (upvotes >= 50 OR comments >= 30), call write_reddit_post with:
   - post_url (exact, from the candidate)
   - blurb: ONE line, max 140 chars, in your voice, leading with the vibe
   - verdict: one of "hype looks real", "inflated", "emerging", "cooling"
3. Skip posts with low engagement — don't post noise.
4. Post at most the top 3 candidates per run.
"""


_CANDIDATE_CACHE: dict[str, dict] = {}


@tool
async def fetch_reddit_posts() -> str:
    """Fetch today's trending Reddit AI posts with upvotes + comment counts."""
    candidates = await fetch_reddit_candidates(max_results=10)
    if not candidates:
        return "No trending Reddit posts found today."
    _CANDIDATE_CACHE.clear()
    _CANDIDATE_CACHE.update({c["url"]: c for c in candidates})
    lines = []
    for c in candidates:
        lines.append(
            f"- {c['title']} | {c['url']}\n"
            f"  upvotes={c['upvotes']} | comments={c['num_comments']} | "
            f"subreddit={c.get('subreddit', '?')}\n"
            f"  desc: {c['description'][:120]}"
        )
    return "\n".join(lines)


@tool
async def write_reddit_post(post_url: str, blurb: str, verdict: str) -> str:
    """Publish a hype post about a Reddit thread or Reddit-discovered repo.

    Args:
        post_url: exact URL from the candidate listing
        blurb: one line, max 140 chars, @reddit-pulse voice, lead with the vibe
        verdict: one of "hype looks real", "inflated", "emerging", "cooling"
    """
    c = _CANDIDATE_CACHE.get(post_url)
    if not c:
        return f"ERROR: unknown post_url {post_url}. Call fetch_reddit_posts first."

    momentum = min(c["upvotes"] / 10, 100)  # 1000 upvotes = 100
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
        "value": c["upvotes"],
        "delta": c["num_comments"],
        "summary": f"upvotes={c['upvotes']}, comments={c['num_comments']}",
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
    return f"Posted: {c['title']} (upvotes {c['upvotes']}, verdict '{verdict}') -> post {post_id}"


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
