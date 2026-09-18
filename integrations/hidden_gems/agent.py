"""@hidden-gems agent brain — Deep Agents harness.

Voice: the scout. Finds early evidence without inventing a trajectory.
"""

import logging
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from deepagents import create_deep_agent
from langchain_openai import ChatOpenAI
from langchain_core.tools import tool

from _shared.agent_catalog import agent_identity
from _shared.grove import grove_api_key
from _shared.evidence_copy import (
    arxiv_evidence_copy,
    community_quote_copy,
    hidden_gem_evidence_copy,
    hidden_gem_momentum_copy,
    hn_engagement_copy,
)
from _shared.momentum import _REPUBLISH_COOLDOWN_DAYS
from _shared.mongo import _get_db, get_last_published_days
from _shared.write_post import write_post
from source import (
    fetch_arxiv_candidates,
    fetch_breakout_candidates,
    fetch_hn_candidates,
)

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
1. Call fetch_hidden_gem_candidates to get today's breakout candidates (repos that passed the momentum-score gate), HN Show HN discoveries, and fresh arXiv papers with code repos.
2. For EACH candidate that passes the gate (has a momentumScore field) or was discovered via HN/arXiv, call write_hidden_gem with:
   - gem_url (exact, from the candidate)
   - verdict: "emerging" for most gems, or "hype looks real" if you see strong breakout signs
3. If no candidates pass, post nothing.
"""


_CANDIDATE_CACHE: dict[str, dict] = {}


@tool
async def fetch_hidden_gem_candidates() -> str:
    """Fetch today's hidden gems: breakout candidates that passed the momentum gate, HN Show HN posts, and arXiv papers with code repos."""
    db = _get_db()
    breakout = await fetch_breakout_candidates(db)
    hn = await fetch_hn_candidates(max_results=10)
    try:
        arxiv = await fetch_arxiv_candidates(max_results=8)
    except Exception as e:
        logging.warning("arXiv discovery failed (skipping source): %s", e)
        arxiv = []
    candidates = breakout + hn + arxiv
    if not candidates:
        return "No hidden gems found today."
    _CANDIDATE_CACHE.clear()
    _CANDIDATE_CACHE.update({c["url"]: c for c in candidates})
    lines = []
    for c in candidates:
        if c["discovery_source"] == "hacker_news":
            evidence = f"HN points={c['hn_points']} | HN comments={c['hn_comments']}"
        elif c["discovery_source"] == "arxiv":
            evidence = (
                f"arXiv paper='{c['arxiv_title'][:80]}' | "
                f"GitHub stars={c['github_stars']}"
            )
        elif c["discovery_source"] == "breakout":
            evidence = (
                f"Momentum Score={c['momentumScore']}/100 | "
                f"velocity={c['velocity']} stars/week | "
                f"GitHub stars={c['github_stars']}"
            )
            if "hn_points" in c:
                evidence += f" | HN points={c['hn_points']} (engagement-weighted)"
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

    # Cross-agent cooldown: HN discoveries and breakout repos alike are never
    # reposted (by any agent) inside the republish window.
    db = _get_db()
    last_pub = await get_last_published_days(db, gem_url)
    if last_pub < _REPUBLISH_COOLDOWN_DAYS:
        return (
            f"SKIP: {gem_url} was posted {last_pub} day(s) ago by an agent — "
            f"republish cooldown is {_REPUBLISH_COOLDOWN_DAYS} days."
        )

    if c["discovery_source"] == "hacker_news":
        value = c["hn_points"]
        metric = "hn_points"
        source = "hacker_news"
        evidence = f"HN points={value}; HN comments={c['hn_comments']}"
        momentum = min(35 + value / 10, 70)
        blurb = hidden_gem_evidence_copy(c["discovery_source"], value)
    elif c["discovery_source"] == "arxiv":
        value = c["github_stars"]
        metric = "github_stars"
        source = "github"
        evidence = f"arXiv paper='{c['arxiv_title']}'; GitHub stars={value}"
        momentum = min(40 + value / 10, 70)
        blurb = arxiv_evidence_copy(value, c["arxiv_title"])
    elif c["discovery_source"] == "breakout":
        value = c["github_stars"]
        metric = "github_stars"
        source = "github"
        momentum = c["momentumScore"]
        blurb = hidden_gem_momentum_copy(
            c["momentumScore"], c["velocity"], c["acceleration"]
        )
        evidence = blurb
        if "hn_points" in c:
            engagement_line = hn_engagement_copy(c["hn_points"], c["hn_comments"])
            blurb = f"{blurb} {engagement_line}"
            evidence = f"{evidence}; {engagement_line.rstrip('.')}"
    else:
        value = c["github_stars"]
        metric = "github_stars"
        source = "github"
        evidence = f"GitHub stars={value}; discovered in recent-repository search"
        momentum = min(40 + value / 10, 70)
        blurb = hidden_gem_evidence_copy(c["discovery_source"], value)

    top_comment = c.get("hn_top_comment")
    if top_comment:
        quote = community_quote_copy(top_comment["author"], top_comment["text"])
        blurb = f"{blurb} {quote}"

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
            else "Open arXiv paper"
            if c["discovery_source"] == "arxiv"
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
        api_key=grove_api_key(),
        base_url=os.environ["GROVE_BASE_URL"],
        default_headers={"api-key": grove_api_key()},
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
