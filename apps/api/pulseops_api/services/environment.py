"""The environment/outcome seam.

If `execute_approved_action` wrote healthy readings itself, every approved
action would "work" by construction. This module keeps three concerns
separate:

  1. executing an approved operational action  (tools.execute_approved_action)
  2. how the physical environment responds     (THIS module — the simulator)
  3. observing/verifying the result            (tools.verify_outcome + services/verification.py)

The agent never sees or chooses the outcome: `execute_approved_action` hands
the asset to the environment, the environment writes whatever telemetry its
configured response profile dictates, and verification later *observes* the
result the same way it observes any sensor data.

Outcome profiles (deterministic, for tests/eval/demo):

  success   readings settle to sensor baselines           → verify: passed
  partial   readings recover ~25% toward baseline         → verify: failed (still elevated)
  failure   readings hold at degraded levels              → verify: failed
  adverse   readings degrade a further ~30%               → verify: adverse
  sparse    only 2 post-action readings arrive            → verify: inconclusive (too little data)
  none      no post-action telemetry at all               → verify: inconclusive (waiting)

Selection: per-asset override via `set_outcome_profile` (used by tests, eval
scenarios, and the demo), else DEFAULT_PROFILE ("success" — preserves the
golden path). Overrides are in-process and explicitly simulation-only.

SIMULATOR NOTE (time fast-forward): post-action readings are written
with timestamps running FORWARD from execution time (a "simulated
fast-forward" of the next ~6h) so that verification — which reads the latest
telemetry — can observe the response without a real waiting period. This is
the one place simulated future time enters the system; it is isolated here on
purpose. Each executed action supplies a source-id correlation prefix so
verification cannot accidentally count another replay/action's telemetry.
See ADR-007/ADR-011.
"""

from __future__ import annotations

from datetime import timedelta

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..models import Sensor, SensorReading

PROFILES = ["success", "partial", "failure", "adverse", "sparse", "none"]
DEFAULT_PROFILE = "success"

# In-process per-asset overrides (tests/eval/demo). Cleared via clear_overrides().
_overrides: dict[int, str] = {}


def set_outcome_profile(asset_id: int, profile: str) -> None:
    if profile not in PROFILES:
        raise ValueError(f"unknown outcome profile '{profile}' (must be one of {PROFILES})")
    _overrides[asset_id] = profile


def get_outcome_profile(asset_id: int) -> str:
    return _overrides.get(asset_id, DEFAULT_PROFILE)


def clear_overrides() -> None:
    _overrides.clear()


def _latest_value(
    session: Session, sensor: Sensor, *, as_of=None, source_id_prefix: str | None = None
) -> float | None:
    query = (
        select(SensorReading)
        .where(SensorReading.sensor_id == sensor.id)
        .order_by(SensorReading.ts.desc())
        .limit(1)
    )
    if as_of is not None:
        query = query.where(SensorReading.ts <= as_of)
    if source_id_prefix is not None:
        query = query.where(SensorReading.source_id.startswith(source_id_prefix, autoescape=True))
    row = session.scalars(query).first()
    return row.value if row else None


def apply_maintenance_response(
    session: Session,
    asset_id: int,
    hours: int = 6,
    *,
    observation_as_of=None,
    observation_source_prefix: str | None = None,
    response_source_prefix: str | None = None,
) -> dict:
    """Write the environment's telemetry response to an executed maintenance
    action, according to the configured outcome profile.

    ``observation_*`` anchors the response to the evidence that triggered the
    action; ``response_source_prefix`` correlates every emitted reading to
    that action. Returns bookkeeping metadata (profile name is NOT exposed to
    the agent's tool results — the agent discovers the outcome via verification).
    """
    profile = get_outcome_profile(asset_id)
    # Recovery telemetry continues the asset's OWN clock (its observation
    # anchor) — wall time for seeded HVAC assets, source/sim time for
    # replayed assets. See failure_risk.observation_anchor.
    from .failure_risk import observation_anchor

    now = observation_as_of or observation_anchor(session, asset_id, observation_source_prefix)
    sensors = session.scalars(select(Sensor).where(Sensor.asset_id == asset_id)).all()

    if profile == "none":
        return {"readings_written": 0, "profile": profile}

    n_points = 2 if profile == "sparse" else hours * 2  # 30-min cadence
    written = 0
    for s in sensors:
        latest = _latest_value(session, s, as_of=now, source_id_prefix=observation_source_prefix)
        if latest is None:
            continue
        baseline = s.baseline_value
        for i in range(n_points):
            ts = now + timedelta(minutes=30 * (i + 1))
            if profile == "success":
                # exponential settle to within ~5% of baseline
                value = baseline * (1.0 + 0.05 * (0.6**i))
            elif profile == "partial":
                # recover a quarter of the deviation, then hold
                value = latest - 0.25 * (latest - baseline)
            elif profile == "adverse":
                # deviation keeps worsening THROUGH the response window
                # (~+60% by the end), so verification scoped to this action's
                # correlated telemetry observes deterioration, not a plateau
                value = baseline + (1.0 + 0.6 * (i + 1) / n_points) * (latest - baseline)
            else:  # failure, sparse: hold at degraded levels
                value = latest
            source_id = f"{response_source_prefix}{i}" if response_source_prefix else None
            session.add(
                SensorReading(sensor_id=s.id, ts=ts, value=round(value, 3), source_id=source_id)
            )
            written += 1
    session.flush()
    return {"readings_written": written, "profile": profile}
