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
publish_community_posts AND it returned real results. If \
publish_community_posts returns 'No trending community discussions found today.' \
then STOP — return 'No community discussions to publish today.' and end.

Workflow:
1. Call publish_community_posts to fetch and publish all trending community discussions.
2. Review the results — each post will include the topic, contributor count, and verdict.
3. If no discussions found, STOP and report empty.
4. Community discourse is evidence of real developer interest — cite the \
   number of contributors, not stars or views.
"""


_CANDIDATE_CACHE: dict[str, dict] = {}


@tool
async def publish_community_posts() -> str:
    """Fetch all trending community discussions and publish them in one call.

    Returns a summary of what was posted. If no discussions found, returns
    'No trending community discussions found today.'
    """
    candidates = await fetch_community_candidates(max_results=10)
    if not candidates:
        return "No trending community discussions found today."

    results = []
    for c in candidates:
        momentum = c["visibility_score"]
        contributors = c.get("num_contributors", 0)
        blurb = community_evidence_copy(contributors)
        # Deterministic verdict based on contributor engagement.
        if contributors >= 15:
            verdict = "hype looks real"
        elif contributors >= 10:
            verdict = "emerging"
        else:
            verdict = "cooling"
        # No external URL — the community corpus is private.
        # Use the URL from the source (already an internal anchor).
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
            "evidenceUrl": c.get("evidence_url") or None,
            "evidenceLabel": "AI Agents Community corpus (private)",
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
        results.append(
            f"Posted: {c['title']} ({contributors} contributors, "
            f"verdict '{verdict}') -> post {post_id}"
        )
    return f"Published {len(results)} community discussions:\n" + "\n".join(results)


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
        "tools": [publish_community_posts],
        "system_prompt": SYSTEM_PROMPT,
    }
    if checkpointer is not None:
        kwargs["checkpointer"] = checkpointer
    return create_deep_agent(**kwargs)
