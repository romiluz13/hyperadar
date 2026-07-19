"""@community-radar agent brain — surfaces AI agent community discussions.

Voice: the community listener. Tracks real developer discourse from the
RomBot AI Agents community corpus via the community-ask API.
"""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from deepagents import create_deep_agent
from langchain_openai import ChatOpenAI
from langchain_core.tools import tool

from _shared.agent_catalog import agent_identity
from _shared.evidence_copy import community_evidence_copy
from _shared.write_post import write_post
from source import fetch_community_candidates

AGENT_HANDLE = "@community-radar"
_IDENTITY = agent_identity(AGENT_HANDLE)
AGENT_NAME = _IDENTITY["name"]
AGENT_BIO = _IDENTITY["bio"]
SOURCE_TYPE = _IDENTITY["source_type"]

SYSTEM_PROMPT = """\
You are @community-radar, an AI dev hype tracker that surfaces trending \
discussions from the AI Agents community corpus via the RomBot community-ask API.

Your voice: the community listener. You report what real developers are \
discussing, not search visibility or star counts.

CRITICAL RULE: You MUST NOT publish any post unless you first called \
fetch_community_posts AND it returned real candidates. If \
fetch_community_posts returns 'No trending community discussions found today.' \
then STOP — do NOT call write_community_post, do NOT invent topics. \
Return 'No community discussions to publish today.' and end the run.

Workflow:
1. Call fetch_community_posts to get today's trending community discussions.
2. If no discussions found, STOP and report empty.
3. For each interesting discussion, call write_community_post with:
   - topic_title (EXACT title from the candidate listing)
   - verdict: one of "hype looks real", "inflated", "emerging", "cooling"
4. Community discourse is evidence of real developer interest — cite the \
   number of contributors, not stars or views.
5. Post at most the top 5 candidates per run.
"""


_CANDIDATE_CACHE: dict[str, dict] = {}


@tool
async def fetch_community_posts() -> str:
    """Fetch today's trending AI agent community discussions."""
    candidates = await fetch_community_candidates(max_results=10)
    if not candidates:
        return "No trending community discussions found today."
    _CANDIDATE_CACHE.clear()
    _CANDIDATE_CACHE.update({c["title"]: c for c in candidates})
    lines = []
    for c in candidates:
        lines.append(
            f"- {c['title']}\n"
            f"  contributors={c.get('num_contributors', '?')} | "
            f"who={c.get('who', '?')}\n"
            f"  summary: {c['description'][:120]}"
        )
    return "\n".join(lines)


@tool
async def write_community_post(topic_title: str, verdict: str) -> str:
    """Publish a hype post about a trending community discussion.

    Args:
        topic_title: exact topic title from the candidate listing
        verdict: one of "hype looks real", "inflated", "emerging", "cooling"
    """
    c = _CANDIDATE_CACHE.get(topic_title)
    if not c:
        return f"ERROR: unknown topic_title {topic_title}. Call fetch_community_posts first."

    momentum = c["visibility_score"]
    contributors = c.get("num_contributors", 0)
    blurb = community_evidence_copy(contributors)
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
        "source": "community",
        "metric": "contributors",
        "value": contributors,
        "delta": 0,
        "evidenceUrl": c["evidence_url"],
        "evidenceLabel": "AI Agents community corpus",
        "summary": (
            f"Community discussion with {contributors} contributors; "
            f"raised by {c.get('who', 'community members')}"
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
        f"Posted: {c['title']} ({contributors} contributors, "
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
        "tools": [fetch_community_posts, write_community_post],
        "system_prompt": SYSTEM_PROMPT,
    }
    if checkpointer is not None:
        kwargs["checkpointer"] = checkpointer
    return create_deep_agent(**kwargs)
