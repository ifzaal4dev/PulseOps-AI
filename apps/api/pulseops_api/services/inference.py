"""Online inference for the MetroPT `air_compressor` asset.

THE ONE-CODE-PATH RULE: features come from `pulseops_ml.features.
FeatureComputer.at` — the exact function used in offline training — over an
input frame reconstructed ENTIRELY from replayed database state (analog
sensor_readings + digital asset_operating_states). The source Parquet is
never consulted at scoring time, so no future value can leak in.

Provenance rules enforced here:
  * only assets of type `air_compressor` may be scored with the MetroPT
    artifact (ModelSourceError otherwise) — chillers/pumps stay on the
    clearly-labeled mock adapter;
  * a missing/corrupt artifact fails loudly (no silent fallback that could be
    mislabeled as metropt3_iforest_v1);
  * every stored ModelPrediction carries model_source, feature_version,
    threshold, trigger-policy mode/version, and window bounds;
  * per trigger_policy.json the policy is SHADOW: predictions are stored for
    observation and never open incidents (services/shadow_trigger.py).

Explanations are DESCRIPTIVE standardized feature deviations (|x-mean|/scale
using the training scaler) — IsolationForest provides no causal importances
and none are claimed.

Artifact location: PULSEOPS_ARTIFACTS_DIR env var, else the pulseops_ml
default (ml/model_artifacts). pandas/pulseops_ml are imported lazily so the
API core has no hard ML dependency until inference is actually used.
"""

from __future__ import annotations

import json
import os
from datetime import timedelta
from pathlib import Path

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from ..models import Asset, AssetOperatingState, ModelPrediction, ReplayRun, Sensor, SensorReading
from . import source_ids
from .events import emit_event


class ModelSourceError(Exception):
    """Wrong asset type for the artifact, or artifact unavailable/corrupt."""


def artifacts_dir() -> Path | None:
    env = os.environ.get("PULSEOPS_ARTIFACTS_DIR")
    return Path(env) if env else None


class OnlineInferenceService:
    """Scores completed feature windows for ONE air_compressor asset."""

    def __init__(self, session: Session, asset_id: int, artifacts_path: Path | None = None):
        from pulseops_ml.artifacts import load_artifacts  # lazy

        asset = session.get(Asset, asset_id)
        if asset is None or asset.asset_type != "air_compressor":
            raise ModelSourceError(
                f"the MetroPT artifact only scores air_compressor assets "
                f"(asset {asset_id} is '{getattr(asset, 'asset_type', 'missing')}'); "
                f"other assets remain on the labeled mock adapter"
            )
        self.session = session
        self.asset = asset
        path = artifacts_path or artifacts_dir()
        try:
            self.pipeline, self.metadata, self.feature_spec = load_artifacts(path)
        except (FileNotFoundError, RuntimeError, ValueError, json.JSONDecodeError) as e:
            raise ModelSourceError(
                f"MetroPT artifact unavailable or incompatible ({e}); refusing to score — "
                f"nothing may masquerade as metropt3_iforest_v1"
            ) from e

        policy_path = (path or self._default_artifacts_dir()) / "trigger_policy.json"
        if policy_path.exists():
            policy = json.loads(policy_path.read_text(encoding="utf-8"))
            self.policy_mode = policy.get("mode", "shadow")
            self.policy_version = policy.get("generated_at", "")
        else:
            self.policy_mode, self.policy_version = "shadow", "unspecified"

        self.window_minutes = self.feature_spec["window_minutes"]
        self.step_minutes = self.feature_spec["step_minutes"]
        self._sensors = {
            s.kind: s.id for s in session.scalars(select(Sensor).where(Sensor.asset_id == asset_id))
        }

    @staticmethod
    def _default_artifacts_dir() -> Path:
        from pulseops_ml.artifacts import ARTIFACTS_DIR

        return ARTIFACTS_DIR

    # -------------------------------------------------------- reconstruction

    def reconstruct_frame(self, start, end, replay_run_id: int | None = None):
        """Rebuild the 15-channel MetroPT frame for (start, end] from
        REPLAYED DB state only. Rows are keyed by source timestamp; only
        timestamps with all 7 analog readings AND a digital state row are
        usable (partial rows are dropped, counted by the caller via len)."""
        import pandas as pd  # lazy

        from pulseops_ml.dataset import ANALOG_COLUMNS, DIGITAL_COLUMNS

        reading_query = (
            select(SensorReading.ts, Sensor.kind, SensorReading.value)
            .join(Sensor, Sensor.id == SensorReading.sensor_id)
            .where(
                Sensor.asset_id == self.asset.id, SensorReading.ts > start, SensorReading.ts <= end
            )
        )
        state_query = select(AssetOperatingState).where(
            AssetOperatingState.asset_id == self.asset.id,
            AssetOperatingState.ts > start,
            AssetOperatingState.ts <= end,
        )
        if replay_run_id is not None:
            # Multiple demo replays may cover the same source timestamps. A
            # scoring run must never mix rows from their independent clocks.
            reading_query = reading_query.where(
                SensorReading.source_id.startswith(
                    source_ids.replay_prefix(replay_run_id), autoescape=True
                )
            )
            state_query = state_query.where(AssetOperatingState.replay_run_id == replay_run_id)
        readings = self.session.execute(reading_query).all()
        states = self.session.scalars(state_query).all()

        by_ts: dict = {}
        for ts, kind, value in readings:
            by_ts.setdefault(ts, {})[kind] = value
        for st in states:
            row = by_ts.setdefault(st.ts, {})
            for c in DIGITAL_COLUMNS:
                row[c] = getattr(st, c)

        complete = {
            ts: row
            for ts, row in by_ts.items()
            if all(c in row for c in ANALOG_COLUMNS + DIGITAL_COLUMNS)
        }
        if not complete:
            return pd.DataFrame(columns=["timestamp"] + ANALOG_COLUMNS + DIGITAL_COLUMNS)
        frame = pd.DataFrame([{"timestamp": ts, **row} for ts, row in sorted(complete.items())])
        # FeatureComputer expects naive timestamps (dataset convention);
        # DB returns aware UTC.
        frame["timestamp"] = pd.to_datetime(frame["timestamp"]).dt.tz_localize(None)
        return frame[["timestamp"] + ANALOG_COLUMNS + DIGITAL_COLUMNS]

    # --------------------------------------------------------------- scoring

    def score_window(self, window_end, replay_run_id: int | None = None) -> ModelPrediction | None:
        """Score one completed window. Returns the stored prediction, the
        existing one (idempotent), or None when coverage is insufficient —
        never a fabricated score."""
        import numpy as np  # lazy
        import pandas as pd

        from pulseops_ml.artifacts import risk_from_raw
        from pulseops_ml.features import FeatureComputer

        window_end = pd.Timestamp(window_end)
        naive_end = window_end.tz_localize(None) if window_end.tzinfo else window_end
        aware_end = window_end if window_end.tzinfo else window_end.tz_localize("UTC")
        window_start = aware_end - timedelta(minutes=self.window_minutes)

        existing = self.session.scalars(
            select(ModelPrediction).where(
                ModelPrediction.replay_run_id == replay_run_id,
                ModelPrediction.asset_id == self.asset.id,
                ModelPrediction.model_source == self.metadata["model_source"],
                ModelPrediction.window_end == aware_end.to_pydatetime(),
            )
        ).first()
        if existing is not None:
            return existing

        frame = self.reconstruct_frame(
            window_start.to_pydatetime(), aware_end.to_pydatetime(), replay_run_id
        )
        if frame.empty:
            return None
        feats = FeatureComputer(frame, window_minutes=self.window_minutes).at(naive_end)
        if feats is None:
            return None  # insufficient coverage: no prediction is fabricated

        names = self.feature_spec["feature_names"]
        X = np.array([[feats[n] for n in names]])
        risk = float(
            risk_from_raw(-self.pipeline.decision_function(X), self.metadata["calibration"])[0]
        )
        threshold = float(self.metadata["threshold"])

        # Descriptive deviations vs the training scaler (NOT causal importance).
        scaler = self.pipeline.named_steps["scaler"]
        z = np.abs((X[0] - scaler.mean_) / np.where(scaler.scale_ == 0, 1.0, scaler.scale_))
        top = np.argsort(-z)[:5]
        explanation = {
            "kind": "standardized_feature_deviations",
            "note": "descriptive |z| vs healthy-training scaler; NOT causal importance",
            "top": [
                {
                    "feature": names[i],
                    "abs_z": round(float(z[i]), 2),
                    "observed": round(float(X[0][i]), 4),
                    "healthy_mean": round(float(scaler.mean_[i]), 4),
                    "healthy_std": round(float(scaler.scale_[i]), 4),
                }
                for i in top
            ],
        }

        prediction = ModelPrediction(
            asset_id=self.asset.id,
            window_start=window_start.to_pydatetime(),
            window_end=aware_end.to_pydatetime(),
            risk=risk,  # full precision — offline/online parity is exact
            threshold=threshold,
            alert=risk >= threshold,
            shadow=(self.policy_mode == "shadow"),
            model_source=self.metadata["model_source"],
            feature_version=self.feature_spec["feature_version"],
            policy_mode=self.policy_mode,
            policy_version=self.policy_version,
            operating_mode="loaded" if feats["COMP_duty"] < 0.5 else "idle",
            explanation_json=json.dumps(explanation),
            replay_run_id=replay_run_id,
        )
        self.session.add(prediction)
        try:
            self.session.flush()
        except IntegrityError:
            self.session.rollback()  # concurrent scorer won the race — idempotent
            return self.session.scalars(
                select(ModelPrediction).where(
                    ModelPrediction.replay_run_id == replay_run_id,
                    ModelPrediction.asset_id == self.asset.id,
                    ModelPrediction.model_source == self.metadata["model_source"],
                    ModelPrediction.window_end == aware_end.to_pydatetime(),
                )
            ).first()
        emit_event(
            self.session,
            "model.prediction_stored",
            {
                "prediction_id": prediction.id,
                "asset_id": self.asset.id,
                "window_end": str(aware_end),
                "risk": prediction.risk,
                "alert": prediction.alert,
                "shadow": prediction.shadow,
                "model_source": prediction.model_source,
            },
        )
        return prediction

    def score_pending(self, replay_run: ReplayRun, limit: int = 50) -> list[ModelPrediction]:
        """Score grid-aligned windows completed up to the replay cursor that
        have no prediction yet. Also feeds each stored prediction through the
        shadow-trigger state machine."""
        import pandas as pd

        from .shadow_trigger import process_prediction

        if replay_run.cursor is None:
            return []
        latest = self.session.scalars(
            select(ModelPrediction)
            .where(
                ModelPrediction.replay_run_id == replay_run.id,
                ModelPrediction.asset_id == self.asset.id,
                ModelPrediction.model_source == self.metadata["model_source"],
            )
            .order_by(ModelPrediction.window_end.desc())
        ).first()
        step = timedelta(minutes=self.step_minutes)
        start_from = (
            (latest.window_end + step)
            if latest
            else (
                pd.Timestamp(replay_run.source_start)
                .ceil(f"{self.step_minutes}min")
                .to_pydatetime()
            )
        )
        stored: list[ModelPrediction] = []
        window_end = start_from
        while window_end <= replay_run.cursor and len(stored) < limit:
            prediction = self.score_window(window_end, replay_run_id=replay_run.id)
            if prediction is not None and prediction.id is not None:
                stored.append(prediction)
                process_prediction(self.session, prediction)
            window_end = window_end + step
        self.session.commit()
        return stored
