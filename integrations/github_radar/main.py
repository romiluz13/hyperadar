"""@github-radar runner — executes one agent run end-to-end.

Usage (from the integration folder):
    uv run python main.py

The agent fetches trending GitHub AI repos, scores them (Deep Agents + Grove),
and for each breakout writes signals + project + post to MongoDB and upserts the
agent/project/post entities in Port. MongoDBSaver checkpoints the run.

T2 tracer bullet: proves the whole spine
(Port catalog + Deep Agents + Grove + MongoDB memory + feed data) with one agent.
"""

import asyncio
import os
import sys

from dotenv import load_dotenv

load_dotenv()  # load repo-root .env
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from _shared.runner import run_agent  # noqa: E402
from agent import (  # noqa: E402
    AGENT_BIO,
    AGENT_HANDLE,
    AGENT_NAME,
    SOURCE_TYPE,
    build_agent,
)


async def run_once() -> dict:
    """Run one @github-radar cycle. Returns a summary dict."""
    return await run_agent(
        AGENT_HANDLE, AGENT_NAME, AGENT_BIO, SOURCE_TYPE, build_agent
    )


def main():
    summary = asyncio.run(run_once())
    print(f"@github-radar run complete: {summary}")
    if not summary["ok"]:
        sys.exit(1)


if __name__ == "__main__":
    main()
