"""@github-radar runner — executes one agent run end-to-end.

Usage (from the integration folder):
    uv run python main.py

The run is plain code (``agent.scan``): today's trending AI and dev-tools
repos, ordered by hype score, each written to MongoDB and mirrored to Port.
The shared runner keeps the bookkeeping: doctor, Port agent upsert, pending
post repair and the ``agent_runs`` record.
"""

import asyncio
import os
import sys

from dotenv import load_dotenv

load_dotenv()  # load repo-root .env
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import agent  # noqa: E402
from _shared import mongo  # noqa: E402
from _shared.runner import run_agent  # noqa: E402


async def run_once() -> dict:
    """Run one @github-radar cycle. Returns a summary dict."""
    return await run_agent(
        agent.AGENT_HANDLE,
        agent.AGENT_NAME,
        agent.AGENT_BIO,
        agent.SOURCE_TYPE,
        plain_scan=lambda: agent.scan(mongo._get_db()),
    )


def main():
    summary = asyncio.run(run_once())
    print(f"@github-radar run complete: {summary}")
    if not summary["ok"]:
        sys.exit(1)


if __name__ == "__main__":
    main()
