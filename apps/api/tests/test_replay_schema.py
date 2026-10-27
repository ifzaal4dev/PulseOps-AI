"""Replay/prediction persistence contracts and the
honestly-mapped MetroPT APU asset."""

import pytest
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError

from pulseops_api.models import Asset, ModelPrediction, ReplayRun, SensorReading
from pulseops_api.services.agent_loop import AgentEngine
from pulseops_api.timeutil import utcnow

MAPPED_ANALOG_KINDS = {
    "TP2",
    "TP3",
    "H1",
    "DV_pressure",
    "Reservoirs",
    "Oil_temperature",
    "Motor_current",
}
DIGITAL_CHANNELS = {
    "COMP",
    "DV_eletric",
    "Towers",
    "MPG",
    "LPS",
    "Pressure_switch",
    "Oil_level",
    "Caudal_impulses",
}


def test_apu_asset_is_honestly_mapped(seeded):
    session, ids = seeded
    apu = session.get(Asset, ids["apu_id"])
    assert apu.asset_type == "air_compressor"
    assert "MetroPT" in apu.name  # clearly labeled as the replay asset

    kinds = {s.kind for s in apu.sensors}
    assert kinds == MAPPED_ANALOG_KINDS, "exactly the seven analog MetroPT channels"
    assert not (kinds & DIGITAL_CHANNELS), (
        "digital channels are operating-state inputs, not sensors"
    )

    # Telemetry arrives only via replay — nothing seeded.
    sensor_ids = [s.id for s in apu.sensors]
    n = session.scalars(select(SensorReading).where(SensorReading.sensor_id.in_(sensor_ids))).all()
    assert n == []


def test_apu_diagnosis_abstains_without_telemetry(seeded):
    """air_compressor now HAS failure-mode signatures, but with zero replayed
    telemetry the evidence is insufficient: the agent must abstain
    (escalate), never fabricate a work order."""
    session, ids = seeded
    run = AgentEngine.start_run(session, ids["apu_id"], trigger="w3 slice test").run_until_blocked()
    assert run.status == "escalated"
    assert "insufficient evidence" in run.summary


def test_prediction_idempotency_constraint(seeded):
    session, ids = seeded
    window_end = utcnow()
    runs = [
        ReplayRun(
            dataset_name="metropt3",
            dataset_checksum="fixture",
            asset_id=ids["apu_id"],
            source_start=window_end,
            source_end=window_end,
            speed=120.0,
            status="ready",
            model_source="metropt3_iforest_v1",
            feature_version="metropt3_fw_v1",
        )
        for _ in range(2)
    ]
    session.add_all(runs)
    session.commit()
    row = dict(
        replay_run_id=runs[0].id,
        asset_id=ids["apu_id"],
        window_end=window_end,
        risk=0.42,
        threshold=0.491,
        alert=False,
        shadow=True,
        model_source="metropt3_iforest_v1",
        feature_version="metropt3_fw_v1",
        operating_mode="idle",
    )
    session.add(ModelPrediction(**row))
    session.commit()

    session.add(ModelPrediction(**row))  # replayed window: same asset/model/window_end
    with pytest.raises(IntegrityError):
        session.commit()
    session.rollback()

    # The same source window in another replay is an independent observation.
    session.add(ModelPrediction(**{**row, "replay_run_id": runs[1].id}))
    session.commit()

    # A different model for the same window is a distinct row (versioned inference).
    session.add(ModelPrediction(**{**row, "model_source": "mock_demo_v0"}))
    session.commit()
    assert len(session.scalars(select(ModelPrediction)).all()) == 3


def test_prediction_defaults_to_shadow(seeded):
    """Shadow-by-default matches the trigger_policy.json decision: predictions
    are observational unless a readiness-approved policy flips them."""
    session, ids = seeded
    session.add(
        ModelPrediction(
            asset_id=ids["apu_id"],
            window_end=utcnow(),
            risk=0.9,
            threshold=0.491,
            alert=True,
            model_source="metropt3_iforest_v1",
        )
    )
    session.commit()
    p = session.scalars(select(ModelPrediction)).one()
    assert p.shadow is True
    assert p.predicted_at is not None  # ingestion time recorded separately from window_end


def test_replay_run_state_persists_across_instances(seeded):
    """The DB row is the source of truth: a fresh read of the run resumes at
    the persisted cursor (three clocks: source/sim/ingestion)."""
    session, ids = seeded
    start, end = utcnow(), utcnow()
    run = ReplayRun(
        dataset_name="metropt3",
        dataset_checksum="aab991a9",
        asset_id=ids["apu_id"],
        source_start=start,
        source_end=end,
        speed=120.0,
        status="ready",
        model_source="metropt3_iforest_v1",
        feature_version="metropt3_fw_v1",
    )
    session.add(run)
    session.commit()
    run_id = run.id

    run.status = "running"
    run.cursor = start
    run.sim_time = start
    session.commit()
    session.expunge_all()  # simulate a fresh service instance / process restart

    resumed = session.get(ReplayRun, run_id)
    assert resumed.status == "running"
    assert resumed.cursor == start
    assert resumed.sim_time == start
    assert resumed.speed == 120.0
    assert resumed.dataset_name == "metropt3"
