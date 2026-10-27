"""Leakage-safe windowed features for MetroPT-3 telemetry.

THE ONE CODE PATH RULE: `FeatureComputer.at(window_end)` computes the feature
vector for the half-open interval (window_end - window, window_end] and is
used by BOTH offline training (build_feature_matrix loops over a grid) and
online serving (the app builds a FeatureComputer over recent rows).
There is no separate vectorized implementation to drift out of parity.

Leakage guarantees (tested in ml/tests/test_features.py):
  * only rows with window_end - window < ts <= window_end contribute;
  * mutating any row after window_end cannot change the features at window_end.

Feature set (FEATURE_VERSION):
  per analog sensor: mean, std, min, max, last, delta (last - first)
  per digital signal: duty (fraction high), transitions (count of changes)
  window meta: row_count, coverage (rows / expected at nominal cadence)
"""

from __future__ import annotations

import numpy as np
import pandas as pd

from .dataset import ANALOG_COLUMNS, DIGITAL_COLUMNS

FEATURE_VERSION = "metropt3_fw_v1"
WINDOW_MINUTES = 30
STEP_MINUTES = 10
NOMINAL_CADENCE_SECONDS = 10  # published MetroPT-3 cadence
MIN_COVERAGE = 0.5  # below this the window is unusable (returns None)


def feature_names() -> list[str]:
    names: list[str] = []
    for col in ANALOG_COLUMNS:
        names += [f"{col}_{stat}" for stat in ("mean", "std", "min", "max", "last", "delta")]
    for col in DIGITAL_COLUMNS:
        names += [f"{col}_duty", f"{col}_transitions"]
    names += ["row_count", "coverage"]
    return names


class FeatureComputer:
    """Computes window features over a chronologically sorted telemetry frame."""

    def __init__(self, df: pd.DataFrame, window_minutes: int = WINDOW_MINUTES):
        if not df["timestamp"].is_monotonic_increasing:
            df = df.sort_values("timestamp", kind="mergesort")
        # ns-precision int array for O(log n) window slicing
        self._ts = df["timestamp"].values.astype("datetime64[ns]").astype(np.int64)
        self._cols = {c: df[c].values.astype(np.float64) for c in ANALOG_COLUMNS + DIGITAL_COLUMNS}
        self.window_minutes = window_minutes
        self._window_ns = int(window_minutes * 60 * 1e9)
        self._expected_rows = window_minutes * 60 / NOMINAL_CADENCE_SECONDS

    def at(self, window_end: pd.Timestamp) -> dict[str, float] | None:
        """Feature vector for (window_end - window, window_end], or None when
        coverage is insufficient. Strictly past-only relative to window_end."""
        end_ns = pd.Timestamp(window_end).value
        start_ns = end_ns - self._window_ns
        lo = int(np.searchsorted(self._ts, start_ns, side="right"))  # ts > start
        hi = int(np.searchsorted(self._ts, end_ns, side="right"))  # ts <= end
        n = hi - lo
        coverage = n / self._expected_rows
        if n == 0 or coverage < MIN_COVERAGE:
            return None

        out: dict[str, float] = {}
        for col in ANALOG_COLUMNS:
            v = self._cols[col][lo:hi]
            out[f"{col}_mean"] = float(np.mean(v))
            out[f"{col}_std"] = float(np.std(v))
            out[f"{col}_min"] = float(np.min(v))
            out[f"{col}_max"] = float(np.max(v))
            out[f"{col}_last"] = float(v[-1])
            out[f"{col}_delta"] = float(v[-1] - v[0])
        for col in DIGITAL_COLUMNS:
            v = self._cols[col][lo:hi]
            out[f"{col}_duty"] = float(np.mean(v))
            out[f"{col}_transitions"] = float(np.count_nonzero(np.diff(v)))
        out["row_count"] = float(n)
        out["coverage"] = float(min(coverage, 1.0))
        return out


def window_grid(
    start: pd.Timestamp, end: pd.Timestamp, step_minutes: int = STEP_MINUTES
) -> pd.DatetimeIndex:
    """Window-end grid aligned to step boundaries."""
    first = pd.Timestamp(start).ceil(f"{step_minutes}min")
    return pd.date_range(first, end, freq=f"{step_minutes}min")


def build_feature_matrix(
    df: pd.DataFrame,
    start: pd.Timestamp | None = None,
    end: pd.Timestamp | None = None,
    window_minutes: int = WINDOW_MINUTES,
    step_minutes: int = STEP_MINUTES,
) -> pd.DataFrame:
    """Offline feature matrix indexed by window_end — a thin loop over the
    same FeatureComputer.at() used online."""
    start = start or df["timestamp"].iloc[0]
    end = end or df["timestamp"].iloc[-1]
    fc = FeatureComputer(df, window_minutes=window_minutes)
    rows, index = [], []
    for window_end in window_grid(start, end, step_minutes):
        feats = fc.at(window_end)
        if feats is not None:
            rows.append(feats)
            index.append(window_end)
    matrix = pd.DataFrame(rows, index=pd.DatetimeIndex(index, name="window_end"))
    return matrix[feature_names()]  # stable column order
