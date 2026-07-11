"""Port.io actions + scorecards setup for HypeRadar.

Creates the 6 self-service actions and 3 scorecards via the Port REST API.
Idempotent — safe to re-run.

Usage:
    set -a && source .env && set +a
    uv run --with pymongo python scripts/setup_port.py
"""

import json
import os
import urllib.error
import urllib.request

BASE = "https://api.getport.io/v1"
_client_id = os.environ["PORT_CLIENT_ID"]
_client_secret = os.environ["PORT_CLIENT_SECRET"]
_token: str | None = None


def _get_token() -> str:
    global _token
    if _token:
        return _token
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
    _token = tok
    return tok


def _req(method: str, path: str, body: dict | None = None) -> dict:
    data = json.dumps(body).encode() if body else None
    req = urllib.request.Request(
        f"{BASE}{path}",
        data=data,
        method=method,
        headers={
            "Authorization": f"Bearer {_get_token()}",
            "Content-Type": "application/json",
        },
    )
    try:
        with urllib.request.urlopen(req) as r:
            return json.loads(r.read())
    except urllib.error.HTTPError as e:
        try:
            return json.loads(e.read())
        except ValueError:
            return {"ok": False, "error": "http_error", "status": e.code}


# --- Self-Service Actions ---

ACTIONS = [
    {
        "identifier": "run_agent_now",
        "title": "Run Agent Now",
        "icon": "Rocket",
        "trigger": {
            "type": "self-service",
            "operation": "DAY-2",
            "userInputs": {
                "properties": {
                    "agent_handle": {
                        "type": "string",
                        "title": "Agent to run",
                        "description": "Agent handle to run",
                        "enum": [
                            "@github-radar",
                            "@reddit-pulse",
                            "@youtube-trends",
                            "@hidden-gems",
                            "@weekly-digest",
                        ],
                    },
                },
                "required": ["agent_handle"],
            },
        },
        "invocationMethod": {
            "type": "WEBHOOK",
            "url": os.environ.get("NEXT_PUBLIC_APP_URL", "http://localhost:3000")
            + "/api/port/webhook",
        },
    },
    {
        "identifier": "track_project",
        "title": "Track Project",
        "icon": "Star",
        "trigger": {
            "type": "self-service",
            "operation": "CREATE",
            "userInputs": {
                "properties": {
                    "project_url": {
                        "type": "string",
                        "title": "Project URL",
                        "description": "URL of the project to track",
                        "format": "url",
                    },
                },
                "required": ["project_url"],
            },
        },
        "invocationMethod": {
            "type": "WEBHOOK",
            "url": os.environ.get("NEXT_PUBLIC_APP_URL", "http://localhost:3000")
            + "/api/port/webhook",
        },
    },
    {
        "identifier": "boost_post",
        "title": "Boost Post",
        "icon": "TrendUp",
        "trigger": {
            "type": "self-service",
            "operation": "DAY-2",
            "blueprintIdentifier": "hyperadar_post",
            "userInputs": {"properties": {}},
        },
        "invocationMethod": {
            "type": "WEBHOOK",
            "url": os.environ.get("NEXT_PUBLIC_APP_URL", "http://localhost:3000")
            + "/api/port/webhook",
        },
    },
    {
        "identifier": "mute_agent",
        "title": "Mute Agent",
        "icon": "Mute",
        "trigger": {
            "type": "self-service",
            "operation": "DAY-2",
            "blueprintIdentifier": "hyperadar_agent",
            "userInputs": {"properties": {}},
        },
        "invocationMethod": {
            "type": "WEBHOOK",
            "url": os.environ.get("NEXT_PUBLIC_APP_URL", "http://localhost:3000")
            + "/api/port/webhook",
        },
    },
    {
        "identifier": "retire_agent",
        "title": "Retire Agent",
        "icon": "X",
        "trigger": {
            "type": "self-service",
            "operation": "DELETE",
            "blueprintIdentifier": "hyperadar_agent",
            "userInputs": {"properties": {}},
        },
        "invocationMethod": {
            "type": "WEBHOOK",
            "url": os.environ.get("NEXT_PUBLIC_APP_URL", "http://localhost:3000")
            + "/api/port/webhook",
        },
    },
    {
        "identifier": "generate_digest",
        "title": "Generate Digest",
        "icon": "Calendar",
        "trigger": {
            "type": "self-service",
            "operation": "DAY-2",
            "userInputs": {"properties": {}},
        },
        "invocationMethod": {
            "type": "WEBHOOK",
            "url": os.environ.get("NEXT_PUBLIC_APP_URL", "http://localhost:3000")
            + "/api/port/webhook",
        },
    },
]


# --- Scorecards ---

SCORECARDS = [
    {
        "blueprint": "hyperadar_post",
        "identifier": "hype_quality",
        "title": "Hype Quality",
        "levels": [
            {"title": "Unrated", "color": "blue"},
            {"title": "Silver", "color": "purple"},
            {"title": "Gold", "color": "yellow"},
        ],
        "rules": [
            {
                "identifier": "has_verdict",
                "title": "Has a verdict",
                "level": "Silver",
                "query": {
                    "combinator": "and",
                    "conditions": [
                        {"property": "$verdict", "operator": "isNotEmpty"},
                    ],
                },
            },
            {
                "identifier": "has_blurb",
                "title": "Has a blurb",
                "level": "Gold",
                "query": {
                    "combinator": "and",
                    "conditions": [
                        {"property": "$body", "operator": "isNotEmpty"},
                    ],
                },
            },
        ],
    },
    {
        "blueprint": "hyperadar_agent",
        "identifier": "agent_health",
        "title": "Agent Health",
        "levels": [
            {"title": "Critical", "color": "red"},
            {"title": "Warning", "color": "yellow"},
            {"title": "Healthy", "color": "green"},
        ],
        "rules": [
            {
                "identifier": "has_run",
                "title": "Has run at least once",
                "level": "Warning",
                "query": {
                    "combinator": "and",
                    "conditions": [
                        {"property": "$runCount", "operator": ">=", "value": 1},
                    ],
                },
            },
            {
                "identifier": "is_active",
                "title": "Agent is active",
                "level": "Healthy",
                "query": {
                    "combinator": "and",
                    "conditions": [
                        {"property": "$status", "operator": "=", "value": "active"},
                    ],
                },
            },
        ],
    },
    {
        "blueprint": "hyperadar_project",
        "identifier": "hype_realness",
        "title": "Hype Realness",
        "levels": [
            {"title": "Unverified", "color": "red"},
            {"title": "Emerging", "color": "yellow"},
            {"title": "Confirmed", "color": "green"},
        ],
        "rules": [
            {
                "identifier": "has_verdict",
                "title": "Has a verdict",
                "level": "Emerging",
                "query": {
                    "combinator": "and",
                    "conditions": [
                        {"property": "$hypeVerdict", "operator": "isNotEmpty"},
                    ],
                },
            },
            {
                "identifier": "high_momentum",
                "title": "Momentum >= 70",
                "level": "Confirmed",
                "query": {
                    "combinator": "and",
                    "conditions": [
                        {"property": "$momentumScore", "operator": ">=", "value": 70},
                    ],
                },
            },
        ],
    },
]


def main():
    print("=== Creating Actions ===")
    for action in ACTIONS:
        r = _req("POST", "/actions?version=v2", action)
        if r.get("ok") or "action" in r:
            print(f"  ✓ {action['identifier']}")
        else:
            msg = r.get("message", str(r))[:100]
            if "already exists" in msg.lower():
                print(f"  {action['identifier']} exists ✓")
            else:
                print(f"  ⚠ {action['identifier']}: {msg}")

    print("\n=== Creating Scorecards ===")
    for sc in SCORECARDS:
        bp = sc["blueprint"]
        payload = {k: v for k, v in sc.items() if k != "blueprint"}
        r = _req("POST", f"/blueprints/{bp}/scorecards", payload)
        if r.get("ok") or "scorecard" in r:
            print(f"  ✓ {sc['identifier']} ({bp})")
        else:
            msg = r.get("message", str(r))[:100]
            if "already exists" in msg.lower():
                print(f"  {sc['identifier']} exists ✓")
            else:
                print(f"  ⚠ {sc['identifier']}: {msg}")

    print("\n=== Verifying ===")
    r = _req("GET", "/actions?version=v2")
    actions = r.get("actions", [])
    print(f"Actions: {len(actions)}")
    for a in actions:
        print(f"  - {a.get('identifier')}: {a.get('title')}")

    print("\nSetup complete.")


if __name__ == "__main__":
    main()
