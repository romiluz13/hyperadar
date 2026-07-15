"""Port REST client for the catalog twin of a published HypeRadar post.

The live Port Workflow dispatches a GitHub Actions runner through Port's GitHub
integration. The Python agents then upsert agent, project, and post entities via
this client. They are not custom Ocean integrations.
"""

import json
import os
import time
import urllib.error
import urllib.request
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from urllib.parse import urlparse

BASE = "https://api.getport.io/v1"
_client_id = os.environ["PORT_CLIENT_ID"]
_client_secret = os.environ["PORT_CLIENT_SECRET"]
_cached_token: str | None = None
MAX_RETRY_AFTER_SECONDS = 30.0


def require_success(result: dict, operation: str) -> dict:
    if result.get("ok") is True:
        return result
    message = result.get("message") or result.get("error") or "unknown Port error"
    raise RuntimeError(f"Port {operation} failed: {message}")


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
        with urllib.request.urlopen(req, timeout=30) as r:
            tok = json.loads(r.read())["accessToken"]
    except (urllib.error.URLError, TimeoutError, KeyError) as e:
        raise RuntimeError(f"Port auth failed: {e}") from e
    _cached_token = tok
    return tok


def _retry_delay(headers, attempt: int) -> float:
    value = headers.get("Retry-After") if headers else None
    if value:
        try:
            return min(max(float(value), 0), MAX_RETRY_AFTER_SECONDS)
        except ValueError:
            try:
                retry_at = parsedate_to_datetime(value)
                if retry_at.tzinfo is None:
                    retry_at = retry_at.replace(tzinfo=timezone.utc)
                return min(
                    max((retry_at - datetime.now(timezone.utc)).total_seconds(), 0),
                    MAX_RETRY_AFTER_SECONDS,
                )
            except (TypeError, ValueError, OverflowError):
                pass
    return float(min(2**attempt, 4))


def _req(
    method: str,
    path: str,
    body: dict | None = None,
    _attempt: int = 0,
    _auth_retried: bool = False,
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
        with urllib.request.urlopen(req, timeout=30) as r:
            payload = r.read()
            return json.loads(payload) if payload else {"ok": True, "status": r.status}
    except urllib.error.HTTPError as e:
        # 401 → token expired; refresh once and retry
        if e.code == 401 and not _auth_retried:
            _refresh_token()
            return _req(
                method,
                path,
                body,
                _attempt=_attempt,
                _auth_retried=True,
            )
        if (e.code == 429 or 500 <= e.code < 600) and _attempt < 2:
            delay = _retry_delay(e.headers, _attempt)
            e.close()
            time.sleep(delay)
            return _req(
                method,
                path,
                body,
                _attempt=_attempt + 1,
                _auth_retried=_auth_retried,
            )
        try:
            response = json.loads(e.read())
            if not isinstance(response, dict):
                response = {"message": str(response)}
            response.setdefault("ok", False)
            response.setdefault("status", e.code)
            return response
        except json.JSONDecodeError:
            return {
                "ok": False,
                "error": "http_error",
                "status": e.code,
                "message": str(e),
            }
    except (urllib.error.URLError, TimeoutError) as e:
        if _attempt < 2:
            time.sleep(_retry_delay(None, _attempt))
            return _req(
                method,
                path,
                body,
                _attempt=_attempt + 1,
                _auth_retried=_auth_retried,
            )
        return {"ok": False, "error": "network_error", "message": str(e)}


def _upsert(
    blueprint: str,
    identifier: str,
    payload: dict,
    create_defaults: dict | None = None,
) -> dict:
    """Patch an entity, creating it only after an authoritative 404."""
    payload = {"identifier": identifier, **payload}
    upd = _req("PATCH", f"/blueprints/{blueprint}/entities/{identifier}", payload)
    if upd.get("ok"):
        return upd
    if upd.get("status") != 404:
        return upd

    defaults = create_defaults or {}
    create_payload = {
        **defaults,
        **payload,
        "properties": {
            **defaults.get("properties", {}),
            **payload.get("properties", {}),
        },
        "relations": {
            **defaults.get("relations", {}),
            **payload.get("relations", {}),
        },
    }
    created = _req("POST", f"/blueprints/{blueprint}/entities", create_payload)
    if created.get("status") == 409:
        return _req("PATCH", f"/blueprints/{blueprint}/entities/{identifier}", payload)
    return created


def _iso_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _iso_datetime(value: datetime | str | None) -> str:
    if not isinstance(value, datetime):
        return value or _iso_now()
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    return value.isoformat()


def upsert_agent(
    handle: str,
    name: str,
    bio: str,
    source_type: str,
) -> dict:
    """Sync agent identity without overwriting operator-owned health state."""
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
            },
        },
        create_defaults={"properties": {"status": "active"}},
    )


def record_agent_success(handle: str) -> dict:
    """Record only an observed successful publication cycle."""
    ident = _slug(handle)
    return _req(
        "PATCH",
        f"/blueprints/hyperadar_agent/entities/{ident}",
        {"properties": {"lastRunAt": _iso_now()}},
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
    """Upsert a Project entity under its collision-resistant URL identity."""
    ident = _project_slug(url)
    source_url = _port_source_url(url)
    now = _iso_now()
    return _upsert(
        "hyperadar_project",
        ident,
        {
            "title": title,
            "properties": {
                "title": title,
                "url": source_url,
                "kind": kind,
                "description": description,
                "topics": topics,
                "momentumScore": momentum_score,
                "hypeVerdict": hype_verdict,
                "lastSeenAt": now,
            },
        },
        create_defaults={"properties": {"firstSeenAt": now}},
    )


def _port_source_url(url: str) -> str:
    parsed = urlparse(url)
    if parsed.scheme != "hyperadar":
        return url
    app_url = os.environ.get(
        "NEXT_PUBLIC_APP_URL", "https://web-ebon-nu-43.vercel.app"
    ).rstrip("/")
    if parsed.hostname == "digest":
        return f"{app_url}/digest/{parsed.path.lstrip('/')}"
    return f"{app_url}/project/{_project_slug(url)}"


def upsert_post(
    post_id: str,
    agent_handle: str,
    project_url: str,
    body: str,
    verdict: str,
    rank_score: float,
    posted_at: datetime | str | None = None,
    like_count: int = 0,
    comment_count: int = 0,
    share_count: int = 0,
    signals_summary: str = "",
) -> dict:
    """Upsert a Post twin; reaction counts are snapshots at this catalog sync."""
    project_ident = _project_slug(project_url)
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
                "signalsSummary": signals_summary,
                "postedAt": _iso_datetime(posted_at),
            },
            "relations": {"agent": agent_ident, "project": project_ident},
        },
    )


def delete_project_entity(identifier: str) -> dict:
    """Delete one retired project identity after every post relation moved."""
    return _req("DELETE", f"/blueprints/hyperadar_project/entities/{identifier}")


def delete_post_entity(identifier: str) -> dict:
    """Remove a Port twin for a terminally quarantined MongoDB post."""
    return _req("DELETE", f"/blueprints/hyperadar_post/entities/{identifier}")


def _slug(s: str) -> str:
    """Make a Port-safe identifier for non-project entities such as agents."""
    from .slug import slug_for_url

    return slug_for_url(s)[:120] or "entity"


def _project_slug(url: str) -> str:
    from .slug import project_slug_for_url

    return project_slug_for_url(url)
