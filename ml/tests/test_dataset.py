"""Ingestion/validation checks: schema, chronology, duplicates, missing and
impossible values, operating-mode analysis."""

import numpy as np
import pandas as pd
import pytest

from pulseops_ml import dataset


def test_clean_frame_validates_with_zero_issues(telemetry):
    df, report = dataset.validate_frame(telemetry.copy())
    assert report["rows_clean"] == report["rows_raw"]
    assert report["duplicate_timestamps"] == 0
    assert report["out_of_order_rows"] == 0
    assert report["missing_values"] == {}
    assert report["out_of_range_values"] == {}
    assert report["non_binary_digital_values"] == {}
    assert report["median_cadence_seconds"] == 10.0
    modes = report["operating_modes"]
    # fixture duty: healthy 1/3 for 2.5 days + 2/3 for 6h → ~0.36
    assert 0.30 < modes["loaded_fraction"] < 0.45
    assert modes["mean_TP2_loaded"] > 5.0 > modes["mean_TP2_idle"]


def test_validation_detects_injected_corruption(telemetry):
    df = telemetry.copy()
    df.loc[50, "timestamp"] = df.loc[49, "timestamp"]  # duplicate ts
    df.loc[100, "timestamp"] = df.loc[10, "timestamp"] - pd.Timedelta(seconds=5)  # out of order
    df.loc[200, "TP2"] = np.nan  # missing
    df.loc[300, "Oil_temperature"] = 400.0  # impossible
    df.loc[400, "COMP"] = 0.5  # non-binary digital

    cleaned, report = dataset.validate_frame(df)
    assert report["duplicate_timestamps"] >= 1
    assert report["out_of_order_rows"] >= 1
    assert report["missing_values"].get("TP2", 0) == 1
    assert report["out_of_range_values"].get("Oil_temperature", 0) == 1
    assert report["non_binary_digital_values"].get("COMP", 0) == 1
    # cleaning: duplicates dropped, order restored
    assert cleaned["timestamp"].is_monotonic_increasing
    assert cleaned["timestamp"].duplicated().sum() == 0


def test_schema_mismatch_is_fatal(telemetry):
    with pytest.raises(ValueError, match="schema mismatch"):
        dataset.validate_frame(telemetry.drop(columns=["Motor_current"]))


def test_processed_parquet_env_override(monkeypatch, tmp_path, telemetry):
    """Containers point PULSEOPS_METROPT_PARQUET at a bind-mounted file; the
    loader must honor it instead of the package-relative default."""
    p = tmp_path / "replay.parquet"
    telemetry.head(50).to_parquet(p, index=False)
    monkeypatch.setenv("PULSEOPS_METROPT_PARQUET", str(p))
    assert dataset.processed_parquet_path() == p
    assert len(dataset.load_processed()) == 50


def test_processed_parquet_failure_messages(monkeypatch, tmp_path):
    monkeypatch.setenv("PULSEOPS_METROPT_PARQUET", str(tmp_path / "missing.parquet"))
    with pytest.raises(FileNotFoundError, match="PULSEOPS_METROPT_PARQUET"):
        dataset.load_processed()
    # Docker creates a DIRECTORY when the host file behind a bind mount is
    # missing — the error must say exactly that.
    d = tmp_path / "dir.parquet"
    d.mkdir()
    monkeypatch.setenv("PULSEOPS_METROPT_PARQUET", str(d))
    with pytest.raises(FileNotFoundError, match="is a directory"):
        dataset.load_processed()


def test_failure_events_are_cited_and_well_formed():
    assert len(dataset.FAILURE_EVENTS) == 4
    assert "archive.ics.uci.edu" in dataset.FAILURE_EVENTS_SOURCE
    for start, end, kind in dataset.failure_event_times():
        assert start < end
        assert kind == "air_leak"
