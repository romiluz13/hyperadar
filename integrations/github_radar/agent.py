"""@github-radar agent brain — Deep Agents harness with custom tools.

The LLM (Grove via ChatOpenAI, OpenAI-compatible) orchestrates candidate selection
and verdicts. Public evidence copy is derived deterministically from cached source
values before the write tool persists MongoDB + Port twins.

Deep Agents provides planning/tool-calling on LangGraph; MongoDBSaver checkpoints
the run for durable inspection — the current MongoDB agent-memory proof.
"""

import logging
import os

from deepagents import create_deep_agent
from langchain_openai import ChatOpenAI
from langchain_core.tools import tool

from _shared import mongo
from _shared.agent_catalog import agent_identity
from _shared.evidence_copy import github_evidence_copy
from _shared.momentum import passes_fake_star_filter
from _shared.write_post import write_post
from github_source import (
    _last_published_days,
    compute_momentum,
    fetch_trending_candidates,
    fetch_trending_candidates_with_momentum,
)

AGENT_HANDLE = "@github-radar"
_IDENTITY = agent_identity(AGENT_HANDLE)
AGENT_NAME = _IDENTITY["name"]
AGENT_BIO = _IDENTITY["bio"]
SOURCE_TYPE = _IDENTITY["source_type"]

SYSTEM_PROMPT = """\
You are @github-radar, an AI dev hype tracker that scans GitHub for trending AI repositories.

Your voice: terse and data-forward. Distinguish a lifetime average from observed growth.

Workflow:
1. Call fetch_trending_repos to get today's candidate repos with their momentum data.
2. For EACH candidate that genuinely looks like it's breaking out (momentumScore >= 40),
   call write_hype_post with:
   - repo_url (exact, from the candidate)
   - verdict: one of "hype looks real", "inflated", "emerging", "cooling"
3. Skip candidates with momentumScore < 40 — don't post noise.
4. Post at most the top 20 candidates per run (quality over quantity).

Never imply the repository gained that average in the latest week. Be concrete, no filler.
"""


@tool
async def fetch_trending_repos() -> str:
    """Fetch today's trending AI repos from GitHub (search API), with computed momentum.

    Tries the shared Momentum Score path (``fetch_trending_candidates_with_momentum``)
    when a database is available, falling back to the legacy ``fetch_trending_candidates``
    path when the DB is not reachable.

    Returns a compact text listing of candidates with their momentum scores so you
    can decide which to post about.
    """
    candidates: list[dict] = []
    try:
        async_db = mongo._get_db()
        candidates = await fetch_trending_candidates_with_momentum(async_db)
    except Exception as exc:
        logging.warning("Momentum path unavailable, falling back to legacy: %s", exc)
        candidates = []

    if not candidates:
        # Legacy fallback: no DB or no repos with enough history yet.
        candidates = await fetch_trending_candidates(max_results=25)
        if not candidates:
            return "No trending candidates found today."

        # Apply fake-star filter (defense-in-depth even though
        # fetch_trending_candidates already applies a lenient version).
        candidates = [
            c
            for c in candidates
            if passes_fake_star_filter(c.get("stars", 0), c.get("forks", 0) or 0)
        ]
        if not candidates:
            return "No trending candidates passed the fake-star filter today."

        # Apply 7-day cooldown: skip repos posted < 7 days ago.
        try:
            async_db = mongo._get_db()
            cooled: list[dict] = []
            for c in candidates:
                last_pub = await _last_published_days(async_db, c["url"])
                if last_pub >= 7:
                    cooled.append(c)
            candidates = cooled
        except Exception as exc:
            logging.warning("Cooldown check unavailable for legacy path: %s", exc)
        if not candidates:
            return "No trending candidates passed the cooldown filter today."

        lines = []
        for c in candidates:
            project_id = c["url"]
            history = await mongo.get_momentum_history(
                project_id,
                source="github",
                metric="github_stars",
            )
            prior_posts = await mongo.get_prior_post_count(project_id)
            m = compute_momentum(c, history, prior_posts)
            c["_momentum"] = m  # cache for the write step
            lines.append(
                f"- {c['title']} | {c['url']}\n"
                f"  stars={c['stars']} | avg_stars/wk_since_creation="
                f"{m['avgStarsPerWeekSinceCreation']} | momentumScore={m['momentumScore']} | "
                f"sustainedSixWeekGrowth={m['sustainedSixWeekGrowth']} | "
                f"novel={m['novel']}\n"
                f"  desc: {c['description'][:120]}"
            )
        _CANDIDATE_CACHE.update({c["url"]: c for c in candidates})
        return "\n".join(lines)

    # Shared Momentum Score path: candidates already have momentumScore/velocity.
    lines = []
    for c in candidates:
        c["_momentum"] = {
            "momentumScore": c["momentumScore"],
            "velocity": c["velocity"],
            "acceleration": c["acceleration"],
            # Legacy fields expected by write_hype_post — not computed in the
            # shared path; provide neutral defaults so the write tool doesn't
            # KeyError when the momentum path is active.
            "avgStarsPerWeekSinceCreation": 0.0,
            "sustainedSixWeekGrowth": False,
        }
        lines.append(
            f"- {c['title']} | {c['url']}\n"
            f"  stars={c['stars']} | momentumScore={c['momentumScore']} | "
            f"velocity={c['velocity']} | acceleration={c['acceleration']}\n"
            f"  desc: {c['description'][:120]}"
        )
    _CANDIDATE_CACHE.update({c["url"]: c for c in candidates})
    return "\n".join(lines)


_CANDIDATE_CACHE: dict[str, dict] = {}


@tool
async def write_hype_post(repo_url: str, verdict: str) -> str:
    """Publish a hype post for a repo. Persists signals + project + post to MongoDB
    and upserts the matching Port entities (agent, project, post with relations).

    Args:
        repo_url: exact URL from the candidate listing
        verdict: one of "hype looks real", "inflated", "emerging", "cooling"
    """
    c = _CANDIDATE_CACHE.get(repo_url)
    if not c:
        return f"ERROR: unknown repo_url {repo_url}. Call fetch_trending_repos first."
    m = c.get("_momentum") or {
        "momentumScore": 0.0,
        "avgStarsPerWeekSinceCreation": 0.0,
        "sustainedSixWeekGrowth": False,
    }
    blurb = github_evidence_copy(
        m["avgStarsPerWeekSinceCreation"],
        c["stars"],
        m["sustainedSixWeekGrowth"],
    )

    project_doc = {
        "url": c["url"],
        "title": c["title"],
        "kind": c["kind"],
        "description": c["description"],
        "topics": c["topics"],
        "momentumScore": m["momentumScore"],
        "hypeVerdict": verdict,
    }
    rank_score = m["momentumScore"]  # v1: rank = momentum (reactions blend in T4)
    signal = {
        "source": "github",
        "metric": "github_stars",
        "value": c["stars"],
        "delta": 0,
        "evidenceUrl": c["url"],
        "evidenceLabel": "Open GitHub repository",
        "summary": (
            f"GitHub stars={c['stars']}; avg since creation="
            f"{m['avgStarsPerWeekSinceCreation']}/wk; "
            f"6-week sustained={'yes' if m['sustainedSixWeekGrowth'] else 'not proven'}"
        ),
    }
    post_id = await write_post(
        AGENT_HANDLE,
        AGENT_NAME,
        AGENT_BIO,
        SOURCE_TYPE,
        project_doc,
        blurb,
        verdict,
        signal,
        rank_score,
    )

    return f"Posted: {c['title']} (momentum {m['momentumScore']}, verdict '{verdict}') -> post {post_id}"


def build_agent(checkpointer=None):
    """Create the Deep Agents brain wired to Grove. Optional MongoDB checkpoint."""
    model = ChatOpenAI(
        model=os.environ["GROVE_MODEL"],
        api_key=os.environ["GROVE_API_KEY"],
        base_url=os.environ["GROVE_BASE_URL"],
        # Grove is an Azure APIM gateway: it requires the `api-key` header,
        # not the OpenAI default `Authorization: Bearer`. Send both.
        default_headers={"api-key": os.environ["GROVE_API_KEY"]},
        temperature=0.7,
    )
    kwargs = {
        "model": model,
        "tools": [fetch_trending_repos, write_hype_post],
        "system_prompt": SYSTEM_PROMPT,
    }
    if checkpointer is not None:
        kwargs["checkpointer"] = checkpointer
    return create_deep_agent(**kwargs)
