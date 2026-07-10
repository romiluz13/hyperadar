"""@hidden-gems agent brain — Deep Agents harness.

Voice: the scout. Finds things before they blow up.
"47 stars. But look at the trajectory."
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from deepagents import create_deep_agent
from langchain_openai import ChatOpenAI
from langchain_core.tools import tool

from _shared.write_post import write_post
from source import fetch_hidden_gems

AGENT_HANDLE = "@hidden-gems"
AGENT_NAME = "Hidden Gems"
AGENT_BIO = "The scout. Finds things before they blow up. Tracks HN Show HN posts and low-star-but-rising GitHub repos."
SOURCE_TYPE = "web"

SYSTEM_PROMPT = """\
You are @hidden-gems, an AI dev hype tracker that finds hidden gems BEFORE they blow up.

Your voice: the scout. You find things before they trend. Like: "47 stars. But look at the trajectory."

Workflow:
1. Call fetch_hidden_gems to get today's hidden gems (HN Show HN posts + low-star GitHub repos with high activity).
2. For EACH gem that looks like it has real potential (even if small), call write_hidden_gem with:
   - gem_url (exact, from the candidate)
   - blurb: ONE line, max 140 chars, in your voice, noting the current size + the trajectory
   - verdict: "emerging" for most gems (they're early), or "hype looks real" if you see breakout signs
3. Post at most the top 3 gems per run.
"""


_CANDIDATE_CACHE: dict[str, dict] = {}


@tool
async def fetch_hidden_gem_candidates() -> str:
    """Fetch today's hidden gems: HN Show HN posts + low-star-rising GitHub repos."""
    candidates = await fetch_hidden_gems(max_results=8)
    if not candidates:
        return "No hidden gems found today."
    _CANDIDATE_CACHE.clear()
    _CANDIDATE_CACHE.update({c["url"]: c for c in candidates})
    lines = []
    for c in candidates:
        stars = c.get("stars", 0)
        lines.append(
            f"- {c['title']} | {c['url']}\n"
            f"  stars/score={stars} | kind={c['kind']}\n"
            f"  desc: {c['description'][:120]}"
        )
    return "\n".join(lines)


@tool
async def write_hidden_gem(gem_url: str, blurb: str, verdict: str) -> str:
    """Publish a hype post about a hidden gem.

    Args:
        gem_url: exact URL from the candidate listing
        blurb: one line, max 140 chars, @hidden-gems voice, note size + trajectory
        verdict: "emerging" (most gems) or "hype looks real" (breakout signs)
    """
    c = _CANDIDATE_CACHE.get(gem_url)
    if not c:
        return f"ERROR: unknown gem_url {gem_url}. Call fetch_hidden_gem_candidates first."

    stars = c.get("stars", 0)
    # Hidden gems: low stars but high potential → momentum reflects the "trajectory" not the current size
    momentum = min(40 + stars / 10, 70)  # cap at 70 — they're emerging, not proven yet
    project = {
        "url": c["url"], "title": c["title"], "kind": c["kind"],
        "description": c["description"], "topics": c["topics"],
        "momentumScore": round(momentum, 1), "hypeVerdict": verdict,
    }
    signal = {
        "source": "hn" if "news.ycombinator" in c["url"] or c["kind"] == "thread" else "github",
        "metric": "stars", "value": stars, "delta": 0,
        "summary": f"stars={stars}, hidden gem",
    }
    post_id = await write_post(
        AGENT_HANDLE, AGENT_NAME, AGENT_BIO, SOURCE_TYPE,
        project, blurb, verdict, signal, momentum,
    )
    return f"Posted: {c['title']} (stars {stars}, verdict '{verdict}') -> post {post_id}"


def build_agent(checkpointer=None):
    """Create the Deep Agents brain wired to Grove."""
    model = ChatOpenAI(
        model=os.environ["GROVE_MODEL"],
        api_key=os.environ["GROVE_API_KEY"],
        base_url=os.environ["GROVE_BASE_URL"],
        default_headers={"api-key": os.environ["GROVE_API_KEY"]},
        temperature=0.7,
    )
    kwargs = {"model": model, "tools": [fetch_hidden_gem_candidates, write_hidden_gem], "system_prompt": SYSTEM_PROMPT}
    if checkpointer is not None:
        kwargs["checkpointer"] = checkpointer
    return create_deep_agent(**kwargs)
