"""The operator-visible HypeRadar agent identities."""

import json
from pathlib import Path

_CATALOG_PATH = Path(__file__).parents[2] / "agent_catalog.json"
AGENT_CATALOG = tuple(json.loads(_CATALOG_PATH.read_text()))


def agent_identity(handle: str) -> dict:
    return next(agent for agent in AGENT_CATALOG if agent["handle"] == handle)
