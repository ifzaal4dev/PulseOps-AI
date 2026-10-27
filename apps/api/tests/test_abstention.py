"""The diagnostician abstains instead of guessing.

Abstention triggers (knowledge.MIN_EVIDENCE_SCORE / TIE_RATIO, empty
signature table): unsupported asset type, insufficient evidence, materially
tied hypotheses. An abstaining run escalates to humans, requests evidence,
and proposes NO work order.
"""

import json
from datetime import timedelta

from sqlalchemy import select

from pulseops_api.models import (
    AgentAction,
    Asset,
    Inspection,
    Room,
    Sensor,
    SensorReading,
    Technician,
    WorkOrder,
)
from pulseops_api.services.agent_loop import AgentEngine
from pulseops_api.timeutil import utcnow


def get_asset(session, tag):
    return session.scalars(select(Asset).where(Asset.tag == tag)).one()


def diagnose_output(run):
    step = next(s for s in run.steps if s.phase == "diagnose")
    return json.loads(step.output_json)


def assert_abstained(session, run, reason_fragment):
    output = diagnose_output(run)
    assert output["abstained"] is True
    assert reason_fragment in output["abstain_reason"]
    assert output["requested_evidence"], "abstention must request concrete evidence"
    assert run.status == "escalated"
    assert "abstained" in run.summary.lower()
    # No fabricated action, no work order.
    assert session.scalars(select(AgentAction).where(AgentAction.run_id == run.id)).all() == []
    assert session.scalars(select(WorkOrder).where(WorkOrder.source == "agent")).all() == []
    # Contract intact: case memory still queried first.
    diagnose_step = next(s for s in run.steps if s.phase == "diagnose")
    dcalls = [tc for tc in run.tool_calls if tc.step_id == diagnose_step.id]
    assert dcalls[0].tool_name == "query_case_memory"


def test_unsupported_asset_type_abstains(seeded):
    session, ids = seeded
    ahu = get_asset(session, "AHU-05")  # no failure-mode signatures for 'ahu'
    run = AgentEngine.start_run(session, ahu.id, trigger="abstention test").run_until_blocked()
    assert_abstained(session, run, "unsupported asset type")


def test_healthy_asset_insufficient_evidence_abstains(seeded):
    session, ids = seeded
    ch1 = get_asset(session, "CH-01")  # healthy chiller: no anomalies, no notes
    run = AgentEngine.start_run(session, ch1.id, trigger="abstention test").run_until_blocked()
    assert_abstained(session, run, "insufficient evidence")


def test_materially_tied_hypotheses_abstain(seeded):
    """A chiller with a pure pressure drop plus notes hinting at BOTH
    refrigerant loss and bearing trouble: no hypothesis clearly wins."""
    session, ids = seeded
    room = session.scalars(select(Room)).first()
    tech = session.scalars(select(Technician)).first()
    now = utcnow()

    ch9 = Asset(
        tag="CH-09",
        name="Chiller-09",
        asset_type="chiller",
        room_id=room.id,
        criticality="high",
        status="degraded",
    )
    session.add(ch9)
    session.flush()
    press = Sensor(asset_id=ch9.id, kind="discharge_pressure", unit="bar", baseline_value=9.0)
    vib = Sensor(asset_id=ch9.id, kind="vibration_rms", unit="mm/s", baseline_value=2.2)
    session.add_all([press, vib])
    session.flush()
    for i in range(96):  # 48h at 30-min cadence: strong pressure drop, calm vibration
        ts = now - timedelta(minutes=30 * (95 - i))
        frac = i / 95
        session.add(SensorReading(sensor_id=press.id, ts=ts, value=round(9.0 - 2.9 * frac, 3)))
        session.add(SensorReading(sensor_id=vib.id, ts=ts, value=2.2))
    session.add(
        Inspection(
            asset_id=ch9.id,
            technician_id=tech.id,
            ts=now - timedelta(days=1),
            summary="Chiller-09 walkdown",
            notes=(
                "Suspect refrigerant charge loss at the purge unit, though a faint bearing "
                "whine was also noted near the drive end. Needs follow-up."
            ),
        )
    )
    session.commit()

    run = AgentEngine.start_run(session, ch9.id, trigger="abstention test").run_until_blocked()
    assert_abstained(session, run, "ambiguous evidence")
    # The two named hypotheses really were close.
    hyps = diagnose_output(run)["hypotheses"]
    assert hyps[1]["score"] >= 0.8 * hyps[0]["score"]


def test_golden_path_still_diagnoses_confidently(seeded):
    """Regression guard: abstention thresholds must not break the golden slice."""
    session, ids = seeded
    run = AgentEngine.start_run(
        session, ids["chiller3_id"], trigger="regression"
    ).run_until_blocked()
    assert run.status == "awaiting_approval"
    output = diagnose_output(run)
    assert output["abstained"] is False
    assert output["hypotheses"][0]["cause"] == "compressor bearing wear"
