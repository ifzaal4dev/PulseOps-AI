"""Online inference, pacing, shadow trigger, and promotion.

A disposable artifact is trained on the shared synthetic fixture
(pulseops_ml.synthetic) — CI never needs the real dataset or the real
artifact. The replayed-online path must match the offline path exactly:
same FeatureComputer, same feature order, same normalization.
"""

import json
from datetime import UTC, timedelta

import numpy as np
import pandas as pd
import pytest
from sqlalchemy import select

from pulseops_api.models import AgentRun, Incident, ModelPrediction, ShadowAlert
from pulseops_api.services import shadow_trigger
from pulseops_api.services.inference import ModelSourceError, OnlineInferenceService
from pulseops_api.services.replay import ReplayError, ReplayService, create_replay_run
from pulseops_api.services.replay_worker import MAX_SOURCE_SPAN_S, ReplayPacer
from pulseops_api.services.shadow_trigger import (
    dismiss_shadow_alert,
    process_prediction,
    promote_shadow_alert,
)
from pulseops_ml.synthetic import TRAIN_END, TRAIN_START, VAL_END, synthetic_telemetry


def assert_no_auto_artifacts(session, asset_id):
    """Shadow processing must never create an Incident or AgentRun for the
    asset; only operator promotions (titled [SYNTHETIC/DEMO]) may."""
    incidents = session.scalars(select(Incident).where(Incident.asset_id == asset_id)).all()
    auto = [i for i in incidents if "SYNTHETIC/DEMO" not in i.title]
    runs = session.scalars(select(AgentRun).where(AgentRun.asset_id == asset_id)).all()
    assert not auto and not runs, (
        f"shadow mode violated: auto incidents={len(auto)}, agent runs={len(runs)}"
    )


@pytest.fixture(scope="session")
def fixture_artifact(tmp_path_factory):
    """Train a disposable artifact on the synthetic fixture (once per session)."""
    from pulseops_ml import train

    frame = synthetic_telemetry()
    out = tmp_path_factory.mktemp("artifact")
    data = out / "fixture.parquet"
    frame.to_parquet(data, index=False)
    metadata = train.train(data, out, TRAIN_START, TRAIN_END, VAL_END)
    (out / "trigger_policy.json").write_text(
        json.dumps(
            {
                "mode": "shadow",
                "generated_at": "test-policy-v1",
                "rationale": "fixture policy for tests",
            }
        ),
        encoding="utf-8",
    )
    return out, frame, metadata


def frame_rows(frame: pd.DataFrame, start, end):
    """Synthetic frame → aware SourceRows (all 15 channels)."""
    ts = pd.DatetimeIndex(frame["timestamp"])
    lo, hi = pd.Timestamp(start).tz_localize(None), pd.Timestamp(end).tz_localize(None)
    window = frame[(ts > lo) & (ts <= hi)]
    channels = [c for c in frame.columns if c != "timestamp"]
    return [
        (
            row.timestamp.to_pydatetime().replace(tzinfo=UTC),
            {c: float(getattr(row, c)) for c in channels},
        )
        for row in window.itertuples(index=False)
    ]


def replay_slice(session, ids, frame, start, end, speed=600.0):
    run = create_replay_run(
        session,
        ids["apu_id"],
        source_start=pd.Timestamp(start).tz_localize(UTC).to_pydatetime(),
        source_end=pd.Timestamp(end).tz_localize(UTC).to_pydatetime(),
        dataset_checksum="fixture",
        speed=speed,
    )
    svc = ReplayService(session, run.id)
    svc.start()
    svc.ingest_chunk(frame_rows(frame, start, end))
    return run


HEALTHY_START, HEALTHY_END = "2021-03-02 06:00:00", "2021-03-02 09:00:00"
ANOM_START, ANOM_END = "2021-03-03 13:00:00", "2021-03-03 16:00:00"


def test_offline_and_replayed_online_features_and_scores_match(seeded, fixture_artifact):
    session, ids = seeded
    artifacts_dir, frame, metadata = fixture_artifact
    run = replay_slice(session, ids, frame, HEALTHY_START, HEALTHY_END)

    from pulseops_ml.artifacts import risk_from_raw
    from pulseops_ml.features import FeatureComputer, feature_names

    svc = OnlineInferenceService(session, ids["apu_id"], artifacts_path=artifacts_dir)
    for window_end in ["2021-03-02 07:00:00", "2021-03-02 08:30:00"]:
        naive_end = pd.Timestamp(window_end)
        offline = FeatureComputer(frame, window_minutes=svc.window_minutes).at(naive_end)

        online_frame = svc.reconstruct_frame(
            (naive_end - timedelta(minutes=svc.window_minutes)).tz_localize(UTC).to_pydatetime(),
            naive_end.tz_localize(UTC).to_pydatetime(),
        )
        online = FeatureComputer(online_frame, window_minutes=svc.window_minutes).at(naive_end)

        # Exact feature parity: replayed DB state reproduces the source frame.
        for name in feature_names():
            assert online[name] == pytest.approx(offline[name], abs=1e-9), name

        prediction = svc.score_window(naive_end.tz_localize(UTC), replay_run_id=run.id)
        X = np.array([[offline[n] for n in svc.feature_spec["feature_names"]]])
        offline_risk = float(
            risk_from_raw(-svc.pipeline.decision_function(X), metadata["calibration"])[0]
        )
        assert prediction.risk == pytest.approx(offline_risk, abs=1e-6)
        assert prediction.model_source == "metropt3_iforest_v1"
        assert prediction.feature_version == "metropt3_fw_v1"
        assert prediction.policy_mode == "shadow" and prediction.policy_version == "test-policy-v1"
        assert prediction.window_start is not None
        explanation = json.loads(prediction.explanation_json)
        assert explanation["kind"] == "standardized_feature_deviations"
        assert "NOT causal" in explanation["note"]


def test_scoring_is_idempotent_and_coverage_gated(seeded, fixture_artifact):
    session, ids = seeded
    artifacts_dir, frame, _ = fixture_artifact
    run = replay_slice(session, ids, frame, HEALTHY_START, HEALTHY_END)
    svc = OnlineInferenceService(session, ids["apu_id"], artifacts_path=artifacts_dir)

    end = pd.Timestamp("2021-03-02 07:00:00").tz_localize(UTC)
    first = svc.score_window(end, replay_run_id=run.id)
    again = svc.score_window(end, replay_run_id=run.id)
    assert first.id == again.id  # retry returns the same row

    # No telemetry that far in: no fabricated prediction.
    assert svc.score_window(pd.Timestamp("2021-03-02 23:00:00").tz_localize(UTC)) is None

    svc.score_pending(run)
    n = len(session.scalars(select(ModelPrediction)).all())
    assert svc.score_pending(run) == []  # second pass finds nothing new
    assert len(session.scalars(select(ModelPrediction)).all()) == n


def test_provenance_guards(seeded, fixture_artifact, tmp_path):
    session, ids = seeded
    artifacts_dir, frame, _ = fixture_artifact

    # Only air_compressor may use the MetroPT artifact.
    with pytest.raises(ModelSourceError, match="air_compressor"):
        OnlineInferenceService(session, ids["chiller3_id"], artifacts_path=artifacts_dir)

    # Missing artifact fails loudly — nothing can masquerade as the model.
    with pytest.raises(ModelSourceError, match="refusing to score"):
        OnlineInferenceService(session, ids["apu_id"], artifacts_path=tmp_path)

    # Chillers/pumps stay on the labeled mock adapter.
    from pulseops_api.services.failure_risk import get_adapter

    pred = get_adapter().predict(session, ids["chiller3_id"])
    assert pred["model_source"] == "mock_demo_v0"
    assert (
        session.scalars(
            select(ModelPrediction).where(ModelPrediction.asset_id == ids["chiller3_id"])
        ).all()
        == []
    )


def test_shadow_trigger_raises_one_alert_and_never_an_incident(seeded, fixture_artifact):
    session, ids = seeded
    artifacts_dir, frame, _ = fixture_artifact
    run = replay_slice(session, ids, frame, ANOM_START, ANOM_END)
    svc = OnlineInferenceService(session, ids["apu_id"], artifacts_path=artifacts_dir)

    stored = svc.score_pending(run, limit=100)
    alerts = [p for p in stored if p.alert]
    assert len(alerts) >= shadow_trigger.PERSISTENCE_WINDOWS, "anomalous slice must alert"
    assert all(p.shadow for p in stored)

    episodes = session.scalars(select(ShadowAlert)).all()
    assert len(episodes) == 1, "persistence + dedup: exactly one episode"
    alert = episodes[0]
    assert alert.status == "observed"
    assert alert.consecutive_windows >= shadow_trigger.PERSISTENCE_WINDOWS
    assert "WOULD-TRIGGER" in alert.trigger_reason
    assert alert.low_threshold == pytest.approx(alert.high_threshold * shadow_trigger.LOW_FACTOR)

    # THE hard shadow guarantee: no Incident, no AgentRun, no matter how many alerts.
    assert_no_auto_artifacts(session, ids["apu_id"])
    assert session.scalars(select(Incident).where(Incident.asset_id == ids["apu_id"])).all() == []
    assert session.scalars(select(AgentRun).where(AgentRun.asset_id == ids["apu_id"])).all() == []


def test_hysteresis_clears_and_cooldown_blocks(seeded, fixture_artifact):
    session, ids = seeded
    artifacts_dir, frame, metadata = fixture_artifact
    run = replay_slice(session, ids, frame, ANOM_START, ANOM_END)
    svc = OnlineInferenceService(session, ids["apu_id"], artifacts_path=artifacts_dir)
    svc.score_pending(run, limit=100)
    alert = session.scalars(select(ShadowAlert)).one()
    last = session.scalars(
        select(ModelPrediction).order_by(ModelPrediction.window_end.desc())
    ).first()

    def synthetic_prediction(offset_min, risk):
        p = ModelPrediction(
            replay_run_id=last.replay_run_id,
            asset_id=ids["apu_id"],
            window_end=last.window_end + timedelta(minutes=offset_min),
            risk=risk,
            threshold=last.threshold,
            alert=risk >= last.threshold,
            shadow=True,
            model_source=last.model_source,
            policy_mode="shadow",
            policy_version=last.policy_version,
        )
        session.add(p)
        session.flush()
        return p

    # Risk falls below the LOW threshold → episode clears, cooldown starts.
    process_prediction(session, synthetic_prediction(10, 0.01))
    session.refresh(alert)
    assert alert.hysteresis_state == "cleared"
    assert alert.cooldown_until is not None

    # New alerting streak inside the cooldown window → no new episode.
    for i in range(shadow_trigger.PERSISTENCE_WINDOWS):
        process_prediction(session, synthetic_prediction(20 + i * 10, 0.99))
    assert len(session.scalars(select(ShadowAlert)).all()) == 1


def test_dismissed_episode_stays_quiet_until_the_condition_clears(seeded, fixture_artifact):
    """Dismissing acknowledges the episode: a condition that stays high does
    not re-alert. Once the score falls below the hysteresis threshold and the
    cooldown passes, a new streak opens a new alert as usual."""
    session, ids = seeded
    artifacts_dir, frame, _ = fixture_artifact
    run = replay_slice(session, ids, frame, ANOM_START, ANOM_END)
    OnlineInferenceService(session, ids["apu_id"], artifacts_path=artifacts_dir).score_pending(
        run, limit=100
    )
    alert = session.scalars(select(ShadowAlert)).one()
    last = session.scalars(
        select(ModelPrediction).order_by(ModelPrediction.window_end.desc())
    ).first()
    shadow_trigger.dismiss_shadow_alert(session, alert.id, "operator")

    def predict(offset_min, risk):
        p = ModelPrediction(
            replay_run_id=last.replay_run_id,
            asset_id=ids["apu_id"],
            window_end=last.window_end + timedelta(minutes=offset_min),
            risk=risk,
            threshold=last.threshold,
            alert=risk >= last.threshold,
            shadow=True,
            model_source=last.model_source,
            policy_mode="shadow",
            policy_version=last.policy_version,
        )
        session.add(p)
        session.flush()
        process_prediction(session, p)

    # Still high: absorbed by the dismissed episode, no new alert.
    for i in range(6):
        predict(10 + i * 10, 0.99)
    assert len(session.scalars(select(ShadowAlert)).all()) == 1

    # Clears, then a fresh streak after the cooldown opens a new alert.
    predict(80, 0.01)
    start = 80 + shadow_trigger.COOLDOWN_MINUTES + 10
    for i in range(shadow_trigger.PERSISTENCE_WINDOWS):
        predict(start + i * 10, 0.99)
    alerts = session.scalars(select(ShadowAlert).order_by(ShadowAlert.id)).all()
    assert [a.status for a in alerts] == ["dismissed", "observed"]


def test_identical_replays_are_isolated_and_reset_clears_only_derived_state(
    seeded, fixture_artifact
):
    """Repeating a demo interval must produce a fresh, internally coherent
    observation without mixing telemetry, predictions, or alert episodes.
    Resetting one unpromoted run must not damage the other run's audit trail.
    """
    session, ids = seeded
    artifacts_dir, frame, _ = fixture_artifact
    service = OnlineInferenceService(session, ids["apu_id"], artifacts_path=artifacts_dir)

    first = replay_slice(session, ids, frame, ANOM_START, ANOM_END)
    first_predictions = service.score_pending(first, limit=100)
    assert first_predictions
    first_alert = session.scalars(
        select(ShadowAlert).where(ShadowAlert.replay_run_id == first.id)
    ).one()

    second = replay_slice(session, ids, frame, ANOM_START, ANOM_END)
    second_predictions = service.score_pending(second, limit=100)
    assert second_predictions
    second_alert = session.scalars(
        select(ShadowAlert).where(ShadowAlert.replay_run_id == second.id)
    ).one()

    assert first_alert.id != second_alert.id
    assert {p.window_end for p in first_predictions} == {p.window_end for p in second_predictions}
    assert all(p.replay_run_id == first.id for p in first_predictions)
    assert all(p.replay_run_id == second.id for p in second_predictions)

    ReplayService(session, second.id).reset()
    assert (
        session.scalars(
            select(ModelPrediction).where(ModelPrediction.replay_run_id == second.id)
        ).all()
        == []
    )
    assert (
        session.scalars(select(ShadowAlert).where(ShadowAlert.replay_run_id == second.id)).all()
        == []
    )
    assert session.scalars(
        select(ModelPrediction).where(ModelPrediction.replay_run_id == first.id)
    ).all()
    assert session.get(ShadowAlert, first_alert.id) is not None


def test_promotion_creates_exactly_one_synthetic_incident(seeded, fixture_artifact):
    session, ids = seeded
    artifacts_dir, frame, _ = fixture_artifact
    run = replay_slice(session, ids, frame, ANOM_START, ANOM_END)
    OnlineInferenceService(session, ids["apu_id"], artifacts_path=artifacts_dir).score_pending(
        run, limit=100
    )
    alert = session.scalars(select(ShadowAlert)).one()

    with pytest.raises(shadow_trigger.ShadowTriggerError, match="operator identity"):
        promote_shadow_alert(session, alert.id, "")

    incident = promote_shadow_alert(session, alert.id, operator="ifzaal")
    assert "[SYNTHETIC/DEMO]" in incident.title
    assert "operator 'ifzaal'" in incident.summary
    assert alert.model_source in incident.summary  # provenance retained
    assert alert.status == "promoted" and alert.incident_id == incident.id

    # Idempotent: repeated promotion returns the SAME incident.
    again = promote_shadow_alert(session, alert.id, operator="ifzaal")
    assert again.id == incident.id
    assert (
        len(session.scalars(select(Incident).where(Incident.asset_id == ids["apu_id"])).all()) == 1
    )

    # Promotion started no run; at most one can ever attach (existing guard).
    assert session.scalars(select(AgentRun).where(AgentRun.asset_id == ids["apu_id"])).all() == []
    with pytest.raises(ReplayError, match="preserve its audit trail"):
        ReplayService(session, run.id).reset()


def test_promote_and_investigate_is_atomic_and_idempotent(seeded, fixture_artifact):
    """One click → exactly one [SYNTHETIC/DEMO] incident + exactly one
    investigation advanced to the human-approval gate; retries and repeated
    clicks converge on the same pair; nothing executes without approval."""
    from pulseops_api.services.shadow_trigger import promote_and_investigate

    session, ids = seeded
    artifacts_dir, frame, _ = fixture_artifact
    run = replay_slice(session, ids, frame, ANOM_START, ANOM_END)
    OnlineInferenceService(session, ids["apu_id"], artifacts_path=artifacts_dir).score_pending(
        run, limit=100
    )
    alert = session.scalars(select(ShadowAlert)).one()

    incident, arun = promote_and_investigate(session, alert.id, operator="ifzaal")
    assert "[SYNTHETIC/DEMO]" in incident.title
    assert arun.incident_id == incident.id
    # Read-only phases ran; the proposal (if grounded) awaits a HUMAN.
    assert arun.status in ("awaiting_evidence", "awaiting_approval", "escalated")
    assert all(a.status == "proposed" for a in arun.actions)
    diagnose = json.loads(next(s for s in arun.steps if s.phase == "diagnose").output_json)
    trigger_prediction = session.get(ModelPrediction, alert.latest_prediction_id)
    assert diagnose["model_source"] == "metropt3_iforest_v1"
    assert diagnose["risk"] == pytest.approx(trigger_prediction.risk)
    evidence_manifest = diagnose["reasoning"]["evidence_snapshot"]
    prediction_evidence = next(
        item for item in evidence_manifest["evidence"] if item["ref"] == "failure_prediction"
    )
    assert {"prediction_id", "replay_run_id", "score_semantics"}.issubset(
        prediction_evidence["fact_keys"]
    )
    if arun.status == "awaiting_approval":
        action_payload = json.loads(arun.actions[0].payload_json)
        assert action_payload["trigger_source"] == "metropt3_iforest_v1"
        assert action_payload["trigger_score"] == pytest.approx(trigger_prediction.risk)
        assert action_payload["trigger_prediction_id"] == trigger_prediction.id
        assert action_payload["risk_source"] == "mock_demo_v0"
        baseline_call = next(
            call for call in arun.tool_calls if call.tool_name == "run_verification_baseline"
        )
        baseline = json.loads(baseline_call.result_json)
        assert baseline["anchored_replay_run_id"] == run.id
        assert baseline["anchored_prediction_id"] == trigger_prediction.id
    from pulseops_api.models import WorkOrder

    assert session.scalars(select(WorkOrder).where(WorkOrder.source == "agent")).all() == []

    # Retry / double-click: same incident, same run — no duplicates.
    incident2, arun2 = promote_and_investigate(session, alert.id, operator="ifzaal")
    assert incident2.id == incident.id and arun2.id == arun.id
    assert (
        len(session.scalars(select(Incident).where(Incident.asset_id == ids["apu_id"])).all()) == 1
    )
    assert (
        len(session.scalars(select(AgentRun).where(AgentRun.incident_id == incident.id)).all()) == 1
    )


def test_concurrent_promotion_race_on_postgres(seeded, fixture_artifact):
    """REAL two-session race: two threads with independent PostgreSQL
    sessions call promote_and_investigate simultaneously. The CAS promotion
    plus the unique-index-backed run insert must yield exactly one incident
    and one run. (Cross-session races need a real server — skipped on the
    in-memory SQLite default.)"""
    import threading

    from conftest import TEST_DB_URL
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker

    from pulseops_api.services.shadow_trigger import promote_and_investigate

    if TEST_DB_URL.startswith("sqlite"):
        pytest.skip("cross-session race requires a real database server")

    session, ids = seeded
    artifacts_dir, frame, _ = fixture_artifact
    run = replay_slice(session, ids, frame, ANOM_START, ANOM_END)
    OnlineInferenceService(session, ids["apu_id"], artifacts_path=artifacts_dir).score_pending(
        run, limit=100
    )
    alert_id = session.scalars(select(ShadowAlert)).one().id
    session.commit()

    engine2 = create_engine(TEST_DB_URL)
    Racer = sessionmaker(bind=engine2, autoflush=False, expire_on_commit=False)
    barrier = threading.Barrier(2)
    results, errors = [], []

    def racer(name):
        s = Racer()
        try:
            barrier.wait(timeout=10)
            incident, arun = promote_and_investigate(s, alert_id, operator=name)
            results.append((incident.id, arun.id))
        except Exception as e:  # noqa: BLE001 — recorded for the assertion
            errors.append(f"{name}: {type(e).__name__}: {e}")
        finally:
            s.close()

    threads = [threading.Thread(target=racer, args=(f"op{i}",)) for i in range(2)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=60)
    engine2.dispose()

    assert not errors, errors
    assert len(results) == 2
    assert results[0] == results[1], "both racers must converge on the same pair"
    session.expire_all()
    assert (
        len(session.scalars(select(Incident).where(Incident.asset_id == ids["apu_id"])).all()) == 1
    )
    incident_id = results[0][0]
    assert (
        len(session.scalars(select(AgentRun).where(AgentRun.incident_id == incident_id)).all()) == 1
    )


def test_dismissal_creates_nothing(seeded, fixture_artifact):
    session, ids = seeded
    artifacts_dir, frame, _ = fixture_artifact
    run = replay_slice(session, ids, frame, ANOM_START, ANOM_END)
    OnlineInferenceService(session, ids["apu_id"], artifacts_path=artifacts_dir).score_pending(
        run, limit=100
    )
    alert = session.scalars(select(ShadowAlert)).one()

    dismissed = dismiss_shadow_alert(session, alert.id, operator="ifzaal")
    assert dismissed.status == "dismissed"
    assert session.scalars(select(Incident).where(Incident.asset_id == ids["apu_id"])).all() == []
    with pytest.raises(shadow_trigger.ShadowTriggerError):
        promote_shadow_alert(session, alert.id, operator="ifzaal")


def test_replayed_telemetry_grounds_diagnosis_on_source_time(seeded, fixture_artifact):
    """Operational reads follow the ASSET'S clock (observation anchor):
    telemetry replayed with 2021 source timestamps must be visible to the
    agent's evidence tools, ground an air-leak diagnosis, and keep the whole
    act/verify cycle on source time."""
    from pulseops_api.models import VerificationResult
    from pulseops_api.services import approval
    from pulseops_api.services.agent_loop import AgentEngine

    session, ids = seeded
    _, frame, _ = fixture_artifact
    replay_slice(session, ids, frame, ANOM_START, ANOM_END)  # anomalous source period

    engine = AgentEngine.start_run(session, ids["apu_id"], trigger="anchor test")
    run = engine.run_until_blocked()
    assert run.status == "awaiting_approval", "replayed evidence must ground a diagnosis"
    diagnose = json.loads(next(s for s in run.steps if s.phase == "diagnose").output_json)
    assert diagnose["abstained"] is False
    assert diagnose["hypotheses"][0]["cause"] == "compressed-air leak"

    # Approve → environment responds on SOURCE time → verification passes
    # counting post-anchor telemetry on the same clock.
    approval.approve_action(session, run.actions[0].id, decided_by="operator")
    run = engine.run_until_blocked()
    assert run.status == "completed"
    vr = session.scalars(
        select(VerificationResult).where(VerificationResult.run_id == run.id)
    ).one()
    assert vr.outcome == "passed"
    details = json.loads(vr.details_json)
    assert min(details["per_sensor_post_readings"].values()) >= 4


# --------------------------------------------------------- HTTP acceptance


def test_full_window3_acceptance_path_over_http(seeded, fixture_artifact, monkeypatch):
    """Replay → telemetry+state persist → predictions → ONE shadow alert →
    no auto incident → operator promotes → exactly one synthetic incident →
    investigation can start → any work order stays approval-gated."""
    from fastapi.testclient import TestClient

    import pulseops_api.routes.replay as replay_routes
    from pulseops_api.db import get_session
    from pulseops_api.main import app
    from pulseops_api.services import replay as replay_module

    session, ids = seeded
    artifacts_dir, frame, _ = fixture_artifact
    monkeypatch.setenv("PULSEOPS_ARTIFACTS_DIR", str(artifacts_dir))
    monkeypatch.setattr(
        replay_module,
        "load_metropt_rows",
        lambda start, end, parquet_path=None: frame_rows(frame, start, end),
    )
    # The synthetic fixture lives in 2021; widen the route's dataset bounds.
    monkeypatch.setattr(
        replay_routes,
        "METROPT_DATA_START",
        pd.Timestamp("2021-03-01").tz_localize(UTC).to_pydatetime(),
    )
    monkeypatch.setattr(
        replay_routes,
        "METROPT_DATA_END",
        pd.Timestamp("2021-03-04").tz_localize(UTC).to_pydatetime(),
    )

    def _override():
        yield session

    app.dependency_overrides[get_session] = _override
    try:
        with TestClient(app, headers={"Content-Type": "application/json"}) as c:
            # Input validation returns useful errors.
            bad = c.post("/api/replay", json={"source_start": "2019-01-01T00:00:00Z"})
            assert bad.status_code == 422 and "outside the MetroPT-3 range" in bad.text
            assert c.post("/api/replay", json={"duration_hours": -1}).status_code == 422

            run = c.post(
                "/api/replay",
                json={
                    "source_start": "2021-03-03T13:00:00Z",
                    "duration_hours": 2.0,
                    "speed": 3600.0,
                },
            ).json()
            assert run["status"] == "ready" and run["asset_id"] == ids["apu_id"]

            assert c.post(f"/api/replay/{run['id']}/start").json()["status"] == "running"
            state = c.post(f"/api/replay/{run['id']}/advance", json={"wall_seconds": 2.0}).json()
            assert state["inserted"] > 0 and state["predictions"] > 0
            assert "inference_note" not in state  # artifact was found and used

            detail = c.get(f"/api/replay/{run['id']}").json()
            assert detail["cursor"] is not None and detail["sim_time"] is not None

            preds = c.get(
                f"/api/assets/{ids['apu_id']}/predictions?replay_run_id={run['id']}"
            ).json()
            assert preds and preds[0]["model_source"] == "metropt3_iforest_v1"
            assert all(p["shadow"] and p["policy_mode"] == "shadow" for p in preds)
            assert all(p["replay_run_id"] == run["id"] for p in preds)

            alerts = c.get(f"/api/shadow-alerts?replay_run_id={run['id']}&status=observed").json()
            assert len(alerts) == 1 and alerts[0]["status"] == "observed"
            assert alerts[0]["replay_run_id"] == run["id"]
            dashboard = c.get("/api/dashboard").json()
            flagship = dashboard["flagship_model"]
            assert flagship["model_source"] == "metropt3_iforest_v1"
            assert flagship["pending_shadow_alerts"] == 1
            assert flagship["status"] == "review_required"
            evaluation = c.get("/api/model-evaluation/metropt3_iforest_v1").json()
            assert evaluation["primary_policy"]["events_detected"] == 3
            assert evaluation["trigger_mode"] == "shadow"
            # No auto incident for the APU (only the seeded chiller incident exists).
            incidents = c.get(f"/api/assets/{ids['apu_id']}/incidents").json()
            assert incidents == []

            incident = c.post(
                f"/api/shadow-alerts/{alerts[0]['id']}/promote", json={"decided_by": "ifzaal"}
            ).json()
            assert "[SYNTHETIC/DEMO]" in incident["title"]
            again = c.post(
                f"/api/shadow-alerts/{alerts[0]['id']}/promote", json={"decided_by": "ifzaal"}
            ).json()
            assert again["id"] == incident["id"]  # idempotent

            # Investigation starts from the promoted incident; the proposal
            # still awaits human approval (the compressor has real replayed
            # anomalous telemetry, so Diagnose grounds an air-leak hypothesis).
            agent_run = c.post(
                "/api/agent/runs",
                json={"asset_id": ids["apu_id"], "trigger": "promoted shadow alert"},
            ).json()
            finished = c.post(
                f"/api/agent/runs/{agent_run['id']}/advance", json={"mode": "until_blocked"}
            ).json()
            assert finished["status"] in ("awaiting_evidence", "awaiting_approval", "escalated")
            if finished["status"] == "awaiting_approval":
                assert finished["actions"][0]["status"] == "proposed"
    finally:
        app.dependency_overrides.clear()


# ------------------------------------------------------------------- pacer


def test_pacer_speed_and_bounded_stepping(seeded, fixture_artifact):
    session, ids = seeded
    artifacts_dir, frame, _ = fixture_artifact
    run = replay_slice(session, ids, frame, HEALTHY_START, HEALTHY_END, speed=600.0)
    ReplayService(session, run.id).reset()
    ReplayService(session, run.id).start()

    provider = lambda start, end: frame_rows(frame, start, end)  # noqa: E731
    pacer = ReplayPacer(session, run.id, provider)
    assert pacer.source_span(2.0) == 1200.0  # speed × wall seconds
    assert pacer.source_span(1e6) == MAX_SOURCE_SPAN_S  # bounded chunks

    state = pacer.step(wall_seconds=1.0)  # 600 source-seconds
    assert state["inserted"] == 60 * 8  # 10s cadence → 60 rows × 8
    assert state["cursor"] is not None and state["sim_time"] == state["cursor"]

    # Pause takes effect on the next tick and prevents advancement.
    ReplayService(session, run.id).pause()
    paused = pacer.step(wall_seconds=1.0)
    assert paused["status"] == "paused" and paused["inserted"] == 0

    # Resume + fresh pacer (restart) continues from the persisted cursor.
    ReplayService(session, run.id).start()
    pacer2 = ReplayPacer(session, run.id, provider)
    resumed = pacer2.step(wall_seconds=1.0)
    assert resumed["inserted"] == 60 * 8
    # Redelivering the union is harmless.
    dup = pacer2.step(wall_seconds=0.0)
    assert dup["inserted"] == 0 or dup["status"] != "running"


def test_pacer_completion_and_failure(seeded, fixture_artifact):
    session, ids = seeded
    artifacts_dir, frame, _ = fixture_artifact
    run = replay_slice(session, ids, frame, HEALTHY_START, "2021-03-02 06:20:00", speed=600.0)
    ReplayService(session, run.id).reset()
    ReplayService(session, run.id).start()
    provider = lambda start, end: frame_rows(frame, start, end)  # noqa: E731
    pacer = ReplayPacer(session, run.id, provider)
    status = pacer.run_loop(max_ticks=10, sleep_fn=lambda s: None)
    assert status == "completed"

    run2 = replay_slice(session, ids, frame, "2021-03-02 10:00:00", "2021-03-02 11:00:00")
    ReplayService(session, run2.id).reset()
    ReplayService(session, run2.id).start()

    def bad_provider(start, end):
        raise RuntimeError("parquet unavailable")

    pacer_bad = ReplayPacer(session, run2.id, bad_provider)
    with pytest.raises(RuntimeError):
        pacer_bad.step(1.0)
    session.refresh(pacer_bad.run)
    assert pacer_bad.run.status == "failed"
    assert "parquet unavailable" in pacer_bad.run.last_error
