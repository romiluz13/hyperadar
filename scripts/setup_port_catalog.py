"""Provision the three Port blueprints used by HypeRadar's live catalog twin."""

import argparse
import json
import os
import sys
from pathlib import Path

from setup_port_workflows import PortClient

sys.path.insert(0, str(Path(__file__).parents[1] / "integrations"))

from _shared.agent_catalog import AGENT_CATALOG

VERDICTS = ["hype looks real", "inflated", "emerging", "cooling"]
RETIRED_ACTIONS = [
    "run_agent_now",
    "track_project",
    "boost_post",
    "mute_agent",
    "retire_agent",
    "generate_digest",
]
RETIRED_SCORECARDS = [
    ("hyperadar_post", "hype_quality"),
    ("hyperadar_agent", "agent_health"),
    ("hyperadar_project", "hype_realness"),
]


def _has_real_brightdata_key(value: str | None) -> bool:
    if not value or not value.strip():
        return False
    normalized = value.strip().lower()
    return not any(
        marker in normalized
        for marker in ("replace", "your_", "placeholder", "example")
    )


def build_agent_entities(brightdata_api_key: str | None) -> list[dict]:
    reddit_enabled = _has_real_brightdata_key(brightdata_api_key)
    return [
        {
            "identifier": agent["handle"].removeprefix("@"),
            "title": agent["name"],
            "properties": {
                "handle": agent["handle"],
                "name": agent["name"],
                "bio": agent["bio"],
                "sourceType": agent["source_type"],
                "status": (
                    "muted"
                    if agent["handle"] == "@reddit-pulse" and not reddit_enabled
                    else "active"
                ),
            },
        }
        for agent in AGENT_CATALOG
    ]


def build_catalog_blueprints() -> list[dict]:
    return [
        {
            "identifier": "hyperadar_agent",
            "title": "HypeRadar Agent",
            "icon": "Robot",
            "schema": {
                "properties": {
                    "handle": {"title": "Handle", "type": "string"},
                    "name": {"title": "Name", "type": "string"},
                    "bio": {"title": "Bio", "type": "string", "format": "markdown"},
                    "sourceType": {
                        "title": "Source type",
                        "type": "string",
                        "enum": [
                            "github",
                            "reddit",
                            "youtube",
                            "web",
                            "aggregator",
                        ],
                    },
                    "status": {
                        "title": "Status",
                        "type": "string",
                        "enum": ["active", "muted", "retired"],
                    },
                    "lastRunAt": {
                        "title": "Last successful run",
                        "type": "string",
                        "format": "date-time",
                    },
                    "runCount": {
                        "title": "Run count",
                        "type": "number",
                        "default": 0,
                    },
                    "successRate": {
                        "title": "Success rate",
                        "description": "Observed success percentage from 0 to 100",
                        "type": "number",
                        "minimum": 0,
                        "maximum": 100,
                    },
                },
                "required": [],
            },
            "relations": {},
        },
        {
            "identifier": "hyperadar_project",
            "title": "Trending Project",
            "icon": "Star",
            "schema": {
                "properties": {
                    "title": {"title": "Title", "type": "string"},
                    "url": {"title": "Source URL", "type": "string", "format": "url"},
                    "kind": {
                        "title": "Kind",
                        "type": "string",
                        "enum": ["repo", "video", "thread", "site"],
                    },
                    "description": {
                        "title": "Description",
                        "type": "string",
                        "format": "markdown",
                    },
                    "topics": {
                        "title": "Topics",
                        "type": "array",
                        "items": {"type": "string"},
                    },
                    "momentumScore": {
                        "title": "Momentum score",
                        "type": "number",
                        "minimum": 0,
                        "maximum": 100,
                    },
                    "hypeVerdict": {
                        "title": "Hype verdict",
                        "type": "string",
                        "enum": VERDICTS,
                    },
                    "firstSeenAt": {
                        "title": "First seen",
                        "type": "string",
                        "format": "date-time",
                    },
                    "lastSeenAt": {
                        "title": "Last seen",
                        "type": "string",
                        "format": "date-time",
                    },
                },
                "required": [],
            },
            "relations": {},
        },
        {
            "identifier": "hyperadar_post",
            "title": "HypeRadar Post",
            "icon": "Message",
            "schema": {
                "properties": {
                    "body": {
                        "title": "Claim",
                        "type": "string",
                        "format": "markdown",
                    },
                    "verdict": {
                        "title": "Verdict",
                        "type": "string",
                        "enum": VERDICTS,
                    },
                    "rankScore": {
                        "title": "Rank score",
                        "type": "number",
                        "minimum": 0,
                        "maximum": 100,
                    },
                    "postedAt": {
                        "title": "Posted at",
                        "type": "string",
                        "format": "date-time",
                    },
                    "signalsSummary": {
                        "title": "Signals summary",
                        "type": "string",
                    },
                    "likeCount": {
                        "title": "Likes at catalog sync",
                        "type": "number",
                        "default": 0,
                    },
                    "commentCount": {
                        "title": "Comments at catalog sync",
                        "type": "number",
                        "default": 0,
                    },
                    "shareCount": {
                        "title": "Shares at catalog sync",
                        "type": "number",
                        "default": 0,
                    },
                },
                "required": [],
            },
            "relations": {
                "agent": {
                    "title": "Agent creator",
                    "target": "hyperadar_agent",
                    "required": True,
                    "many": False,
                },
                "project": {
                    "title": "Project",
                    "target": "hyperadar_project",
                    "required": True,
                    "many": False,
                },
            },
        },
    ]


def provision_blueprint(client, blueprint: dict) -> str:
    identifier = blueprint["identifier"]
    status, response = client.request("GET", f"/blueprints/{identifier}")
    if status == 200:
        status, response = client.request("PUT", f"/blueprints/{identifier}", blueprint)
        if status != 200:
            raise RuntimeError(
                f"Blueprint {identifier} update failed ({status}): "
                f"{response.get('message', 'unknown Port API error')}"
            )
        return "updated"
    if status != 404:
        raise RuntimeError(f"Unexpected {identifier} lookup status: {status}")

    status, response = client.request("POST", "/blueprints", blueprint)
    if status != 201:
        raise RuntimeError(
            f"Blueprint {identifier} creation failed ({status}): "
            f"{response.get('message', 'unknown Port API error')}"
        )
    return "created"


def provision_entity(client, blueprint: str, entity: dict) -> str:
    identifier = entity["identifier"]
    patch_entity = entity
    if blueprint == "hyperadar_agent":
        generated_status = entity.get("properties", {}).get("status")
        patch_entity = {
            **entity,
            "properties": {
                key: value
                for key, value in entity.get("properties", {}).items()
                if key != "status" or generated_status == "muted"
            },
        }
    status, response = client.request(
        "PATCH", f"/blueprints/{blueprint}/entities/{identifier}", patch_entity
    )
    if status == 200:
        return "updated"
    if status != 404:
        raise RuntimeError(
            f"Entity {identifier} update failed ({status}): "
            f"{response.get('message', 'unknown Port API error')}"
        )
    status, response = client.request(
        "POST", f"/blueprints/{blueprint}/entities", entity
    )
    if status != 201:
        raise RuntimeError(
            f"Entity {identifier} creation failed ({status}): "
            f"{response.get('message', 'unknown Port API error')}"
        )
    return "created"


def retire_legacy_port_assets(client) -> dict[str, int]:
    """Delete controls whose webhook backend has been retired."""
    deleted_actions = 0
    for identifier in RETIRED_ACTIONS:
        status, response = client.request("DELETE", f"/actions/{identifier}")
        if status in {200, 204}:
            deleted_actions += 1
        elif status != 404:
            raise RuntimeError(
                f"Retired action {identifier} cleanup failed ({status}): "
                f"{response.get('message', 'unknown Port API error')}"
            )

    deleted_scorecards = 0
    for blueprint, identifier in RETIRED_SCORECARDS:
        status, response = client.request(
            "DELETE", f"/blueprints/{blueprint}/scorecards/{identifier}"
        )
        if status in {200, 204}:
            deleted_scorecards += 1
        elif status != 404:
            raise RuntimeError(
                f"Retired scorecard {identifier} cleanup failed ({status}): "
                f"{response.get('message', 'unknown Port API error')}"
            )
    return {"actions": deleted_actions, "scorecards": deleted_scorecards}


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()
    blueprints = build_catalog_blueprints()
    agents = build_agent_entities(os.environ.get("BRIGHTDATA_API_KEY"))
    if args.dry_run:
        print(json.dumps({"blueprints": blueprints, "agents": agents}, indent=2))
        return

    client_id = os.environ.get("PORT_CLIENT_ID")
    client_secret = os.environ.get("PORT_CLIENT_SECRET")
    if not client_id or not client_secret:
        parser.error(
            "PORT_CLIENT_ID and PORT_CLIENT_SECRET are required. Source .env first."
        )
    client = PortClient(client_id, client_secret)
    retired = retire_legacy_port_assets(client)
    print(
        "Retired Port assets: "
        f"{retired['actions']} actions, {retired['scorecards']} scorecards removed"
    )
    for blueprint in blueprints:
        outcome = provision_blueprint(client, blueprint)
        print(f"{blueprint['title']}: {outcome}")
    for agent in agents:
        outcome = provision_entity(client, "hyperadar_agent", agent)
        print(f"{agent['title']}: {outcome}")


if __name__ == "__main__":
    main()
