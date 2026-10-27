"""Feature-pipeline guarantees: no temporal leakage, offline/online
consistency (single code path), stable feature order, boundary semantics."""

import pandas as pd

from pulseops_ml import features
from pulseops_ml.features import FeatureComputer, build_feature_matrix, feature_names


def test_future_data_cannot_change_past_features(telemetry):
    """THE leakage test: mutating everything after window_end must not move
    a single feature value at window_end."""
    window_end = pd.Timestamp("2021-03-02 06:00:00")
    baseline = FeatureComputer(telemetry).at(window_end)

    poisoned = telemetry.copy()
    future = poisoned["timestamp"] > window_end
    cols = [c for c in poisoned.columns if c != "timestamp"]
    poisoned.loc[future, cols] = 9999.0
    after = FeatureComputer(poisoned).at(window_end)

    assert baseline == after


def test_window_boundaries_are_half_open(telemetry):
    """(end - window, end]: the row AT window_end counts, the row exactly at
    window_start does not."""
    df = telemetry.iloc[:400].copy()
    end = df["timestamp"].iloc[200]
    start = end - pd.Timedelta(minutes=features.WINDOW_MINUTES)

    marked = df.copy()
    marked.loc[marked["timestamp"] == end, "Oil_temperature"] = 99.0  # inside
    feats = FeatureComputer(marked).at(end)
    assert feats["Oil_temperature_max"] == 99.0
    assert feats["Oil_temperature_last"] == 99.0

    marked2 = df.copy()
    marked2.loc[marked2["timestamp"] == start, "Oil_temperature"] = 99.0  # excluded
    feats2 = FeatureComputer(marked2).at(end)
    assert feats2["Oil_temperature_max"] < 99.0


def test_offline_matrix_equals_single_window_computation(telemetry):
    """Offline building is a loop over the same FeatureComputer.at() used
    online — assert exact equality on sampled windows to lock the parity."""
    start = pd.Timestamp("2021-03-01 12:00:00")
    end = pd.Timestamp("2021-03-01 18:00:00")
    matrix = build_feature_matrix(telemetry, start=start, end=end)
    fc = FeatureComputer(telemetry)
    for window_end in matrix.index[::5]:
        single = fc.at(window_end)
        row = matrix.loc[window_end]
        for name in feature_names():
            assert row[name] == single[name], f"parity break at {window_end}: {name}"


def test_insufficient_coverage_returns_none(telemetry):
    fc = FeatureComputer(telemetry)
    # window ending long before the data starts → no rows
    assert fc.at(pd.Timestamp("2021-02-01 00:00:00")) is None
    # window straddling the very first rows → coverage below the minimum
    sparse_end = telemetry["timestamp"].iloc[0] + pd.Timedelta(minutes=2)
    assert fc.at(sparse_end) is None


def test_feature_names_are_stable_and_complete(telemetry):
    names = feature_names()
    assert len(names) == len(set(names))
    feats = FeatureComputer(telemetry).at(pd.Timestamp("2021-03-02 00:00:00"))
    assert set(feats.keys()) == set(names)
    matrix = build_feature_matrix(
        telemetry,
        start=pd.Timestamp("2021-03-02 00:00:00"),
        end=pd.Timestamp("2021-03-02 01:00:00"),
    )
    assert list(matrix.columns) == names


def test_anomalous_period_features_differ_from_healthy(telemetry):
    """Sanity: the injected air-leak signature is visible in the features."""
    fc = FeatureComputer(telemetry)
    healthy = fc.at(pd.Timestamp("2021-03-02 14:00:00"))
    anomalous = fc.at(pd.Timestamp("2021-03-03 14:00:00"))
    assert anomalous["COMP_duty"] < healthy["COMP_duty"]  # loading more often
    assert anomalous["Motor_current_mean"] > healthy["Motor_current_mean"]
    assert anomalous["Reservoirs_mean"] < healthy["Reservoirs_mean"]
