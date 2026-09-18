"""Source doctor — live credential and source health, before each agent run.

<!-- scar: 2026-09-16 — the Grove key and the rombot token both died silently;
every scheduled run went red with a stack trace and no diagnosis for days.
Presence-only secret validation cannot catch a dead-but-set credential, so
each check here makes a real (cheap) request against the live source. -->

Each check returns ``{"name", "status", "detail"}`` with status ``"ok"`` or
``"fail"``. ``preflight`` prints one ``[doctor]`` line per check into the run
log — it never raises, because a doctor crash would take down the agent it
is trying to diagnose. The runner treats the doctor as diagnostics only;
run health is decided by ``_run_ok``.
"""

import os
from datetime import datetime, timedelta, timezone

import httpx

from _shared import mongo
from _shared.grove import grove_api_key

_GITHUB_API = "https://api.github.com"
_HN_ALGOLIA = "https://hn.algolia.com/api/v1/search"
_ROMBOT_API_URL = "https://api.rombot.uk/api/community-ask"
_YOUTUBE_API = "https://www.googleapis.com/youtube/v3/videos"

# Which checks each agent's run actually depends on. Keyed by agent handle.
AGENT_CHECKS = {
    # github-radar's corroboration gate depends on HN and on the
    # @reddit-pulse corpus (Reddit's public search API is 403-blocked).
    "@github-radar": (
        "grove",
        "mongodb",
        "github_token",
        "hn_algolia",
        "reddit_corpus",
    ),
    # hidden-gems depends on HN for discovery and the engagement boost. Its
    # arXiv source was removed (export.arxiv.org unreliably 406s the Python
    # client), so no arxiv check is required.
    "@hidden-gems": ("grove", "mongodb", "github_token", "hn_algolia"),
    "@reddit-pulse": ("grove", "mongodb", "brightdata"),
    "@youtube-trends": ("grove", "mongodb", "youtube_key"),
    "@community-radar": ("grove", "mongodb", "rombot"),
    "@weekly-digest": ("grove", "mongodb"),
}


def _ok(name: str, detail: str) -> dict:
    return {"name": name, "status": "ok", "detail": detail}


def _fail(name: str, detail: str) -> dict:
    return {"name": name, "status": "fail", "detail": detail}


async def check_grove(client=None) -> dict:
    """One-token chat completion through the Grove gateway.

    The only definitive test: the key is accepted and the model responds.
    Both ``api-key`` and ``Authorization`` headers are sent, matching the
    agent brains' configuration.
    """
    key = grove_api_key()
    base = os.environ.get("GROVE_BASE_URL", "").rstrip("/")
    model = os.environ.get("GROVE_MODEL", "")
    if not key or not base:
        return _fail("grove", "GROVE_API_KEY or GROVE_BASE_URL not set")
    owns_client = client is None
    if client is None:
        client = httpx.AsyncClient(timeout=30)
    try:
        r = await client.post(
            f"{base}/chat/completions",
            headers={"api-key": key, "Authorization": f"Bearer {key}"},
            json={
                "model": model,
                "messages": [{"role": "user", "content": "ping"}],
                "max_tokens": 1,
            },
        )
        if r.status_code == 200:
            return _ok("grove", f"gateway accepted the key (model {model})")
        return _fail("grove", f"gateway returned HTTP {r.status_code} for the key")
    except httpx.HTTPError as e:
        return _fail("grove", f"request failed: {e}")
    finally:
        if owns_client:
            await client.aclose()


async def check_mongodb() -> dict:
    try:
        db = mongo._get_db()
        await db.command("ping")
        return _ok("mongodb", "ping succeeded")
    except Exception as e:
        return _fail("mongodb", f"ping failed: {e}")


async def check_github_token(client=None) -> dict:
    """Rate-limit endpoint: authenticated when a token is present."""
    token = os.environ.get("GITHUB_TOKEN", "")
    headers = {"Authorization": f"token {token}"} if token else {}
    owns_client = client is None
    if client is None:
        client = httpx.AsyncClient(timeout=15)
    try:
        r = await client.get(f"{_GITHUB_API}/rate_limit", headers=headers)
        if r.status_code != 200:
            return _fail(
                "github_token",
                f"HTTP {r.status_code} "
                + ("with token" if token else "(anonymous, no GITHUB_TOKEN set)"),
            )
        if token:
            return _ok("github_token", "token accepted")
        return _ok("github_token", "no GITHUB_TOKEN set; anonymous limits apply")
    except httpx.HTTPError as e:
        return _fail("github_token", f"request failed: {e}")
    finally:
        if owns_client:
            await client.aclose()


async def check_hn_algolia(client=None) -> dict:
    owns_client = client is None
    if client is None:
        client = httpx.AsyncClient(timeout=15)
    try:
        r = await client.get(
            _HN_ALGOLIA, params={"tags": "show_hn", "hitsPerPage": 1}
        )
        if r.status_code == 200:
            return _ok("hn_algolia", "search API reachable")
        return _fail("hn_algolia", f"HTTP {r.status_code}")
    except httpx.HTTPError as e:
        return _fail("hn_algolia", f"request failed: {e}")
    finally:
        if owns_client:
            await client.aclose()


async def check_rombot(client=None) -> dict:
    """A minimal community-ask call: the token's 401 is what we must catch."""
    token = os.environ.get("ROMBOT_COMMUNITY_ASK_TOKEN", "")
    if not token:
        return _fail("rombot", "ROMBOT_COMMUNITY_ASK_TOKEN not set")
    owns_client = client is None
    if client is None:
        client = httpx.AsyncClient(timeout=30)
    try:
        r = await client.post(
            _ROMBOT_API_URL,
            json={"message": "ping"},
            headers={"X-Community-Ask-Token": token},
        )
        if r.status_code in (200, 201):
            return _ok("rombot", "token accepted")
        return _fail(
            "rombot",
            f"HTTP {r.status_code} — "
            + ("token rejected" if r.status_code in (401, 403) else "unexpected"),
        )
    except httpx.HTTPError as e:
        return _fail("rombot", f"request failed: {e}")
    finally:
        if owns_client:
            await client.aclose()


async def check_youtube_key(client=None) -> dict:
    """1-quota-unit videos.list call: proves the key itself is valid."""
    key = os.environ.get("YOUTUBE_API_KEY", "")
    if not key:
        return _fail("youtube_key", "YOUTUBE_API_KEY not set")
    owns_client = client is None
    if client is None:
        client = httpx.AsyncClient(timeout=15)
    try:
        r = await client.get(
            _YOUTUBE_API,
            params={
                "part": "id",
                "chart": "mostPopular",
                "maxResults": 1,
                "regionCode": "US",
                "key": key,
            },
        )
        if r.status_code == 200:
            return _ok("youtube_key", "key accepted")
        return _fail("youtube_key", f"HTTP {r.status_code} — key rejected or invalid")
    except httpx.HTTPError as e:
        return _fail("youtube_key", f"request failed: {e}")
    finally:
        if owns_client:
            await client.aclose()


async def check_brightdata() -> dict:
    """Presence-only: the brightdata CLI cannot be exercised cheaply from Python."""
    if os.environ.get("BRIGHTDATA_API_KEY"):
        return _ok("brightdata", "configured (presence check; live call needs the CLI)")
    return _fail("brightdata", "BRIGHTDATA_API_KEY not set")


async def check_reddit_corpus() -> dict:
    """Fresh @reddit-pulse snapshots: the gate's Reddit leg depends on them.

    Reddit's public search API 403-blocks unauthenticated clients, so the
    corroboration gate matches against the corpus @reddit-pulse collects.
    A corpus older than two days means the Reddit leg is blind — the gate
    still runs on HN alone, but the operator should know.
    """
    try:
        db = mongo._get_db()
        since = datetime.now(timezone.utc) - timedelta(hours=48)
        count = await db.reddit_post_snapshots.count_documents(
            {"capturedAt": {"$gte": since}}
        )
    except Exception as e:
        return _fail("reddit_corpus", f"count query failed: {e}")
    if count > 0:
        return _ok("reddit_corpus", f"{count} thread snapshots in the last 48h")
    return _fail(
        "reddit_corpus",
        "no snapshots in the last 48h — @reddit-pulse is stale or never ran; "
        "the gate's Reddit leg is blind",
    )


CHECKS = {
    "grove": check_grove,
    "mongodb": check_mongodb,
    "github_token": check_github_token,
    "hn_algolia": check_hn_algolia,
    "rombot": check_rombot,
    "youtube_key": check_youtube_key,
    "brightdata": check_brightdata,
    "reddit_corpus": check_reddit_corpus,
}


def required_checks(agent_handle: str) -> tuple:
    """The checks an agent's run depends on; unknown handles get the core set."""
    return AGENT_CHECKS.get(agent_handle, ("grove", "mongodb"))


async def preflight(agent_handle: str) -> list[dict]:
    """Run this agent's checks, print one [doctor] line each, never raise."""
    results = []
    for name in required_checks(agent_handle):
        check = CHECKS[name]
        try:
            result = await check()
        except Exception as e:  # a crashing check is a finding, not an outage
            result = _fail(name, f"check crashed: {e}")
        results.append(result)
        marker = "OK" if result["status"] == "ok" else "FAIL"
        print(f"[doctor] {name}: {marker} — {result['detail']}", flush=True)
    return results
