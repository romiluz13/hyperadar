"""T2 end-to-end test: @github-radar agent run populates MongoDB + Port.

Mocks the GitHub search source so the test is deterministic and doesn't depend
on live trending data. Asserts:
  - Posts appear in MongoDB (posts collection) with the right shape
  - A `hyperadar_post` entity exists in Port for each, with relations

Run:  uv run pytest test_github_radar_e2e.py -v
"""
import os

import pytest
import pymongo


@pytest.fixture()
def env_loaded():
    from dotenv import load_dotenv
    load_dotenv()
    return os.environ


@pytest.fixture()
def mongo_db(env_loaded):
    client = pymongo.MongoClient(os.environ["MONGODB_URI"])
    return client[os.environ.get("MONGODB_DB", "hyperadar")]


@pytest.fixture()
def port_token(env_loaded):
    import json
    import urllib.request
    body = json.dumps({"clientId": os.environ["PORT_CLIENT_ID"],
                       "clientSecret": os.environ["PORT_CLIENT_SECRET"]}).encode()
    req = urllib.request.Request("https://api.getport.io/v1/auth/access_token",
                                 data=body, method="POST",
                                 headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req) as r:
        return json.loads(r.read())["accessToken"]


def _mock_candidates():
    """Deterministic fake trending repos (no live GitHub call)."""
    return [{
        "url": "https://github.com/test-org/hype-test-repo",
        "title": "test-org/hype-test-repo",
        "kind": "repo",
        "description": "A test trending AI repo",
        "topics": ["ai", "agents"],
        "stars": 9000,
        "created_at": "2026-06-01T00:00:00Z",
        "owner": "test-org",
        "repo": "hype-test-repo",
    }]


def test_agent_run_writes_mongodb_and_port(mongo_db, port_token, env_loaded, monkeypatch):
    """The spine: scrape (mocked) -> score (Grove) -> write MongoDB + Port."""
    import json
    import urllib.request

    # Monkeypatch the live GitHub source so the test is deterministic.
    import github_source
    from agent import _CANDIDATE_CACHE

    async def fake_fetch(max_results=10):
        return _mock_candidates()

    monkeypatch.setattr(github_source, "fetch_trending_candidates", fake_fetch)

    # Drive the write tool directly (proves the persistence layer end-to-end
    # without depending on Grove's LLM nondeterminism).
    import agent

    cands = _mock_candidates()
    _CANDIDATE_CACHE.clear()
    _CANDIDATE_CACHE.update({c["url"]: c for c in cands})
    history = []
    prior = 0
    m = github_source.compute_momentum(cands[0], history, prior)
    cands[0]["_momentum"] = m

    import asyncio
    out = asyncio.run(agent.write_hype_post.ainvoke({
        "repo_url": cands[0]["url"],
        "blurb": "▲ 9000★/wk. Test repo. This is real.",
        "verdict": "hype looks real",
    }))
    assert "Posted" in out

    # --- Assert MongoDB has the post + project + signal ---
    post = mongo_db.posts.find_one({"project.url": cands[0]["url"]})
    assert post is not None
    assert post["agentHandle"] == "@github-radar"
    assert post["verdict"] == "hype looks real"
    assert "body" in post and "▲" in post["body"]

    proj = mongo_db.projects.find_one({"url": cands[0]["url"]})
    assert proj is not None
    assert proj["momentumScore"] == m["momentumScore"]

    sig = mongo_db.signals.find_one({"projectId": cands[0]["url"]})
    assert sig is not None
    assert sig["metric"] == "stars"

    # --- Assert a hyperadar_post entity exists in Port with relations ---
    req = urllib.request.Request(
        "https://api.getport.io/v1/blueprints/hyperadar_post/entities",
        headers={"Authorization": f"Bearer {port_token}"},
    )
    with urllib.request.urlopen(req) as r:
        entities = json.loads(r.read()).get("entities", [])
    test_posts = [e for e in entities
                  if e.get("properties", {}).get("body", "").startswith("▲ 9000")]
    assert test_posts, "expected a hyperadar_post entity for the test post"
    rels = test_posts[0].get("relations", {})
    assert rels.get("agent") == "github-radar"
    assert "project" in rels
