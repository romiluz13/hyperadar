"""Notes that explain one agent run, such as why it was quiet.

The runner opens a list per run and stores the notes on its ``agent_runs``
record. A source adds to it from here, so it never imports the runner, which
loads ``.env`` and changes ``sys.path`` on import.
"""

from contextvars import ContextVar

current_notes: ContextVar[list[str] | None] = ContextVar(
    "hyperadar_run_notes", default=None
)


def add_run_note(note: str) -> None:
    """Add one explanation to this run's agent_runs ``note``."""
    notes = current_notes.get()
    if notes is not None:
        notes.append(note)
