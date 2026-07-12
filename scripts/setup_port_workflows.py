"""Provision Port Workflows that operate HypeRadar agents."""

import argparse
import json
import os
import urllib.error
import urllib.request

PORT_API = "https://api.port.io/v1"


class PortClient:
    def __init__(self, client_id: str, client_secret: str):
        self.client_id = client_id
        self.client_secret = client_secret
        self.token: str | None = None

    def _get_token(self) -> str:
        if self.token:
            return self.token
        payload = json.dumps(
            {"clientId": self.client_id, "clientSecret": self.client_secret}
        ).encode()
        request = urllib.request.Request(
            f"{PORT_API}/auth/access_token",
            data=payload,
            method="POST",
            headers={"Content-Type": "application/json"},
        )
        try:
            with urllib.request.urlopen(request) as response:
                self.token = json.loads(response.read())["accessToken"]
        except (urllib.error.URLError, KeyError, json.JSONDecodeError) as error:
            raise RuntimeError(f"Port authentication failed: {error}") from error
        return self.token

    def request(
        self,
        method: str,
        path: str,
        payload: dict | None = None,
        headers: dict | None = None,
    ) -> tuple[int, dict]:
        request_headers = {
            "Authorization": f"Bearer {self._get_token()}",
            "Content-Type": "application/json",
            **(headers or {}),
        }
        request = urllib.request.Request(
            f"{PORT_API}{path}",
            data=json.dumps(payload).encode() if payload is not None else None,
            method=method,
            headers=request_headers,
        )
        try:
            with urllib.request.urlopen(request) as response:
                return response.status, json.loads(response.read())
        except urllib.error.HTTPError as error:
            try:
                body = json.loads(error.read())
            except json.JSONDecodeError:
                body = {"message": error.reason}
            return error.code, body


def build_run_agent_workflow(installation_id: str) -> dict:
    return {
        "identifier": "run-hyperadar-agent",
        "title": "Run HypeRadar Agent",
        "icon": "AI",
        "category": "Agent Operations",
        "description": (
            "Refresh one source agent through a governed GitHub run and wait for "
            "its result."
        ),
        "nodes": [
            {
                "identifier": "select-agent",
                "title": "Choose an agent creator",
                "config": {
                    "type": "SELF_SERVE_TRIGGER",
                    "permissions": {},
                    "userInputs": {
                        "properties": {
                            "agent": {
                                "type": "string",
                                "format": "entity",
                                "blueprint": "hyperadar_agent",
                                "title": "Agent creator",
                                "description": (
                                    "Choose which source agent should refresh the radar."
                                ),
                                "dataset": {
                                    "combinator": "and",
                                    "rules": [
                                        {
                                            "property": "status",
                                            "operator": "=",
                                            "value": "active",
                                        }
                                    ],
                                },
                            }
                        },
                        "required": ["agent"],
                        "order": ["agent"],
                    },
                    "contexts": [{"on": "ENTITY", "userInput": "agent"}],
                    "published": True,
                },
            },
            {
                "identifier": "run-agent",
                "title": "Run agent and publish signals",
                "config": {
                    "type": "INTEGRATION_ACTION",
                    "installationId": installation_id,
                    "integrationProvider": "github-ocean",
                    "integrationInvocationType": "dispatch_workflow",
                    "integrationActionExecutionProperties": {
                        "org": "romiluz13",
                        "repo": "hyperadar",
                        "workflow": "run-hyperadar-agent.yml",
                        "workflowInputs": {
                            "agent": "{{ .outputs.trigger.agent }}"
                        },
                        "reportWorkflowStatus": True,
                    },
                    "onFailure": "terminate",
                },
                "links": [
                    {
                        "url": "https://github.com/romiluz13/hyperadar/actions",
                        "title": "Open GitHub run",
                    }
                ],
                "verbose": True,
            },
        ],
        "connections": [
            {
                "sourceIdentifier": "select-agent",
                "targetIdentifier": "run-agent",
            }
        ],
    }


def provision_workflow(client, workflow: dict) -> str:
    identifier = workflow["identifier"]
    status, current = client.request("GET", f"/workflows/{identifier}")
    if status == 200:
        workflow_details = current.get("workflow", {})
        version = workflow_details.get("workflowVersionIdentifier")
        if not version:
            raise RuntimeError("Port did not return the current workflow version")
        status, response = client.request(
            "PUT",
            f"/workflows/{identifier}",
            workflow,
            {"If-Match": version},
        )
        if status != 200:
            message = response.get("message", "unknown Port API error")
            raise RuntimeError(f"Workflow update failed ({status}): {message}")
        return "updated"
    if status != 404:
        raise RuntimeError(f"Unexpected workflow lookup status: {status}")

    status, response = client.request("POST", "/workflows", workflow)
    if status != 201:
        message = response.get("message", "unknown Port API error")
        raise RuntimeError(f"Workflow creation failed ({status}): {message}")
    return "created"


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--installation-id", required=True)
    args = parser.parse_args()

    workflow = build_run_agent_workflow(args.installation_id)
    if args.dry_run:
        print(json.dumps(workflow, indent=2))
        return

    client_id = os.environ.get("PORT_CLIENT_ID")
    client_secret = os.environ.get("PORT_CLIENT_SECRET")
    if not client_id or not client_secret:
        parser.error(
            "PORT_CLIENT_ID and PORT_CLIENT_SECRET are required. Source .env first."
        )

    outcome = provision_workflow(PortClient(client_id, client_secret), workflow)
    print(f"{workflow['title']}: {outcome}")


if __name__ == "__main__":
    main()
