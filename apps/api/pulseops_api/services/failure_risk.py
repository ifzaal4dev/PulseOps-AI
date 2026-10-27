"""Failure-risk prediction adapter.

`MockFailureRiskAdapter` is a deterministic heuristic over recent sensor
statistics. It scores the synthetic facility assets and the simulated
before/after of maintenance verification. Every response carries
`model_source = "mock_demo_v0"` so nothing downstream can mistake it for a
trained model.

The real MetroPT-3 model (metropt3_iforest_v1) is served separately by
services/inference.py and scores only the replayed air compressor.
"""

from __future__ import annotations

from datetime import timedelta
from typing import Protocol

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from ..models import Sensor, SensorReading
from ..timeutil import utcnow

MOCK_MODEL_SOURCE = "mock_demo_v0"


class FailureRiskAdapter(Protocol):
    """Serving contract for facility risk scoring. Any adapter must return
    this shape, with model_source set to its own identifier."""

    def predict(
        self, session: Session, asset_id: int, *, as_of=None, source_id_prefix: str | None = None
    ) -> dict: ...
    def feature_importance(self, session: Session, asset_id: int) -> dict: ...


def observation_anchor(session: Session, asset_id: int, source_id_prefix: str | None = None):
    """The asset's 'now' for all operational reads.

    Replayed assets live on SOURCE time (e.g. MetroPT 2020): their newest
    reading — which also advances when simulated maintenance writes recovery
    telemetry — is the honest observation point. Assets without readings fall
    back to the wall clock. Diagnosis, risk scoring, and verification all
    window on this same anchor.
    """
    query = (
        select(func.max(SensorReading.ts))
        .join(Sensor, Sensor.id == SensorReading.sensor_id)
        .where(Sensor.asset_id == asset_id)
    )
    if source_id_prefix is not None:
        query = query.where(SensorReading.source_id.startswith(source_id_prefix, autoescape=True))
    latest = session.scalar(query)
    return latest or utcnow()


def sensor_stats(
    session: Session,
    asset_id: int,
    window_hours: int = 24,
    as_of=None,
    source_id_prefix: str | None = None,
) -> list[dict]:
    """Trusted numeric statistics per sensor for the window ending at `as_of`
    (default: the asset's observation anchor — see observation_anchor)."""
    as_of = as_of or observation_anchor(session, asset_id, source_id_prefix)
    cutoff = as_of - timedelta(hours=window_hours)
    stats = []
    sensors = session.scalars(select(Sensor).where(Sensor.asset_id == asset_id)).all()
    for s in sensors:
        query = (
            select(SensorReading)
            .where(SensorReading.sensor_id == s.id, SensorReading.ts >= cutoff)
            .order_by(SensorReading.ts)
        )
        if source_id_prefix is not None:
            query = query.where(
                SensorReading.source_id.startswith(source_id_prefix, autoescape=True)
            )
        rows = session.scalars(query).all()
        if not rows:
            continue
        values = [r.value for r in rows]
        n = len(values)
        first_half = values[: max(1, n // 2)]
        second_half = values[max(1, n // 2) :] or first_half
        # Trailing sub-window by TIME (count-based slicing breaks under mixed
        # cadences, e.g. 10s replay rows vs 30min recovery telemetry).
        recent_cutoff = as_of - timedelta(hours=max(1, window_hours // 4))
        recent = [r.value for r in rows if r.ts > recent_cutoff] or values[-1:]
        stats.append(
            {
                "sensor_id": s.id,
                "kind": s.kind,
                "unit": s.unit,
                "baseline": s.baseline_value,
                "latest": values[-1],
                "mean": round(sum(values) / n, 3),
                "recent_mean": round(sum(recent) / len(recent), 3),
                "min": min(values),
                "max": max(values),
                "trend": round(
                    (sum(second_half) / len(second_half)) - (sum(first_half) / len(first_half)), 3
                ),
                "count": n,
            }
        )
    return stats


def numeric_signals(stats: list[dict]) -> dict[str, float]:
    """Convert trusted sensor stats into diagnosis signal tokens with strengths (0..1)."""
    signals: dict[str, float] = {}

    def add(name: str, strength: float):
        if strength > 0:
            signals[name] = round(min(1.0, max(signals.get(name, 0.0), strength)), 3)

    for st in stats:
        base = st["baseline"] or 1e-9
        ratio = st["latest"] / base
        kind = st["kind"]
        if kind == "vibration_rms":
            if ratio >= 1.4:
                add("vibration_high", min(1.0, (ratio - 1.4) / 1.6))
            if st["trend"] > 0.15 * base:
                add("vibration_ramp", min(1.0, st["trend"] / base))
            if ratio <= 1.1:
                add("vibration_low", 0.5)
        elif kind in ("discharge_pressure", "steam_pressure"):
            if ratio <= 0.9:
                add("pressure_drop", min(1.0, (0.9 - ratio) / 0.25))
        elif kind == "power_kw":
            if ratio >= 1.06:
                add("power_increase", min(1.0, (ratio - 1.06) / 0.15))
            if ratio >= 1.15 or ratio <= 0.85:
                add("power_anomaly", 0.6)
        elif kind in ("supply_temp", "return_temp"):
            if ratio >= 1.12:
                add("supply_temp_high", min(1.0, (ratio - 1.12) / 0.2))
        elif kind == "flow_rate":
            if ratio <= 0.92:
                add("flow_drop", min(1.0, (0.92 - ratio) / 0.2))
        # APU air-compressor channels (MetroPT replay asset). Trusted numeric
        # signals derived from replayed telemetry only.
        elif kind in ("Reservoirs", "TP3"):
            if ratio <= 0.92:
                add("air_pressure_drop", min(1.0, (0.92 - ratio) / 0.2))
        elif kind == "Oil_temperature":
            if ratio >= 1.12:
                add("oil_temp_high", min(1.0, (ratio - 1.12) / 0.25))
        elif kind == "Motor_current":
            # baseline is the mixed idle/loaded nominal; a sustained TRAILING
            # mean well above it means the compressor is loading hard — the
            # trailing window (recent_mean) lets recovery clear the signal
            if st["recent_mean"] / base >= 1.5:
                add("motor_current_high", min(1.0, (st["recent_mean"] / base - 1.5) / 1.5))
        elif kind == "TP2":
            # TP2 sits near 0 bar at idle; a high trailing mean = high duty cycle
            if st["recent_mean"] / base >= 2.0:
                add("compressor_duty_high", min(1.0, (st["recent_mean"] / base - 2.0) / 3.0))
    return signals


class MockFailureRiskAdapter:
    """MOCK/DEMO adapter — deterministic heuristic, clearly labeled.

    Risk model: small base risk plus bounded contributions from anomalous
    vibration, pressure, temperature, and power. Monotonic in each anomaly so
    before/after verification behaves sensibly.
    """

    model_source = MOCK_MODEL_SOURCE

    def predict(
        self, session: Session, asset_id: int, *, as_of=None, source_id_prefix: str | None = None
    ) -> dict:
        stats = sensor_stats(session, asset_id, as_of=as_of, source_id_prefix=source_id_prefix)
        signals = numeric_signals(stats)
        risk = 0.05
        risk += 0.50 * signals.get("vibration_high", 0.0)
        risk += 0.15 * signals.get("vibration_ramp", 0.0)
        risk += 0.15 * signals.get("pressure_drop", 0.0)
        risk += 0.08 * signals.get("supply_temp_high", 0.0)
        risk += 0.07 * signals.get("power_increase", 0.0)
        risk += 0.10 * signals.get("flow_drop", 0.0)
        # APU air-compressor signals (facility risk view; still mock_demo_v0 —
        # the real MetroPT model's shadow predictions live in model_predictions)
        risk += 0.25 * signals.get("air_pressure_drop", 0.0)
        risk += 0.20 * signals.get("compressor_duty_high", 0.0)
        risk += 0.15 * signals.get("motor_current_high", 0.0)
        risk += 0.10 * signals.get("oil_temp_high", 0.0)
        risk = round(min(0.98, risk), 3)
        return {
            "asset_id": asset_id,
            "risk": risk,
            "risk_band": "critical" if risk >= 0.7 else "elevated" if risk >= 0.4 else "normal",
            "model_source": self.model_source,
            "signals": signals,
            "stats": stats,
            "disclaimer": (
                "Mock demo heuristic for synthetic HVAC assets — not the real "
                "MetroPT-3-trained detector (metropt3_iforest_v1, shadow-only)."
            ),
        }

    def feature_importance(self, session: Session, asset_id: int) -> dict:
        pred = self.predict(session, asset_id)
        weights = {
            "vibration_high": 0.50,
            "vibration_ramp": 0.15,
            "pressure_drop": 0.15,
            "supply_temp_high": 0.08,
            "power_increase": 0.07,
            "flow_drop": 0.10,
            "air_pressure_drop": 0.25,
            "compressor_duty_high": 0.20,
            "motor_current_high": 0.15,
            "oil_temp_high": 0.10,
        }
        contributions = [
            {"feature": name, "contribution": round(weights.get(name, 0.0) * strength, 3)}
            for name, strength in pred["signals"].items()
        ]
        contributions.sort(key=lambda c: -c["contribution"])
        return {
            "asset_id": asset_id,
            "model_source": self.model_source,
            "importance": contributions,
        }


def get_adapter() -> MockFailureRiskAdapter:
    """Adapter factory. Always the labelled mock; the trained MetroPT model
    is served by services/inference.py, not through this adapter."""
    return MockFailureRiskAdapter()
