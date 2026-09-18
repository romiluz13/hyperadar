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
from _shared.corroboration import corroborated_candidates
from _shared.engagement import engagement_boost
from _shared.grove import grove_api_key
from _shared.evidence_copy import (
    community_quote_copy,
    github_evidence_copy,
    hn_engagement_copy,
)
from _shared.momentum import _REPUBLISH_COOLDOWN_DAYS, passes_fake_star_filter
from _shared.write_post import write_post
from github_source import (
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
   Every candidate is cross-source corroborated: an independent source (HN story or
   Reddit thread) noticed it in the same window, not GitHub stars alone.
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
    path only when the DB is unreachable or no repo has enough history yet. Every
    candidate then passes the cross-source corroboration gate (an HN story or Reddit
    thread noticed it in the same window), and HN engagement is added to the published
    momentum score. A momentum pool that the gate empties is reported as-is — it never
    falls back to the weaker legacy bar.

    Returns a compact text listing of candidates with their momentum scores so you
    can decide which to post about.
    """
    candidates: list[dict] = []
    momentum_candidates: list[dict] = []
    try:
        async_db = mongo._get_db()
        momentum_candidates = await fetch_trending_candidates_with_momentum(async_db)
    except Exception as exc:
        logging.warning("Momentum path unavailable, falling back to legacy: %s", exc)
        momentum_candidates = []

    if momentum_candidates:
        # Cross-source corroboration gate: GitHub discovery + at least one
        # independent community source (HN story / Reddit thread) in the same
        # window. Single-source star velocity alone is not a publishable
        # signal. A corroborated-empty pool here is a real "nothing reached
        # consensus today" outcome — it must NOT fall through to the weaker
        # legacy bar, which exists only for cold-start/no-history runs.
        candidates = await corroborated_candidates(momentum_candidates, db=async_db)
        if not candidates:
            return (
                "No trending candidates passed the cross-source corroboration "
                "gate today (no independent HN/Reddit signal in the window)."
            )

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

        # Apply the cross-agent republish cooldown (defense-in-depth: the
        # momentum path already gates, but the write tool is the last choke
        # point before a post is claimed).
        try:
            async_db = mongo._get_db()
            cooled: list[dict] = []
            for c in candidates:
                last_pub = await mongo.get_last_published_days(async_db, c["url"])
                if last_pub >= _REPUBLISH_COOLDOWN_DAYS:
                    cooled.append(c)
            candidates = cooled
        except Exception as exc:
            logging.warning("Cooldown check unavailable for legacy path: %s", exc)
        if not candidates:
            return "No trending candidates passed the cooldown filter today."

        # The corpus for the gate's Reddit leg lives in Mongo; without a DB
        # the gate falls back to (usually 403-blocked) live Reddit search.
        try:
            gate_db = mongo._get_db()
        except Exception:
            gate_db = None
        candidates = await corroborated_candidates(candidates, db=gate_db)
        if not candidates:
            return (
                "No trending candidates passed the cross-source corroboration "
                "gate today (no independent HN/Reddit signal in the window)."
            )

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
            m["momentumScore"] = _engagement_weighted_score(m["momentumScore"], c)
            c["_momentum"] = m  # cache for the write step
            lines.append(
                f"- {c['title']} | {c['url']}\n"
                f"  stars={c['stars']} | avg_stars/wk_since_creation="
                f"{m['avgStarsPerWeekSinceCreation']} | momentumScore={m['momentumScore']} | "
                f"sustainedSixWeekGrowth={m['sustainedSixWeekGrowth']} | "
                f"novel={m['novel']} | {_engagement_line(c)}\n"
                f"  desc: {c['description'][:120]}"
            )
        _CANDIDATE_CACHE.update({c["url"]: c for c in candidates})
        return "\n".join(lines)

    # Shared Momentum Score path: candidates already have momentumScore/velocity.
    lines = []
    for c in candidates:
        m = {
            "momentumScore": _engagement_weighted_score(c["momentumScore"], c),
            "velocity": c["velocity"],
            "acceleration": c["acceleration"],
            # Legacy fields expected by write_hype_post — not computed in the
            # shared path; provide neutral defaults so the write tool doesn't
            # KeyError when the momentum path is active.
            "avgStarsPerWeekSinceCreation": 0.0,
            "sustainedSixWeekGrowth": False,
        }
        c["_momentum"] = m
        lines.append(
            f"- {c['title']} | {c['url']}\n"
            f"  stars={c['stars']} | momentumScore={m['momentumScore']} | "
            f"velocity={c['velocity']} | acceleration={c['acceleration']} | "
            f"{_engagement_line(c)}\n"
            f"  desc: {c['description'][:120]}"
        )
    _CANDIDATE_CACHE.update({c["url"]: c for c in candidates})
    return "\n".join(lines)


def _engagement_weighted_score(score: float, candidate: dict) -> float:
    """Add HN engagement (post-gate) to a candidate's momentum score, capped at 100."""
    eng = candidate.get("_hn_engagement")
    if not eng:
        return score
    return min(100.0, score + engagement_boost(eng["points"], eng["comments"]))


def _engagement_line(candidate: dict) -> str:
    """One evidence line for the LLM: HN engagement when measured, plus the
    corroborating sources (a Reddit-only candidate still shows its sources)."""
    eng = candidate.get("_hn_engagement")
    sources = ",".join(candidate.get("corroborated_by", []))
    if not eng:
        return f"hn=None | corroborated_by={sources}" if sources else "hn=None"
    return (
        f"hn_points={eng['points']} | hn_comments={eng['comments']} | "
        f"corroborated_by={sources}"
    )


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

    # Cross-agent cooldown: no repo is reposted (by any agent) inside the
    # republish window, even if it re-entered the candidate cache.
    async_db = mongo._get_db()
    last_pub = await mongo.get_last_published_days(async_db, repo_url)
    if last_pub < _REPUBLISH_COOLDOWN_DAYS:
        return (
            f"SKIP: {repo_url} was posted {last_pub} day(s) ago by an agent — "
            f"republish cooldown is {_REPUBLISH_COOLDOWN_DAYS} days."
        )
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
    summary_extras = []
    eng = c.get("_hn_engagement")
    if eng:
        engagement_line = hn_engagement_copy(eng["points"], eng["comments"])
        blurb = f"{blurb} {engagement_line}"
        summary_extras.append(engagement_line.rstrip("."))
        if eng.get("top_comment"):
            blurb = (
                f"{blurb} "
                + community_quote_copy(
                    eng["top_comment"]["author"], eng["top_comment"]["text"]
                )
            )
    corroborated_by = c.get("corroborated_by") or []
    if corroborated_by:
        summary_extras.append("corroborated_by=" + ",".join(corroborated_by))

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
            + ("; " + "; ".join(summary_extras) if summary_extras else "")
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
        api_key=grove_api_key(),
        base_url=os.environ["GROVE_BASE_URL"],
        # Grove is an Azure APIM gateway: it requires the `api-key` header,
        # not the OpenAI default `Authorization: Bearer`. Send both.
        default_headers={"api-key": grove_api_key()},
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
