"""Train the flagship anomaly/failure-detection model on MetroPT-3.

    python -m pulseops_ml.train

Task definition (honest): unsupervised ANOMALY/FAILURE DETECTION. The
dataset has ~1.5M correlated rows but only FOUR independent failure episodes
(all air leaks) — far too few to train a supervised classifier or claim
calibrated probabilities. We train an IsolationForest on a documented
HEALTHY interval and score deviation from healthy behavior. "Early warning"
is deliberately NOT claimed: the holdout evaluation measured negative lead
times (alerts after documented onset), i.e. detection, not advance warning.

Leakage-safe intervals (defaults, documented in metadata):
  train    2020-02-01 .. 2020-04-10   (ends 8 days before failure #1)
  val      2020-04-10 .. 2020-04-16   (held-out healthy; threshold selection;
                                       ends 2 days before failure #1)

Threshold method: the alert threshold is the 99.5th percentile of the
held-out healthy validation risks — i.e. calibrated so ≈0.5% of healthy
windows alert. Documented in metadata.threshold_method.
"""

from __future__ import annotations

import argparse
import subprocess
import sys
import time
from datetime import datetime
from pathlib import Path

import numpy as np
import pandas as pd
import sklearn
from sklearn.ensemble import IsolationForest
from sklearn.pipeline import Pipeline
from sklearn.preprocessing import StandardScaler

from . import artifacts, dataset, features

DEFAULT_TRAIN_START = "2020-02-01"
DEFAULT_TRAIN_END = "2020-04-10"
DEFAULT_VAL_END = "2020-04-16"

# Features whose healthy-interval stats feed the trivial comparison baseline
# in evaluate.py (single-sensor z-score alerting).
BASELINE_FEATURES = ["Motor_current_mean", "Oil_temperature_mean"]


def git_sha() -> str:
    try:
        return subprocess.run(
            ["git", "rev-parse", "--short", "HEAD"],
            capture_output=True,
            text=True,
            cwd=Path(__file__).parent,
            check=True,
        ).stdout.strip()
    except Exception:
        return "unknown"


def train(
    data_path: Path | None,
    out_dir: Path | None,
    train_start: str,
    train_end: str,
    val_end: str,
    window_minutes: int = features.WINDOW_MINUTES,
    step_minutes: int = features.STEP_MINUTES,
) -> dict:
    t0 = time.time()
    df = dataset.load_processed(data_path)

    train_start_ts, train_end_ts, val_end_ts = map(pd.Timestamp, (train_start, train_end, val_end))
    for ev_start, ev_end, _ in dataset.failure_event_times():
        if train_start_ts < ev_end and ev_start < val_end_ts:
            raise ValueError(
                f"Leakage guard: failure event {ev_start}..{ev_end} overlaps the "
                f"healthy train/val interval {train_start_ts}..{val_end_ts}."
            )

    print(
        f"[train] building features {train_start} .. {val_end} "
        f"(window={window_minutes}m step={step_minutes}m) ..."
    )
    matrix = features.build_feature_matrix(
        df,
        start=train_start_ts,
        end=val_end_ts,
        window_minutes=window_minutes,
        step_minutes=step_minutes,
    )
    train_X = matrix[(matrix.index > train_start_ts) & (matrix.index <= train_end_ts)]
    val_X = matrix[(matrix.index > train_end_ts) & (matrix.index <= val_end_ts)]
    if len(train_X) < 50 or len(val_X) < 10:
        raise ValueError(f"Not enough windows: train={len(train_X)}, val={len(val_X)}")
    print(f"[train] {len(train_X)} training windows, {len(val_X)} validation windows")

    pipeline = Pipeline(
        [
            ("scaler", StandardScaler()),
            ("iforest", IsolationForest(n_estimators=300, random_state=42, n_jobs=-1)),
        ]
    )
    pipeline.fit(train_X.values)

    raw_train = -pipeline.decision_function(train_X.values)
    calibration = {
        "healthy_median": float(np.median(raw_train)),
        "healthy_q999": float(np.quantile(raw_train, 0.999)),
    }
    val_risk = artifacts.risk_from_raw(-pipeline.decision_function(val_X.values), calibration)
    threshold = float(np.quantile(val_risk, 0.995))

    # Healthy-calibrated threshold for the trivial z-score comparison baseline,
    # selected with the SAME rule (99.5th pct of held-out healthy val) so the
    # evaluation comparison is leakage-free on both sides.
    baseline_stats = {
        name: {"mean": float(train_X[name].mean()), "std": float(train_X[name].std() or 1e-9)}
        for name in BASELINE_FEATURES
    }
    val_z = np.max(
        [
            np.abs((val_X[name] - baseline_stats[name]["mean"]) / baseline_stats[name]["std"])
            for name in BASELINE_FEATURES
        ],
        axis=0,
    )
    baseline_threshold_z = float(np.quantile(val_z, 0.995))

    # Mode-conditioned thresholds (loaded = compressor mostly loading), frozen
    # at training time so evaluation never recomputes anything from pre-cutoff
    # data. Same 99.5th-percentile healthy rule; sparse modes fall back to the
    # global threshold.
    MIN_MODE_WINDOWS = 50
    healthy_risk = artifacts.risk_from_raw(-pipeline.decision_function(matrix.values), calibration)
    healthy_loaded = (matrix["COMP_duty"] < 0.5).values

    def _mode_thr(mask) -> float:
        if int(mask.sum()) >= MIN_MODE_WINDOWS:
            return float(np.quantile(healthy_risk[mask], 0.995))
        return threshold

    mode_conditioned_thresholds = {
        "loaded": _mode_thr(healthy_loaded),
        "idle": _mode_thr(~healthy_loaded),
        "loaded_calibration_windows": int(healthy_loaded.sum()),
        "idle_calibration_windows": int((~healthy_loaded).sum()),
        "min_windows_per_mode": MIN_MODE_WINDOWS,
        "fallback": "global threshold when a mode has too few healthy windows",
    }

    feature_spec = {
        "feature_version": features.FEATURE_VERSION,
        "window_minutes": window_minutes,
        "step_minutes": step_minutes,
        "nominal_cadence_seconds": features.NOMINAL_CADENCE_SECONDS,
        "min_coverage": features.MIN_COVERAGE,
        "feature_names": features.feature_names(),
        "analog_columns": dataset.ANALOG_COLUMNS,
        "digital_columns": dataset.DIGITAL_COLUMNS,
    }
    metadata = {
        "model_source": artifacts.MODEL_SOURCE,
        "task": (
            "unsupervised anomaly/failure DETECTION for an APU air compressor; "
            "score is NOT a calibrated probability (only 4 independent failure episodes) "
            "and no advance-warning lead time is claimed"
        ),
        "dataset": {
            "name": "UCI MetroPT-3 (id 791)",
            "license": "CC BY 4.0",
            "zip_sha256": dataset.RAW_ZIP_SHA256,
            "rows": int(len(df)),
            "time_start": str(df["timestamp"].iloc[0]),
            "time_end": str(df["timestamp"].iloc[-1]),
            "failure_events": dataset.FAILURE_EVENTS,
            "failure_events_source": dataset.FAILURE_EVENTS_SOURCE,
        },
        "healthy_train_interval": [train_start, train_end],
        "healthy_val_interval": [train_end, val_end],
        "n_train_windows": int(len(train_X)),
        "n_val_windows": int(len(val_X)),
        "calibration": calibration,
        "risk_semantics": "0.0 = healthy median, 0.5 = healthy 99.9th percentile, clipped to [0,1]",
        "threshold": threshold,
        "threshold_method": "99.5th percentile of held-out healthy validation risks (~0.5% healthy alert rate)",
        "baseline_feature_stats": baseline_stats,
        "baseline_threshold_z": baseline_threshold_z,
        "mode_conditioned_thresholds": mode_conditioned_thresholds,
        "model_params": {
            "algorithm": "IsolationForest",
            "n_estimators": 300,
            "random_state": 42,
            "scaler": "StandardScaler",
        },
        "sklearn_version": sklearn.__version__,
        "code_version": git_sha(),
        "trained_at": datetime.now().isoformat(),
        "training_seconds": None,  # filled below
    }
    metadata["training_seconds"] = round(time.time() - t0, 1)

    paths = artifacts.save_artifacts(pipeline, metadata, feature_spec, out_dir)
    print(f"[train] threshold={threshold:.3f} ({metadata['threshold_method']})")
    print(f"[train] artifacts: {[str(p) for p in paths.values()]}")
    print(f"[train] done in {metadata['training_seconds']}s")
    return metadata


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--data", type=Path, default=None, help="processed parquet override")
    parser.add_argument("--out", type=Path, default=None, help="artifact dir override")
    parser.add_argument("--train-start", default=DEFAULT_TRAIN_START)
    parser.add_argument("--train-end", default=DEFAULT_TRAIN_END)
    parser.add_argument("--val-end", default=DEFAULT_VAL_END)
    parser.add_argument("--window-minutes", type=int, default=features.WINDOW_MINUTES)
    parser.add_argument("--step-minutes", type=int, default=features.STEP_MINUTES)
    args = parser.parse_args(argv)
    train(
        args.data,
        args.out,
        args.train_start,
        args.train_end,
        args.val_end,
        args.window_minutes,
        args.step_minutes,
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
