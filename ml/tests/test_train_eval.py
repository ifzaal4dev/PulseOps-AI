"""End-to-end train → evaluate on the constructed fixture: reproducible
artifacts, honest metadata, event-aware detection of the injected anomaly."""

import json

import pandas as pd
import pytest
from conftest import ANOMALY_END, ANOMALY_START

from pulseops_ml import artifacts, evaluate, train

TRAIN_START = "2021-03-01 00:00:00"
TRAIN_END = "2021-03-02 18:00:00"
VAL_END = "2021-03-03 00:00:00"
FIXTURE_EVENTS = [
    {"id": 1, "kind": "air_leak", "start": str(ANOMALY_START), "end": str(ANOMALY_END)},
]


def run_pipeline(telemetry, tmp_path):
    data = tmp_path / "fixture.parquet"
    telemetry.to_parquet(data, index=False)
    out = tmp_path / "artifacts"
    metadata = train.train(data, out, TRAIN_START, TRAIN_END, VAL_END)
    return data, out, metadata


def test_training_produces_complete_artifact_contract(telemetry, tmp_path):
    data, out, metadata = run_pipeline(telemetry, tmp_path)

    pipeline, meta, spec = artifacts.load_artifacts(out)
    assert meta["model_source"] == "metropt3_iforest_v1"
    assert (out / f"{meta['model_source']}.joblib").exists()

    # Provenance and honesty fields
    assert meta["dataset"]["zip_sha256"]
    assert meta["healthy_train_interval"] == [TRAIN_START, TRAIN_END]
    assert "NOT a calibrated probability" in meta["task"]
    assert "DETECTION" in meta["task"]  # never claims early warning
    assert 0.0 < meta["threshold"] <= 1.0
    assert "99.5th percentile" in meta["threshold_method"]
    assert meta["baseline_threshold_z"] > 0  # leakage-free baseline threshold
    assert meta["training_seconds"] is not None
    assert spec["feature_version"] == "metropt3_fw_v1"
    assert spec["feature_names"], "serving contract requires explicit feature names"

    # Reproducibility: retraining yields the identical calibration/threshold.
    out2 = tmp_path / "artifacts2"
    metadata2 = train.train(data, out2, TRAIN_START, TRAIN_END, VAL_END)
    assert metadata2["threshold"] == metadata["threshold"]
    assert metadata2["calibration"] == metadata["calibration"]


def test_artifact_loader_refuses_sklearn_version_mismatch(telemetry, tmp_path, monkeypatch):
    _, out, metadata = run_pipeline(telemetry, tmp_path)
    import sklearn

    monkeypatch.setattr(sklearn, "__version__", "99.0-test")
    with pytest.raises(RuntimeError, match=f"requires scikit-learn {metadata['sklearn_version']}"):
        artifacts.load_artifacts(out)


def test_leakage_guard_rejects_event_overlap(telemetry, tmp_path):
    data = tmp_path / "fixture.parquet"
    telemetry.to_parquet(data, index=False)
    import pytest

    from pulseops_ml import dataset

    original = dataset.FAILURE_EVENTS
    dataset.FAILURE_EVENTS = FIXTURE_EVENTS  # pretend the fixture event is documented
    try:
        with pytest.raises(ValueError, match="Leakage guard"):
            # val interval extends past the anomaly start → must be refused
            train.train(data, tmp_path / "bad", TRAIN_START, TRAIN_END, "2021-03-03 15:00:00")
    finally:
        dataset.FAILURE_EVENTS = original


def test_evaluation_is_holdout_only_and_detects_the_event(telemetry, tmp_path):
    data, out, _ = run_pipeline(telemetry, tmp_path)
    report = evaluate.evaluate(data, out, events=FIXTURE_EVENTS, reports_dir=tmp_path / "reports")

    # Holdout boundary comes from artifact metadata and is reported explicitly.
    interval = report["evaluated_interval"]
    assert interval["start_exclusive"] == VAL_END
    assert interval["n_holdout_windows"] > 0
    assert "strictly after" in interval["note"]
    assert "DETECTION" in report["task"].upper() or "detection" in report["task"]
    assert "positive hours = alert BEFORE" in report["lead_time_convention"]

    primary = report["policies"][0]
    assert primary["policy"] == "iforest_global"
    assert primary["event_recall"] == 1.0
    assert primary["events_missed"] == 0
    assert primary["per_event"][0]["detected"] is True
    assert primary["per_event"][0]["lead_time_hours"] is not None
    assert primary["alert_window_rate"] is not None  # merging can't hide volume
    # Sanity bound only on the tiny fixture (see real-data report for the
    # meaningful number): not constantly alerting.
    assert primary["false_alarms_per_operating_day"] < 10.0

    # The comparison includes the honestly-calibrated z baseline, and the
    # post-hoc z rows are labeled as unselectable.
    names = [p["policy"] for p in report["policies"]]
    assert "zscore_healthy_calibrated" in names
    post_hoc = [p for p in report["policies"] if p["policy"].endswith("_POST_HOC")]
    assert post_hoc and all("POST-HOC" in p["threshold_selection"] for p in post_hoc)

    # Trigger decision is machine-readable, never chooses a post-hoc policy,
    # and is persisted for the app.
    decision = report["trigger_decision"]
    assert decision["mode"] in ("shadow", "auto_trigger")
    assert decision["chosen_policy"] is None or "POST_HOC" not in decision["chosen_policy"]
    policy_file = json.loads((out / "trigger_policy.json").read_text(encoding="utf-8"))
    assert policy_file["mode"] == decision["mode"]

    assert (tmp_path / "reports" / "metropt3_eval.md").exists()
    payload = json.loads((tmp_path / "reports" / "metropt3_eval.json").read_text(encoding="utf-8"))
    assert payload["limitations"]


def test_pre_cutoff_data_cannot_affect_holdout_metrics(telemetry, tmp_path):
    """Poisoning telemetry that ends well before the holdout cutoff must not
    move ANY published metric — proof the denominators are out-of-sample."""
    data, out, _ = run_pipeline(telemetry, tmp_path)
    clean = evaluate.evaluate(data, out, events=FIXTURE_EVENTS, reports_dir=tmp_path / "r1")

    poisoned = telemetry.copy()
    cutoff = pd.Timestamp(VAL_END)
    # Stay > one feature-window clear of the cutoff so no holdout window can
    # even read the poisoned rows as feature input.
    target = poisoned["timestamp"] < cutoff - pd.Timedelta(hours=1)
    cols = [c for c in poisoned.columns if c != "timestamp"]
    poisoned.loc[target, cols] = 500.0  # wildly anomalous pre-cutoff world
    data2 = tmp_path / "poisoned.parquet"
    poisoned.to_parquet(data2, index=False)
    dirty = evaluate.evaluate(data2, out, events=FIXTURE_EVENTS, reports_dir=tmp_path / "r2")

    assert clean["policies"] == dirty["policies"]
    assert (
        clean["evaluated_interval"]["n_holdout_windows"]
        == dirty["evaluated_interval"]["n_holdout_windows"]
    )
    assert clean["primary_model_window_pr"] == dirty["primary_model_window_pr"]
    assert clean["primary_model_monthly_alert_rate"] == dirty["primary_model_monthly_alert_rate"]
