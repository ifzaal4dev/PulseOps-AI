"""Timezone-aware UTC timestamp helper.

All datetimes in PulseOps are aware UTC. Columns use db.TZDateTime, which
maps to TIMESTAMPTZ semantics on PostgreSQL and a UTC-string convention on
SQLite, and always returns aware UTC values.
"""

from datetime import UTC, datetime


def utcnow() -> datetime:
    return datetime.now(UTC)
