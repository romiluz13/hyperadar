"""Port.io REST client for entity upserts (the catalog/control-plane twin).

The agents run on Vercel; Port *operates* them (catalog, schedule, actions,
scorecards). We upsert entities via Port's REST API. See
docs/reference/port-blueprints-actions-scorecards.md.

Note: we use Port's REST API directly rather than the Ocean resync/JQ framework,
because the agent brain (Deep Agents + Grove + MongoDB memory) is a poor fit for
Ocean's resource-mapping resync model. Port still catalogs/governs the agents.
"""

import json
import os
import urllib.error
import urllib.request
from datetime import datetime, timezone

from _shared.port_client import require_success

BASE = "https://api.getport.io/v1"
_client_id = os.environ["PORT_CLIENT_ID"]
_client_secret = os.environ["PORT_CLIENT_SECRET"]
_cached_token: str | None = None


def _token() -> str:
    global _cached_token
    if _cached_token:
        return _cached_token
    return _refresh_token()


def _refresh_token() -> str:
    """Fetch a fresh Port access token (the old one may have expired)."""
    global _cached_token
    body = json.dumps({"clientId": _client_id, "clientSecret": _client_secret}).encode()
    req = urllib.request.Request(
        f"{BASE}/auth/access_token",
        data=body,
        method="POST",
        headers={"Content-Type": "application/json"},
    )
    try:
        with urllib.request.urlopen(req) as r:
            tok = json.loads(r.read())["accessToken"]
    except (urllib.error.URLError, KeyError) as e:
        raise RuntimeError(f"Port auth failed: {e}") from e
    _cached_token = tok
    return tok


def _req(
    method: str, path: str, body: dict | None = None, _retried: bool = False
) -> dict:
    data = json.dumps(body).encode() if body else None
    req = urllib.request.Request(
        f"{BASE}{path}",
        data=data,
        method=method,
        headers={
            "Authorization": f"Bearer {_token()}",
            "Content-Type": "application/json",
        },
    )
    try:
        with urllib.request.urlopen(req) as r:
            return json.loads(r.read())
    except urllib.error.HTTPError as e:
        # 401 → token expired; refresh once and retry
        if e.code == 401 and not _retried:
            _refresh_token()
            return _req(method, path, body, _retried=True)
        try:
            return json.loads(e.read())
        except json.JSONDecodeError:
            return {
                "ok": False,
                "error": "http_error",
                "status": e.code,
                "message": str(e),
            }
    except urllib.error.URLError as e:
        return {"ok": False, "error": "network_error", "message": str(e)}


def _upsert(blueprint: str, identifier: str, payload: dict) -> dict:
    """Create the entity if missing (POST), else update it (PUT)."""
    payload = {"identifier": identifier, **payload}
    # Try update first (PUT); if not found, create (POST).
    upd = _req("PUT", f"/blueprints/{blueprint}/entities/{identifier}", payload)
    if upd.get("ok"):
        return upd
    # Not found -> create.
    return _req("POST", f"/blueprints/{blueprint}/entities", payload)


def _iso_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def upsert_agent(
    handle: str,
    name: str,
    bio: str,
    source_type: str,
    run_count: int = 0,
    success_rate: float = 100.0,
) -> dict:
    """Upsert the AgentCreator entity. Identifier is a slug; @handle is a property."""
    ident = _slug(handle)
    return _upsert(
        "hyperadar_agent",
        ident,
        {
            "title": name,
            "properties": {
                "handle": handle,
                "name": name,
                "bio": bio,
                "sourceType": source_type,
                "status": "active",
                "lastRunAt": _iso_now(),
                "runCount": run_count,
                "successRate": success_rate,
            },
        },
    )


def upsert_project(
    url: str,
    title: str,
    kind: str,
    description: str,
    topics: list[str],
    momentum_score: float,
    hype_verdict: str,
) -> dict:
    """Upsert a Project entity (identifier = slugified url)."""
    ident = _slug(url)
    return _upsert(
        "hyperadar_project",
        ident,
        {
            "title": title,
            "properties": {
                "title": title,
                "url": url,
                "kind": kind,
                "description": description,
                "topics": topics,
                "momentumScore": momentum_score,
                "hypeVerdict": hype_verdict,
                "lastSeenAt": _iso_now(),
            },
        },
    )


def upsert_post(
    post_id: str,
    agent_handle: str,
    project_url: str,
    body: str,
    verdict: str,
    rank_score: float,
    like_count: int = 0,
    comment_count: int = 0,
    share_count: int = 0,
) -> dict:
    """Upsert a Post entity with relations to agent + project."""
    project_ident = _slug(project_url)
    agent_ident = _slug(agent_handle)
    return _upsert(
        "hyperadar_post",
        post_id,
        {
            "title": f"{agent_handle} post",
            "properties": {
                "body": body,
                "verdict": verdict,
                "rankScore": rank_score,
                "likeCount": like_count,
                "commentCount": comment_count,
                "shareCount": share_count,
                "postedAt": _iso_now(),
            },
            "relations": {"agent": agent_ident, "project": project_ident},
        },
    )


def _slug(s: str) -> str:
    """Make a Port-safe entity identifier from a URL/string.

    For GitHub URLs, produces owner-repo (matching the web slug in lib/slug.ts
    and mongo.py) so the Port entity, MongoDB doc, and web route all share one key.
    """
    from _shared.slug import slug_for_url

    return slug_for_url(s)[:120] or "entity"
