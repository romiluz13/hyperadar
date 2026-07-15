"""Deterministic identity for one agent/project publication per UTC day."""

from datetime import datetime, timezone
from hashlib import sha256


def publication_day(posted_at: datetime | None = None) -> str:
    value = posted_at or datetime.now(timezone.utc)
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc).date().isoformat()


def publication_key(agent_handle: str, project_url: str, day: str) -> str:
    identity = "\0".join((agent_handle, project_url, day))
    return sha256(identity.encode()).hexdigest()
