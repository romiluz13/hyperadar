"""A post reaches the digest even when its embedding fails (ticket 04, decision Q4).

Seams: ``runner.run_agent`` with a plain scan that calls ``write_post``,
``write_post.repair_pending_posts`` and ``daily_digest.generate_daily_digest``,
all on the real MongoDB test database. Voyage, Port, the doctor and the
digest's GitHub/HN/Grove lookups are faked.
"""

from datetime import datetime, timedelta, timezone

import pytest

from _shared import daily_digest, embeddings, mongo, port_client, runner, write_post

HANDLE = "@github-radar"
SLUG = "acme/embedless-repo"
REPO_URL = f"https://github.com/{SLUG}"
VECTOR = [0.1] * 1024


def _voyage_down(*_args, **_kwargs):
    raise RuntimeError("Voyage API unavailable")


@pytest.fixture(autouse=True)
def _offline(monkeypatch, db):
    async def no_preflight(_handle):
        return []

    monkeypatch.setattr(runner.doctor, "preflight", no_preflight)
    for name in (
        "upsert_agent",
        "upsert_project",
        "upsert_post",
        "record_agent_success",
    ):
        monkeypatch.setattr(port_client, name, lambda *_args: {"ok": True})
    _cleanup(db)
    yield
    _cleanup(db)


def _cleanup(db):
    db.posts.delete_many({"project.url": REPO_URL})
    db.projects.delete_many({"url": REPO_URL})
    db.signals.delete_many({"projectId": REPO_URL})
    db.signal_receipts.delete_many({"signal.projectId": REPO_URL})
    db.embeddings_audit.delete_many({"projectId": REPO_URL})
    db.agent_runs.delete_many({"agentHandle": HANDLE})
    db.digests.delete_many({"digestType": "daily"})


async def _write_repo_post() -> str:
    return await write_post.write_post(
        HANDLE,
        "GitHub Radar",
        "bio",
        "github",
        {
            "url": REPO_URL,
            "title": SLUG,
            "kind": "repo",
            "description": "An agent framework",
            "topics": ["ai"],
            "momentumScore": 80,
        },
        "1,200 stars this week.",
        "emerging",
        {"source": "github", "metric": "github_stars", "value": 4000, "delta": 1200},
        80,
    )


async def _run_once() -> dict:
    async def scan():
        await _write_repo_post()
        return 1

    return await runner.run_agent(
        HANDLE, "GitHub Radar", "bio", "github", plain_scan=scan
    )


def _fake_digest_sources(monkeypatch):
    async def fake_batch(slugs, now=None, client=None):
        return {SLUG: {"starsThisWeek": 1200, "starsToday": 200, "days": []}}, {}

    async def none(*_args, **_kwargs):
        return None

    async def no_readme(*_args, **_kwargs):
        return ""

    async def no_grove(_entries):
        return []

    monkeypatch.setattr(daily_digest, "fetch_stars_this_week_batch", fake_batch)
    monkeypatch.setattr(daily_digest, "_fetch_github_repo", none)
    monkeypatch.setattr(daily_digest, "_fetch_github_readme", no_readme)
    monkeypatch.setattr(daily_digest, "fetch_hn_engagement", none)
    monkeypatch.setattr(daily_digest, "fetch_hn_story", none)
    monkeypatch.setattr(daily_digest, "_call_grove", no_grove)


async def test_a_post_whose_embedding_failed_reaches_the_digest(db, monkeypatch):
    monkeypatch.setattr(embeddings, "embed_project", _voyage_down)
    _fake_digest_sources(monkeypatch)

    summary = await _run_once()

    record = db.agent_runs.find_one({"threadId": summary["thread_id"]})
    assert summary["ok"] is True
    assert record["syncedWithoutEmbedding"] == 1
    digest = await daily_digest.generate_daily_digest(
        db=mongo._get_db(), now=datetime.now(timezone.utc) + timedelta(minutes=1)
    )
    assert REPO_URL in [item["url"] for item in digest["items"]]


async def test_the_repair_path_embeds_the_post_later(db, monkeypatch):
    monkeypatch.setattr(embeddings, "embed_project", _voyage_down)
    await _run_once()
    # $vectorSearch only scores documents that carry the indexed vector.
    assert (
        db.projects.count_documents({"url": REPO_URL, "embedding": {"$exists": True}})
        == 0
    )

    monkeypatch.setattr(embeddings, "embed_project", lambda *_args: VECTOR)
    await write_post.repair_pending_posts(HANDLE, "GitHub Radar", "bio", "github")

    project = db.projects.find_one({"url": REPO_URL})
    assert project["embedding"] == VECTOR
    assert db.embeddings_audit.count_documents({"projectId": REPO_URL}) == 1
    assert (
        db.posts.count_documents({"project.url": REPO_URL, "embeddingMissing": True})
        == 0
    )
