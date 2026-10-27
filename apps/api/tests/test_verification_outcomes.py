"""Verification outcomes control the workflow.

The environment (services/environment.py) decides how the asset responds to
an executed action; verification observes and classifies. These tests prove
that failed / inconclusive / adverse outcomes cannot claim success, cannot
mitigate the incident, and never execute anything without a fresh approval.
"""

import json
from datetime import datetime, timedelta

import pytest
from sqlalchemy import select

from pulseops_api.models import (
    CaseMemory,
    Incident,
    Sensor,
    SensorReading,
    VerificationResult,
    WorkOrder,
)
from pulseops_api.services import approval, environment
from pulseops_api.services.agent_loop import AgentEngine
from pulseops_api.services.tools import ToolError, verify_outcome
from pulseops_api.timeutil import utcnow


def run_to_gate(seeded):
    session, ids = seeded
    engine = AgentEngine.start_run(session, ids["chiller3_id"], trigger="outcome test")
    run = engine.run_until_blocked()
    assert run.status == "awaiting_approval"
    return session, ids, engine, run


def latest_verification(session, run_id):
    return session.scalars(
        select(VerificationResult)
        .where(VerificationResult.run_id == run_id)
        .order_by(VerificationResult.id.desc())
    ).first()


def success_memories(session, run_id):
    return session.scalars(
        select(CaseMemory).where(
            CaseMemory.source_run_id == run_id, CaseMemory.validation_status == "validated_success"
        )
    ).all()


def failure_memories(session, run_id):
    return session.scalars(
        select(CaseMemory).where(
            CaseMemory.source_run_id == run_id, CaseMemory.validation_status == "validated_failure"
        )
    ).all()


def send_correlated_environment_response(session, run, asset_id):
    """Resume the demo simulator using the executed action's correlation id."""
    payload = json.loads(run.actions[0].payload_json)
    replay_run_id = payload.get("replay_run_id")
    environment.apply_maintenance_response(
        session,
        asset_id,
        observation_as_of=datetime.fromisoformat(payload["executed_anchor"]),
        observation_source_prefix=f"replay:{replay_run_id}:" if replay_run_id else None,
        response_source_prefix=payload["verification_source_prefix"],
    )


def test_verification_refuses_cross_model_risk_delta(seeded):
    session, ids, engine, run = run_to_gate(seeded)
    approval.approve_action(session, run.actions[0].id, decided_by="operator")
    engine.advance()  # execute only; Verify has not run yet
    action = run.actions[0]
    payload = json.loads(action.payload_json)
    payload["risk_source"] = "different_model"
    action.payload_json = json.dumps(payload)
    session.commit()

    with pytest.raises(ToolError, match="verification model mismatch"):
        verify_outcome(session, run.id)


def test_failed_outcome_replans_and_requires_new_approval(seeded):
    session, ids, engine, run = run_to_gate(seeded)
    environment.set_outcome_profile(ids["chiller3_id"], "failure")

    approval.approve_action(session, run.actions[0].id, decided_by="operator")
    run = engine.run_until_blocked()

    # Verification failed → truthfully persisted, no mitigation, no success memory.
    vr = latest_verification(session, run.id)
    assert vr.outcome == "failed" and vr.passed is False
    incident = session.get(Incident, ids["incident_id"])
    assert incident.status != "mitigated"
    assert success_memories(session, run.id) == []
    negatives = failure_memories(session, run.id)
    assert len(negatives) == 1
    assert "FAILED" in negatives[0].outcome

    # The run replanned within its attempt budget and is blocked on a NEW approval.
    assert run.status == "awaiting_approval"
    actions = sorted(run.actions, key=lambda a: a.id)
    assert [a.status for a in actions] == ["executed", "proposed"]
    assert run.attempts_used == 1

    # Second attempt with a cooperative environment succeeds end-to-end.
    environment.set_outcome_profile(ids["chiller3_id"], "success")
    approval.approve_action(session, actions[1].id, decided_by="operator")
    run = engine.run_until_blocked()
    assert run.status == "completed"
    assert run.attempts_used == 2
    assert latest_verification(session, run.id).outcome == "passed"
    session.refresh(incident)
    assert incident.status == "mitigated"
    assert len(success_memories(session, run.id)) == 1


def test_exhausted_attempt_budget_escalates(seeded):
    session, ids, engine, run = run_to_gate(seeded)
    environment.set_outcome_profile(ids["chiller3_id"], "failure")

    approval.approve_action(session, run.actions[0].id, decided_by="operator")
    run = engine.run_until_blocked()
    assert run.status == "awaiting_approval"  # replanned once

    actions = sorted(run.actions, key=lambda a: a.id)
    approval.approve_action(session, actions[1].id, decided_by="operator")
    run = engine.run_until_blocked()

    assert run.status == "escalated"
    assert run.attempts_used == 2
    assert "escalat" in run.summary.lower()
    incident = session.get(Incident, ids["incident_id"])
    assert incident.status != "mitigated"
    assert success_memories(session, run.id) == []
    assert len(failure_memories(session, run.id)) == 2
    # Escalated runs refuse to advance further.
    engine.advance()
    assert run.status == "escalated"


def test_adverse_outcome_escalates_and_raises_severity(seeded):
    session, ids, engine, run = run_to_gate(seeded)
    environment.set_outcome_profile(ids["chiller3_id"], "adverse")

    approval.approve_action(session, run.actions[0].id, decided_by="operator")
    run = engine.run_until_blocked()

    vr = latest_verification(session, run.id)
    assert vr.outcome == "adverse"
    assert vr.risk_after > vr.risk_before

    assert run.status == "escalated"
    incident = session.get(Incident, ids["incident_id"])
    assert incident.status == "investigating"  # open, NOT mitigated
    assert incident.severity == "critical"

    # No second action was auto-proposed or auto-executed after the adverse result.
    assert len(run.actions) == 1
    assert success_memories(session, run.id) == []
    negatives = failure_memories(session, run.id)
    assert len(negatives) == 1 and "ADVERSE" in negatives[0].outcome


def test_no_telemetry_is_inconclusive_and_pauses(seeded):
    session, ids, engine, run = run_to_gate(seeded)
    environment.set_outcome_profile(ids["chiller3_id"], "none")

    approval.approve_action(session, run.actions[0].id, decided_by="operator")
    run = engine.run_until_blocked()

    vr = latest_verification(session, run.id)
    assert vr.outcome == "inconclusive"
    assert run.status == "paused"
    assert "waiting" in run.summary.lower()
    incident = session.get(Incident, ids["incident_id"])
    assert incident.status != "mitigated"
    assert success_memories(session, run.id) == []

    # Later, telemetry arrives (healthy recovery); resuming re-verifies and completes.
    environment.set_outcome_profile(ids["chiller3_id"], "success")
    send_correlated_environment_response(session, run, ids["chiller3_id"])
    session.commit()
    engine.advance()  # explicit resume: re-runs verification against new telemetry
    run = engine.run_until_blocked()
    assert run.status == "completed"
    assert latest_verification(session, run.id).outcome == "passed"


def test_sparse_telemetry_is_inconclusive(seeded):
    session, ids, engine, run = run_to_gate(seeded)
    environment.set_outcome_profile(ids["chiller3_id"], "sparse")

    approval.approve_action(session, run.actions[0].id, decided_by="operator")
    run = engine.run_until_blocked()

    vr = latest_verification(session, run.id)
    assert vr.outcome == "inconclusive"
    assert "reading" in vr.outcome_reason
    assert run.status == "paused"


def test_partial_recovery_is_not_success(seeded):
    session, ids, engine, run = run_to_gate(seeded)
    environment.set_outcome_profile(ids["chiller3_id"], "partial")

    approval.approve_action(session, run.actions[0].id, decided_by="operator")
    run = engine.run_until_blocked()

    vr = latest_verification(session, run.id)
    assert vr.outcome == "failed"
    assert vr.risk_after < vr.risk_before  # risk DID drop...
    assert "partial" in vr.outcome_reason  # ...but not enough to claim resolution
    incident = session.get(Incident, ids["incident_id"])
    assert incident.status != "mitigated"
    assert success_memories(session, run.id) == []


def test_uneven_sensor_arrival_stays_inconclusive(seeded):
    """Coverage policy: post-action sufficiency is the MINIMUM across all of
    the asset's sensors. One chatty sensor flooding readings while the others
    stay silent must NOT satisfy the per-sensor requirement (this was a real
    bug: the old max() logic would have judged the outcome on one sensor)."""
    session, ids, engine, run = run_to_gate(seeded)
    environment.set_outcome_profile(ids["chiller3_id"], "none")  # environment sends nothing

    approval.approve_action(session, run.actions[0].id, decided_by="operator")
    run = engine.run_until_blocked()
    assert run.status == "paused"  # inconclusive: zero post-action telemetry

    # One sensor now floods 12 healthy readings; the other three stay silent.
    vib = session.scalars(
        select(Sensor).where(Sensor.asset_id == ids["chiller3_id"], Sensor.kind == "vibration_rms")
    ).one()
    now = utcnow()
    response_prefix = json.loads(run.actions[0].payload_json)["verification_source_prefix"]
    for i in range(12):
        session.add(
            SensorReading(
                sensor_id=vib.id,
                ts=now + timedelta(minutes=30 * (i + 1)),
                value=vib.baseline_value,
                source_id=f"{response_prefix}manual:{i}",
            )
        )
    session.commit()

    engine.advance()  # resume → re-verify
    vr = latest_verification(session, run.id)
    assert vr.outcome == "inconclusive", "min-across-sensors must gate the judgment"
    assert run.status == "paused"

    # Per-sensor counts are persisted for audit and show the imbalance.
    details = json.loads(vr.details_json)
    counts = details["per_sensor_post_readings"]
    assert counts["vibration_rms"] == 12
    assert counts["discharge_pressure"] == 0
    assert details["post_action_readings"] == 0
    assert "min across required sensors" in details["coverage_policy"]

    # Once ALL sensors report recovery, verification can finally judge.
    environment.set_outcome_profile(ids["chiller3_id"], "success")
    send_correlated_environment_response(session, run, ids["chiller3_id"])
    session.commit()
    engine.advance()
    run = engine.run_until_blocked()
    assert run.status == "completed"
    assert latest_verification(session, run.id).outcome == "passed"


def test_agent_orders_exist_only_for_approved_attempts(seeded):
    """Every agent work order must trace to an action approved BEFORE execution."""
    session, ids, engine, run = run_to_gate(seeded)
    environment.set_outcome_profile(ids["chiller3_id"], "failure")
    approval.approve_action(session, run.actions[0].id, decided_by="operator")
    run = engine.run_until_blocked()  # failed → replan → 2nd proposal pending

    orders = session.scalars(select(WorkOrder).where(WorkOrder.source == "agent")).all()
    assert len(orders) == 1  # only the approved first attempt
    executed = [a for a in run.actions if a.status == "executed"]
    for a in executed:
        assert a.decided_at is not None and a.executed_at is not None
        assert a.decided_at <= a.executed_at
