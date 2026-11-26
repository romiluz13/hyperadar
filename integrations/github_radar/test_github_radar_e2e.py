"""Opt-in end-to-end proof against a dedicated MongoDB test DB and Port test org."""

import asyncio
import json
import os
import urllib.error
import urllib.request
from datetime import datetime, timezone
from uuid import uuid4

import pytest


def _port_request(method: str, path: str, token: str) -> dict:
    request = urllib.request.Request(
        f"https://api.getport.io/v1{path}",
        method=method,
        headers={"Authorization": f"Bearer {token}"},
    )
    with urllib.request.urlopen(request) as response:
        payload = response.read()
    return json.loads(payload) if payload else {"ok": True}


@pytest.fixture()
def port_test_token(monkeypatch):
    client_id = os.environ.get("PORT_TEST_CLIENT_ID")
    client_secret = os.environ.get("PORT_TEST_CLIENT_SECRET")
    if not client_id or not client_secret:
        pytest.skip("dedicated PORT_TEST_CLIENT_ID/PORT_TEST_CLIENT_SECRET not set")

    body = json.dumps({"clientId": client_id, "clientSecret": client_secret}).encode()
    request = urllib.request.Request(
        "https://api.getport.io/v1/auth/access_token",
        data=body,
        method="POST",
        headers={"Content-Type": "application/json"},
    )
    with urllib.request.urlopen(request) as response:
        token = json.loads(response.read())["accessToken"]

    from _shared import port_client

    monkeypatch.setattr(port_client, "_client_id", client_id)
    monkeypatch.setattr(port_client, "_client_secret", client_secret)
    monkeypatch.setattr(port_client, "_cached_token", token)
    return token


def _scored_repo(unique: str):
    from agent import ScoredRepo
    from github_source import Repo

    return ScoredRepo(
        repo=Repo(
            slug=f"hyperadar-test/{unique}",
            url=f"https://github.com/hyperadar-test/{unique}",
            description="A deterministic test trending AI repo",
            topics=["ai", "agents"],
            stars=9000,
            forks=120,
            created_at=datetime(2026, 6, 1, tzinfo=timezone.utc),
        ),
        stars_this_week=1500,
        stars_today=300,
        hn_points=None,
        hype=2.4,
    )


def test_agent_run_writes_mongodb_and_port(db, port_test_token):
    """The write spine persists one unique project and its exact Port relations."""
    import agent
    from _shared import mongo
    from _shared.slug import project_slug_for_url

    scored = _scored_repo(f"e2e-{uuid4().hex}")
    test_url = scored.repo.url
    post_ids: list[str] = []
    cleanup_errors: list[str] = []

    async def publish():
        try:
            return await agent.publish(scored, datetime.now(timezone.utc))
        finally:
            await mongo.close_client()

    try:
        assert asyncio.run(publish())

        post = db.posts.find_one({"project.url": test_url})
        assert post is not None
        post_id = str(post["_id"])
        post_ids.append(post_id)
        assert post["agentHandle"] == "@github-radar"
        assert post["verdict"] == "hype looks real"
        assert post["portSyncStatus"] == "synced"

        project = db.projects.find_one({"url": test_url})
        assert project is not None
        assert project["momentumScore"] == 60
        assert db.signals.find_one({"projectId": test_url, "metric": "github_stars"})

        port_post = _port_request(
            "GET",
            f"/blueprints/hyperadar_post/entities/{post_id}",
            port_test_token,
        )["entity"]
        assert port_post["relations"]["agent"] == "github-radar"
        assert port_post["relations"]["project"] == project_slug_for_url(test_url)
    finally:
        post_ids = [
            str(item["_id"])
            for item in db.posts.find({"project.url": test_url}, {"_id": 1})
        ]
        for post_id in post_ids:
            try:
                _port_request(
                    "DELETE",
                    f"/blueprints/hyperadar_post/entities/{post_id}",
                    port_test_token,
                )
            except urllib.error.HTTPError as error:
                if error.code != 404:
                    cleanup_errors.append(f"Port post {post_id}: {error}")
        try:
            _port_request(
                "DELETE",
                f"/blueprints/hyperadar_project/entities/{project_slug_for_url(test_url)}",
                port_test_token,
            )
        except urllib.error.HTTPError as error:
            if error.code != 404:
                cleanup_errors.append(f"Port project: {error}")

        db.reactions.delete_many({"postId": {"$in": post_ids}})
        db.posts.delete_many({"project.url": test_url})
        db.projects.delete_many({"url": test_url})
        db.signals.delete_many({"projectId": test_url})
        db.signal_receipts.delete_many({"signal.projectId": test_url})
        db.embeddings_audit.delete_many({"projectId": test_url})

        if cleanup_errors:
            raise AssertionError("; ".join(cleanup_errors))
