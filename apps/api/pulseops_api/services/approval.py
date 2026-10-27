"""Human approval gate.

Only these functions change an action's status from 'proposed'. They are
called exclusively from the HTTP routes (i.e., by a human through the UI or
API client) — the agent engine never calls them. After approval the run flips
back to 'running' so the next advance() executes the action; after rejection
the run completes without touching the facility.
"""

from __future__ import annotations

from sqlalchemy.orm import Session

from ..models import AgentAction, AgentRun
from ..timeutil import utcnow
from .events import emit_event


class ApprovalError(Exception):
    pass


def _get_action(session: Session, action_id: int) -> AgentAction:
    action = session.get(AgentAction, action_id)
    if action is None:
        raise ApprovalError(f"action {action_id} not found")
    if action.status != "proposed":
        raise ApprovalError(
            f"action {action_id} is '{action.status}', only 'proposed' actions can be decided"
        )
    return action


def approve_action(session: Session, action_id: int, decided_by: str = "operator") -> AgentAction:
    action = _get_action(session, action_id)
    action.status = "approved"
    action.decided_at = utcnow()
    action.decided_by = decided_by
    run = session.get(AgentRun, action.run_id)
    if run and run.status == "awaiting_approval":
        run.status = "running"  # next advance() will execute in the Act phase
    emit_event(
        session,
        "agent.action.approved",
        {"action_id": action.id, "run_id": action.run_id, "decided_by": decided_by},
    )
    session.commit()
    return action


def reject_action(session: Session, action_id: int, decided_by: str = "operator") -> AgentAction:
    action = _get_action(session, action_id)
    action.status = "rejected"
    action.decided_at = utcnow()
    action.decided_by = decided_by
    run = session.get(AgentRun, action.run_id)
    if run and run.status == "awaiting_approval":
        run.status = "running"  # next advance() records the rejection and completes
    emit_event(
        session,
        "agent.action.rejected",
        {"action_id": action.id, "run_id": action.run_id, "decided_by": decided_by},
    )
    session.commit()
    return action
