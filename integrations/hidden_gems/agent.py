"""@hidden-gems agent brain — Deep Agents harness.

Voice: the scout. Finds early evidence without inventing a trajectory.
"""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from deepagents import create_deep_agent
from langchain_openai import ChatOpenAI
from langchain_core.tools import tool

from _shared.agent_catalog import agent_identity
from _shared.evidence_copy import hidden_gem_evidence_copy, hidden_gem_momentum_copy
from _shared.mongo import _get_db
from _shared.write_post import write_post
from source import fetch_breakout_candidates, fetch_hn_candidates

AGENT_HANDLE = "@hidden-gems"
_IDENTITY = agent_identity(AGENT_HANDLE)
AGENT_NAME = _IDENTITY["name"]
AGENT_BIO = _IDENTITY["bio"]
SOURCE_TYPE = _IDENTITY["source_type"]

SYSTEM_PROMPT = """\
You are @hidden-gems, an AI dev hype tracker that finds hidden gems BEFORE they blow up.

Your voice: the scout. You find things before they trend, while naming exactly what was observed.

Only publish repos that pass the breakout gate. Each post must include the Momentum Score and velocity in the evidence. Do NOT post repos that don't pass the gate — if no repos pass, post nothing.

Workflow:
1. Call fetch_hidden_gem_candidates to get today's breakout candidates (repos that passed the momentum-score gate) and HN Show HN discoveries.
2. For EACH candidate that passes the gate (has a momentumScore field), call write_hidden_gem with:
   - gem_url (exact, from the candidate)
   - verdict: "emerging" for most gems, or "hype looks real" if you see strong breakout signs
3. If no candidates pass the gate, post nothing.
"""


_CANDIDATE_CACHE: dict[str, dict] = {}


@tool
async def fetch_hidden_gem_candidates() -> str:
    """Fetch today's hidden gems: breakout candidates that passed the momentum gate + HN Show HN posts."""
    db = _get_db()
    breakout = await fetch_breakout_candidates(db)
    hn = await fetch_hn_candidates(max_results=10)
    candidates = breakout + hn
    if not candidates:
        return "No hidden gems found today."
    _CANDIDATE_CACHE.clear()
    _CANDIDATE_CACHE.update({c["url"]: c for c in candidates})
    lines = []
    for c in candidates:
        if c["discovery_source"] == "hacker_news":
            evidence = f"HN points={c['hn_points']} | HN comments={c['hn_comments']}"
        elif c["discovery_source"] == "breakout":
            evidence = (
                f"Momentum Score={c['momentumScore']}/100 | "
                f"velocity={c['velocity']} stars/week | "
                f"GitHub stars={c['github_stars']}"
            )
        else:
            evidence = f"GitHub stars={c.get('github_stars', '?')}"
        lines.append(
            f"- {c['title']} | {c['url']}\n"
            f"  discovered_via={c['discovery_source']} | {evidence} | kind={c['kind']}\n"
            f"  desc: {c.get('description', '')[:120]}"
        )
    return "\n".join(lines)


@tool
async def write_hidden_gem(gem_url: str, verdict: str) -> str:
    """Publish a hype post about a hidden gem.

    Args:
        gem_url: exact URL from the candidate listing
        verdict: "emerging" (most gems) or "hype looks real" (breakout signs)
    """
    c = _CANDIDATE_CACHE.get(gem_url)
    if not c:
        return (
            f"ERROR: unknown gem_url {gem_url}. Call fetch_hidden_gem_candidates first."
        )

    if c["discovery_source"] == "hacker_news":
        value = c["hn_points"]
        metric = "hn_points"
        source = "hacker_news"
        evidence = f"HN points={value}; HN comments={c['hn_comments']}"
        momentum = min(35 + value / 10, 70)
        blurb = hidden_gem_evidence_copy(c["discovery_source"], value)
    elif c["discovery_source"] == "breakout":
        value = c["github_stars"]
        metric = "github_stars"
        source = "github"
        momentum = c["momentumScore"]
        blurb = hidden_gem_momentum_copy(
            c["momentumScore"], c["velocity"], c["acceleration"]
        )
        evidence = blurb
    else:
        value = c["github_stars"]
        metric = "github_stars"
        source = "github"
        evidence = f"GitHub stars={value}; discovered in recent-repository search"
        momentum = min(40 + value / 10, 70)
        blurb = hidden_gem_evidence_copy(c["discovery_source"], value)
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
        "source": source,
        "metric": metric,
        "value": value,
        "delta": 0,
        "evidenceUrl": c["evidence_url"],
        "evidenceLabel": (
            "Open HN discussion"
            if c["discovery_source"] == "hacker_news"
            else "Open GitHub repository"
        ),
        "summary": evidence,
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
    return f"Posted: {c['title']} ({evidence}, verdict '{verdict}') -> post {post_id}"


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
        "tools": [fetch_hidden_gem_candidates, write_hidden_gem],
        "system_prompt": SYSTEM_PROMPT,
    }
    if checkpointer is not None:
        kwargs["checkpointer"] = checkpointer
    return create_deep_agent(**kwargs)
