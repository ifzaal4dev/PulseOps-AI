"""Transactional outbox helper.

emit_event writes an OutboxEvent row in the CALLER's transaction, so the
event exists if and only if the state change it describes committed.
Nothing consumes the outbox yet; it's a durable audit stream, and a future
worker can claim rows by setting processed_at.
"""

from __future__ import annotations

import json

from sqlalchemy.orm import Session

from ..models import OutboxEvent


def emit_event(session: Session, topic: str, payload: dict) -> OutboxEvent:
    event = OutboxEvent(topic=topic, payload_json=json.dumps(payload, default=str))
    session.add(event)
    return event
