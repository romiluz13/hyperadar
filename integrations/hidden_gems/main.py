"""@hidden-gems runner."""

import asyncio
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from _shared.runner import run_agent
from agent import AGENT_BIO, AGENT_HANDLE, AGENT_NAME, SOURCE_TYPE, build_agent


def main():
    summary = asyncio.run(
        run_agent(AGENT_HANDLE, AGENT_NAME, AGENT_BIO, SOURCE_TYPE, build_agent)
    )
    print(f"@hidden-gems run complete: {summary}")
    if not summary["ok"]:
        sys.exit(1)


if __name__ == "__main__":
    main()
