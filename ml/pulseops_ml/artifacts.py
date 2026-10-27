"""Model artifact contract.

A trained flagship model is four files in ml/model_artifacts/ (binaries
gitignored, JSON committed):

  metropt3_iforest_v1.joblib   sklearn Pipeline (scaler + IsolationForest)
  feature_spec.json            feature version/window/step/names — the serving contract
  metadata.json                provenance: dataset checksums, healthy intervals,
                               calibration, threshold + method, code version, timings
  (ml/reports/metropt3_eval.*) evaluation report, written by evaluate.py

`risk_from_raw` is the single normalization used offline and online: raw
IsolationForest scores → [0, 1] anomaly-risk score. It is an ANOMALY SCORE,
not a calibrated probability — with only 4 independent failure episodes,
probability calibration would be indefensible.
"""

from __future__ import annotations

import json
from pathlib import Path

import joblib
import numpy as np

ML_DIR = Path(__file__).resolve().parent.parent
ARTIFACTS_DIR = ML_DIR / "model_artifacts"
REPORTS_DIR = ML_DIR / "reports"

MODEL_SOURCE = "metropt3_iforest_v1"


def risk_from_raw(raw_scores: np.ndarray, calibration: dict) -> np.ndarray:
    """Map raw anomaly scores (-decision_function, higher = worse) to [0, 1].

    Anchors from HEALTHY TRAINING windows: median -> 0.0, 99.9th percentile
    -> 0.5. The alert threshold is therefore interpretable as "how far beyond
    the healthy extreme" a window sits.
    """
    med = calibration["healthy_median"]
    hi = calibration["healthy_q999"]
    span = max(hi - med, 1e-9)
    return np.clip(0.5 * (np.asarray(raw_scores) - med) / span, 0.0, 1.0)


def save_artifacts(
    pipeline, metadata: dict, feature_spec: dict, out_dir: Path | None = None
) -> dict[str, Path]:
    out_dir = out_dir or ARTIFACTS_DIR
    out_dir.mkdir(parents=True, exist_ok=True)
    paths = {
        "model": out_dir / f"{metadata['model_source']}.joblib",
        "metadata": out_dir / "metadata.json",
        "feature_spec": out_dir / "feature_spec.json",
    }
    joblib.dump(pipeline, paths["model"])
    paths["metadata"].write_text(json.dumps(metadata, indent=2), encoding="utf-8")
    paths["feature_spec"].write_text(json.dumps(feature_spec, indent=2), encoding="utf-8")
    return paths


def load_artifacts(artifacts_dir: Path | None = None):
    """Returns (pipeline, metadata, feature_spec). Raises with instructions
    when no trained artifact exists."""
    d = artifacts_dir or ARTIFACTS_DIR
    metadata_path = d / "metadata.json"
    if not metadata_path.exists():
        raise FileNotFoundError(
            f"No trained artifact in {d}. Train one first:\n"
            f"  python -m pulseops_ml.ingest && python -m pulseops_ml.train\n"
            f"(see ml/README.md; the app's mock adapter remains labeled mock_demo_v0)"
        )
    metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
    expected_sklearn = metadata.get("sklearn_version")
    if expected_sklearn:
        import sklearn

        if sklearn.__version__ != expected_sklearn:
            raise RuntimeError(
                f"Artifact requires scikit-learn {expected_sklearn}, but runtime is "
                f"{sklearn.__version__}. Refusing unsafe joblib unpickle; install the "
                "recorded version or retrain the artifact."
            )
    feature_spec = json.loads((d / "feature_spec.json").read_text(encoding="utf-8"))
    pipeline = joblib.load(d / f"{metadata['model_source']}.joblib")
    return pipeline, metadata, feature_spec
