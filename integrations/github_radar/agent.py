"""@github-radar agent brain — Deep Agents harness with custom tools.

The LLM (Grove via ChatOpenAI, OpenAI-compatible) orchestrates: fetch candidates,
inspect momentum, and for each worth posting write a blurb + verdict in the
@github-radar voice, then persist to MongoDB + Port via the write tool.

Deep Agents provides planning/tool-calling on LangGraph; MongoDBSaver checkpoints
the run (durable, resumable) — the showcase of MongoDB as agent memory.
"""
import os

from deepagents import create_deep_agent
from langchain_openai import ChatOpenAI
from langchain_core.tools import tool

import mongo
import port_client
from github_source import fetch_trending_candidates, compute_momentum

AGENT_HANDLE = "@github-radar"
AGENT_NAME = "GitHub Radar"
AGENT_BIO = "The numbers nerd. Leads with velocity. Terse, data-forward. Tracks trending AI repos on GitHub."
SOURCE_TYPE = "github"

SYSTEM_PROMPT = """\
You are @github-radar, an AI dev hype tracker that scans GitHub for trending AI repositories.

Your voice: terse, data-forward, you lead with velocity. Like: "▲ 2.3k/wk. 6-week sustained growth. This is real."

Workflow:
1. Call fetch_trending_repos to get today's candidate repos with their momentum data.
2. For EACH candidate that genuinely looks like it's breaking out (momentumScore >= 40),
   call write_hype_post with:
   - repo_url (exact, from the candidate)
   - blurb: ONE line, max 140 chars, in your voice, leading with the velocity number
   - verdict: one of "hype looks real", "inflated", "emerging", "cooling"
3. Skip candidates with momentumScore < 40 — don't post noise.
4. Post at most the top 3 candidates per run (quality over quantity).

The blurb must start with the stars/week figure (e.g. "▲ 12k★/wk"). Be concrete, no filler.
"""


@tool
async def fetch_trending_repos() -> str:
    """Fetch today's trending AI repos from GitHub (search API), with computed momentum.

    Returns a compact text listing of candidates with their momentum scores so you
    can decide which to post about.
    """
    candidates = await fetch_trending_candidates(max_results=10)
    if not candidates:
        return "No trending candidates found today."

    lines = []
    for c in candidates:
        project_id = c["url"]
        history = await mongo.get_momentum_history(project_id)
        prior_posts = await mongo.get_prior_post_count(project_id)
        m = compute_momentum(c, history, prior_posts)
        c["_momentum"] = m  # cache for the write step
        lines.append(
            f"- {c['title']} | {c['url']}\n"
            f"  stars={c['stars']} | stars/wk={m['starsPerWeek']} | "
            f"momentumScore={m['momentumScore']} | sustained={m['sustained']} | "
            f"novel={m['novel']}\n"
            f"  desc: {c['description'][:120]}"
        )
    # stash candidates on a module-level cache so write_hype_post can look up real data
    _CANDIDATE_CACHE.update({c["url"]: c for c in candidates})
    return "\n".join(lines)


_CANDIDATE_CACHE: dict[str, dict] = {}


@tool
async def write_hype_post(repo_url: str, blurb: str, verdict: str) -> str:
    """Publish a hype post for a repo. Persists signals + project + post to MongoDB
    and upserts the matching Port entities (agent, project, post with relations).

    Args:
        repo_url: exact URL from the candidate listing
        blurb: one line, max 140 chars, @github-radar voice, lead with velocity
        verdict: one of "hype looks real", "inflated", "emerging", "cooling"
    """
    c = _CANDIDATE_CACHE.get(repo_url)
    if not c:
        return f"ERROR: unknown repo_url {repo_url}. Call fetch_trending_repos first."
    m = c.get("_momentum") or {"momentumScore": 0.0, "starsPerWeek": 0.0}

    project_id = c["url"]
    project_doc = {
        "url": c["url"], "title": c["title"], "kind": c["kind"],
        "description": c["description"], "topics": c["topics"],
        "momentumScore": m["momentumScore"], "hypeVerdict": verdict,
    }
    # 1. MongoDB: upsert project + insert signal + insert post (source of truth)
    await mongo.upsert_project(project_doc)
    await mongo.insert_signal({
        "projectId": project_id, "source": "github",
        "metric": "stars", "value": c["stars"],
        "delta": m["starsPerWeek"],
    })
    rank_score = m["momentumScore"]  # v1: rank = momentum (reactions blend in T4)
    post_doc = {
        "agentHandle": AGENT_HANDLE, "body": blurb, "verdict": verdict,
        "rankScore": rank_score,
        "project": {"url": c["url"], "title": c["title"], "kind": c["kind"],
                     "momentumScore": m["momentumScore"]},
        "signalsSummary": f"stars={c['stars']}, +{m['starsPerWeek']}/wk",
    }
    post_id = await mongo.insert_post(post_doc)

    # 2. Port: upsert agent + project + post entities (catalog twin)
    port_client.upsert_agent(AGENT_HANDLE, AGENT_NAME, AGENT_BIO, SOURCE_TYPE)
    port_client.upsert_project(c["url"], c["title"], c["kind"], c["description"],
                                c["topics"], m["momentumScore"], verdict)
    port_client.upsert_post(post_id, AGENT_HANDLE, c["url"], blurb, verdict, rank_score)

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
    kwargs = {"model": model, "tools": [fetch_trending_repos, write_hype_post], "system_prompt": SYSTEM_PROMPT}
    if checkpointer is not None:
        kwargs["checkpointer"] = checkpointer
    return create_deep_agent(**kwargs)
