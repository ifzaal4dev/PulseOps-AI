"""Golden vertical slice: the full Chiller-03 loop, headless.

Covers the agent loop, the tools, and the approval gate together.
"""

import json

import pytest
from sqlalchemy import select

from pulseops_api.models import (
    AgentAction,
    CaseMemory,
    Incident,
    OutboxEvent,
    VerificationResult,
    WorkOrder,
)
from pulseops_api.services import approval
from pulseops_api.services.agent_loop import AgentEngine, DuplicateRunError
from pulseops_api.services.tools import ApprovalRequiredError, execute_approved_action


def start_chiller_run(seeded):
    session, ids = seeded
    engine = AgentEngine.start_run(
        session, ids["chiller3_id"], trigger="test: critical vibration incident"
    )
    return session, ids, engine


def test_run_reaches_approval_gate_with_correct_diagnosis(seeded):
    session, ids, engine = start_chiller_run(seeded)
    run = engine.run_until_blocked()

    # State machine persisted through observe → diagnose → plan.
    assert run.status == "awaiting_approval"
    assert run.current_phase == "act"
    assert [s.phase for s in run.steps] == ["observe", "diagnose", "plan"]
    assert all(s.status == "completed" for s in run.steps)
    assert run.tool_calls, "tool calls must be persisted"

    # Diagnose contract: case memory queried FIRST within the diagnose step.
    diagnose_step = next(s for s in run.steps if s.phase == "diagnose")
    diagnose_calls = [tc for tc in run.tool_calls if tc.step_id == diagnose_step.id]
    assert diagnose_calls[0].tool_name == "query_case_memory"

    # All evidence tools were consulted.
    tool_names = {tc.tool_name for tc in diagnose_calls}
    assert {
        "query_case_memory",
        "get_recent_sensor_readings",
        "get_open_work_orders",
        "get_recent_inspections",
        "search_maintenance_manuals",
        "run_failure_prediction",
        "get_feature_importance",
    } <= tool_names

    # Ranked hypotheses with bearing wear on top.
    output = json.loads(diagnose_step.output_json)
    hypotheses = output["hypotheses"]
    assert hypotheses[0]["cause"] == "compressor bearing wear"
    assert hypotheses[0]["confidence"] > 0.4
    assert output["model_source"] == "mock_demo_v0"  # honesty: mock is labeled

    # Plan proposed an action but created NO work order yet.
    action = session.scalars(select(AgentAction).where(AgentAction.run_id == run.id)).one()
    assert action.status == "proposed"
    assert action.work_order_id is None
    agent_orders = session.scalars(select(WorkOrder).where(WorkOrder.source == "agent")).all()
    assert agent_orders == []

    # Incident moved to investigating.
    incident = session.get(Incident, ids["incident_id"])
    assert incident.status == "investigating"


def test_execution_requires_approval(seeded):
    session, ids, engine = start_chiller_run(seeded)
    run = engine.run_until_blocked()
    action = run.actions[0]

    # Direct tool invocation without approval is refused.
    with pytest.raises(ApprovalRequiredError):
        execute_approved_action(session, action.id)

    # Advancing a blocked run does not execute anything.
    for _ in range(3):
        engine.advance()
    session.refresh(action)
    assert action.status == "proposed"
    assert run.status == "awaiting_approval"
    assert session.scalars(select(WorkOrder).where(WorkOrder.source == "agent")).all() == []


def test_approved_run_executes_verifies_and_learns(seeded):
    session, ids, engine = start_chiller_run(seeded)
    run = engine.run_until_blocked()
    action = run.actions[0]
    cases_before = session.scalars(select(CaseMemory)).all()

    approval.approve_action(session, action.id, decided_by="ifzaal")
    run = engine.run_until_blocked()

    assert run.status == "completed"
    assert [s.phase for s in run.steps] == ["observe", "diagnose", "plan", "act", "verify", "learn"]

    # Execution created a real agent work order, after approval only.
    session.refresh(action)
    assert action.status == "executed"
    assert action.decided_by == "ifzaal"
    order = session.get(WorkOrder, action.work_order_id)
    assert order is not None and order.source == "agent"

    # Verification shows meaningful risk reduction.
    vr = session.scalars(
        select(VerificationResult).where(VerificationResult.run_id == run.id)
    ).one()
    assert vr.risk_before > 0.6, "Chiller-03 must start high-risk"
    assert vr.risk_after < 0.3, "risk must drop after simulated maintenance"
    assert vr.passed

    # Learn wrote a postmortem to case memory.
    cases_after = session.scalars(select(CaseMemory)).all()
    new_cases = [c for c in cases_after if c.id not in {x.id for x in cases_before}]
    assert len(new_cases) == 1
    postmortem = new_cases[0]
    assert postmortem.source_run_id == run.id
    assert postmortem.root_cause == "compressor bearing wear"
    assert postmortem.risk_reduction > 0.3

    # Incident mitigated; summary written.
    incident = session.get(Incident, ids["incident_id"])
    assert incident.status == "mitigated"
    assert "bearing wear" in run.summary


def test_rejected_action_never_executes(seeded):
    session, ids, engine = start_chiller_run(seeded)
    run = engine.run_until_blocked()
    action = run.actions[0]

    approval.reject_action(session, action.id, decided_by="ifzaal")
    run = engine.run_until_blocked()

    assert run.status == "completed"
    session.refresh(action)
    assert action.status == "rejected"
    assert action.work_order_id is None
    assert session.scalars(select(WorkOrder).where(WorkOrder.source == "agent")).all() == []
    assert "rejected" in run.summary.lower()


def test_one_active_run_per_incident(seeded):
    session, ids, engine = start_chiller_run(seeded)
    engine.run_until_blocked()  # active (awaiting approval)
    with pytest.raises(DuplicateRunError):
        AgentEngine.start_run(session, ids["chiller3_id"], trigger="duplicate attempt")
    session.rollback()


def test_outbox_records_the_loop_lifecycle(seeded):
    session, ids, engine = start_chiller_run(seeded)
    run = engine.run_until_blocked()
    approval.approve_action(session, run.actions[0].id, decided_by="operator")
    engine.run_until_blocked()

    topics = [e.topic for e in session.scalars(select(OutboxEvent).order_by(OutboxEvent.id)).all()]
    for expected in [
        "agent.run.started",
        "agent.run.awaiting_approval",
        "agent.action.approved",
        "agent.action.executed",
        "agent.run.completed",
    ]:
        assert expected in topics, f"outbox missing {expected}"
    # Outbox rows are unclaimed until a worker processes them.
    assert all(e.processed_at is None for e in session.scalars(select(OutboxEvent)).all())


def test_no_hidden_chain_of_thought_persisted(seeded):
    session, ids, engine = start_chiller_run(seeded)
    run = engine.run_until_blocked()
    for step in run.steps:
        # Reasoning summaries stay concise audit lines, not reasoning dumps.
        assert len(step.reasoning_summary) < 600
