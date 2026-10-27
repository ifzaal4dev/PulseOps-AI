"""Where a sensor reading came from, encoded in its source_id.

Replayed telemetry and the simulated maintenance response both write
readings with a source_id prefix. Queries filter on these prefixes so one
replay run, or one executed action, never picks up another's data.
"""

from datetime import datetime


def replay_prefix(replay_run_id: int) -> str:
    """Prefix shared by every reading a replay run ingested."""
    return f"replay:{replay_run_id}:"


def replay_reading_id(replay_run_id: int, source_ts: datetime) -> str:
    """Idempotency key for one replayed source timestamp."""
    return f"{replay_prefix(replay_run_id)}{source_ts.isoformat()}"


def action_response_prefix(action_id: int) -> str:
    """Prefix for readings the simulated environment wrote after an action."""
    return f"environment:action:{action_id}:"
