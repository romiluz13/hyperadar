"""Report a completed GitHub agent job back to its Port workflow node."""

import argparse
import os
import re

from setup_port_workflows import PortClient

JOB_RESULTS = {
    "success": "SUCCESS",
    "failure": "FAILED",
    "cancelled": "CANCELLED",
    "skipped": "CANCELLED",
}


def report_workflow_node(
    client,
    node_run_id: str,
    job_result: str,
    github_run_url: str,
) -> str:
    if not re.fullmatch(r"wfnr_[A-Za-z0-9]+", node_run_id):
        raise ValueError("Invalid Port workflow node run identifier")
    if job_result not in JOB_RESULTS:
        raise ValueError(f"Unknown GitHub job result: {job_result}")

    port_result = JOB_RESULTS[job_result]
    status, response = client.request(
        "PATCH",
        f"/workflows/nodes/runs/{node_run_id}",
        {
            "status": "COMPLETED",
            "result": port_result,
            "output": {"githubRunUrl": github_run_url},
            "links": [github_run_url],
        },
    )
    if status != 200:
        message = response.get("message", "unknown Port API error")
        raise RuntimeError(f"Port workflow node update failed ({status}): {message}")
    return port_result


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--node-run-id", required=True)
    parser.add_argument("--job-result", required=True, choices=JOB_RESULTS)
    parser.add_argument("--github-run-url", required=True)
    args = parser.parse_args()

    client_id = os.environ.get("PORT_CLIENT_ID")
    client_secret = os.environ.get("PORT_CLIENT_SECRET")
    if not client_id or not client_secret:
        parser.error("PORT_CLIENT_ID and PORT_CLIENT_SECRET are required")

    result = report_workflow_node(
        PortClient(client_id, client_secret),
        args.node_run_id,
        args.job_result,
        args.github_run_url,
    )
    print(f"Reported {result} to Port node {args.node_run_id}")


if __name__ == "__main__":
    main()
