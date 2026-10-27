"""Accelerated historical-telemetry replay.

A ReplayService is constructed from a ReplayRun id and NOTHING else — the DB
row is the sole source of truth (cursor, status, speed), so a fresh instance
after a process restart resumes exactly where the last one stopped. The same
service runs inline (local mode) today and inside a worker later.

Three clocks (see models.ReplayRun):
  source/event time = timestamps inside the historical dataset (cursor,
  sim_time); ingestion time = wall clock when rows are written (reading rows'
  insertion, outbox created_at). `speed` records the intended acceleration
  (source seconds per wall second) for pacing by a caller/worker — the
  service itself is pull-based and paces nothing.

Idempotency: every reading carries source_id = "replay:<run_id>:<source_ts>",
covered by the (sensor_id, source_id) unique constraint. Chunk ingestion
filters rows at/before the persisted cursor, and readings + cursor advance
commit in ONE transaction, so redelivered or replayed chunks insert nothing.

Only the seven analog MetroPT channels become sensor readings; the digital
channels are operating-state inputs consumed by the feature pipeline (slice
D reads them from the processed dataset, the source of truth, rather than
round-tripping them through sensor_readings).
"""

from __future__ import annotations

from collections.abc import Iterable
from datetime import datetime

from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from ..models import (
    AssetOperatingState,
    ModelPrediction,
    ReplayRun,
    Sensor,
    SensorReading,
    ShadowAlert,
)
from ..timeutil import utcnow
from . import source_ids
from .events import emit_event

# The eight MetroPT digital channels — persisted as AssetOperatingState rows
# (operating-state inputs), never as sensors.
DIGITAL_CHANNELS = [
    "COMP",
    "DV_eletric",
    "Towers",
    "MPG",
    "LPS",
    "Pressure_switch",
    "Oil_level",
    "Caudal_impulses",
]

# (source_ts, {channel_kind: value, ...}) — channels not mapped to a sensor
# of the target asset are ignored. source_ts must be timezone-aware UTC
# (dataset timestamps are naive local recordings; the loader pins them to UTC
# by convention, matching TZDateTime storage).
SourceRow = tuple[datetime, dict[str, float]]


class ReplayError(Exception):
    pass


def create_replay_run(
    session: Session,
    asset_id: int,
    source_start,
    source_end,
    dataset_name: str = "metropt3",
    dataset_checksum: str = "",
    speed: float = 60.0,
    model_source: str = "",
    feature_version: str = "",
) -> ReplayRun:
    run = ReplayRun(
        dataset_name=dataset_name,
        dataset_checksum=dataset_checksum,
        asset_id=asset_id,
        source_start=source_start,
        source_end=source_end,
        speed=speed,
        status="ready",
        model_source=model_source,
        feature_version=feature_version,
    )
    session.add(run)
    session.flush()
    emit_event(
        session,
        "replay.created",
        {"replay_run_id": run.id, "asset_id": asset_id, "dataset": dataset_name},
    )
    session.commit()
    return run


class ReplayService:
    """Drives one ReplayRun. Stateless between calls — everything is in the DB."""

    def __init__(self, session: Session, replay_run_id: int):
        self.session = session
        self.run = session.get(ReplayRun, replay_run_id)
        if self.run is None:
            raise ReplayError(f"replay run {replay_run_id} not found")
        self._sensor_by_kind: dict[str, int] = {
            s.kind: s.id
            for s in session.scalars(select(Sensor).where(Sensor.asset_id == self.run.asset_id))
        }

    # ------------------------------------------------------------- lifecycle

    def start(self) -> ReplayRun:
        if self.run.status not in ("ready", "paused"):
            raise ReplayError(f"cannot start from status '{self.run.status}'")
        self.run.status = "running"
        emit_event(
            self.session,
            "replay.started",
            {"replay_run_id": self.run.id, "cursor": str(self.run.cursor)},
        )
        self.session.commit()
        return self.run

    def pause(self) -> ReplayRun:
        if self.run.status != "running":
            raise ReplayError(f"cannot pause from status '{self.run.status}'")
        self.run.status = "paused"
        emit_event(
            self.session,
            "replay.paused",
            {"replay_run_id": self.run.id, "cursor": str(self.run.cursor)},
        )
        self.session.commit()
        return self.run

    def reset(self) -> ReplayRun:
        """Deterministic reset of this replay and every derived observation.

        Promoted alerts are retained because they own incidents/investigations;
        operators should create a new replay (now safely isolated by run id) or
        explicitly reseed the demo instead of orphaning an audit trail.
        """
        promoted = self.session.scalars(
            select(ShadowAlert).where(
                ShadowAlert.replay_run_id == self.run.id,
                ShadowAlert.status == "promoted",
            )
        ).first()
        if promoted is not None:
            raise ReplayError(
                f"replay run {self.run.id} has promoted shadow alert #{promoted.id}; "
                "create a new replay or reseed the demo to preserve its audit trail"
            )

        prefix = source_ids.replay_prefix(self.run.id)
        # Alerts reference predictions, so derived state is deleted from the
        # outside in before telemetry and clocks are rewound.
        self.session.execute(delete(ShadowAlert).where(ShadowAlert.replay_run_id == self.run.id))
        self.session.execute(
            delete(ModelPrediction).where(ModelPrediction.replay_run_id == self.run.id)
        )
        self.session.execute(
            delete(SensorReading).where(
                SensorReading.sensor_id.in_(list(self._sensor_by_kind.values())),
                SensorReading.source_id.like(prefix + "%"),
            )
        )
        self.session.execute(
            delete(AssetOperatingState).where(AssetOperatingState.replay_run_id == self.run.id)
        )
        self.run.cursor = None
        self.run.sim_time = None
        self.run.status = "ready"
        self.run.last_error = ""
        emit_event(self.session, "replay.reset", {"replay_run_id": self.run.id})
        self.session.commit()
        return self.run

    # ------------------------------------------------------------- ingestion

    def ingest_chunk(self, rows: Iterable[SourceRow]) -> dict:
        """Insert one chunk of source rows and advance the cursor atomically.

        Rows at/before the persisted cursor or outside the source interval are
        skipped (redelivery-safe). Readings and the cursor commit in one
        transaction; a crash rolls back both, so resume is always exact.
        """
        run = self.run
        if run.status != "running":
            raise ReplayError(f"replay run {run.id} is '{run.status}', not running")

        inserted = skipped = 0
        max_ts: datetime | None = None
        try:
            for source_ts, channels in rows:
                if (
                    source_ts < run.source_start
                    or source_ts > run.source_end
                    or (run.cursor is not None and source_ts <= run.cursor)
                ):
                    skipped += 1
                    continue
                source_id = source_ids.replay_reading_id(run.id, source_ts)
                for kind, value in channels.items():
                    sensor_id = self._sensor_by_kind.get(kind)
                    if sensor_id is None:
                        continue  # digital/unknown channels are handled below
                    self.session.add(
                        SensorReading(
                            sensor_id=sensor_id,
                            ts=source_ts,
                            value=float(value),
                            source_id=source_id,
                        )
                    )
                    inserted += 1
                # Digital operating state: one row per source row, atomic with
                # the analog readings and the cursor (same transaction).
                digital = {c: channels[c] for c in DIGITAL_CHANNELS if c in channels}
                if len(digital) == len(DIGITAL_CHANNELS):
                    self.session.add(
                        AssetOperatingState(
                            replay_run_id=run.id,
                            asset_id=run.asset_id,
                            ts=source_ts,
                            sim_time=source_ts,
                            source_id=source_id,
                            **{c: float(digital[c]) for c in DIGITAL_CHANNELS},
                        )
                    )
                    inserted += 1
                max_ts = source_ts if max_ts is None or source_ts > max_ts else max_ts

            if max_ts is not None:
                run.cursor = max_ts
                run.sim_time = max_ts
            if run.cursor is not None and run.cursor >= run.source_end:
                run.status = "completed"
                emit_event(
                    self.session,
                    "replay.completed",
                    {"replay_run_id": run.id, "cursor": str(run.cursor)},
                )
            run.updated_at = utcnow()
            emit_event(
                self.session,
                "replay.chunk_ingested",
                {
                    "replay_run_id": run.id,
                    "inserted": inserted,
                    "skipped": skipped,
                    "cursor": str(run.cursor),
                },
            )
            self.session.commit()
        except Exception as e:
            self.session.rollback()
            run.status = "failed"
            run.last_error = str(e)[:2000]
            self.session.commit()
            raise
        return {
            "inserted": inserted,
            "skipped": skipped,
            "cursor": run.cursor,
            "status": run.status,
        }


# --------------------------------------------------------------------------
# Real-dataset adapter (local mode). Lazy pandas/pulseops_ml import so the
# API core stays dependency-free; CI tests use constructed rows instead.
# --------------------------------------------------------------------------


def load_metropt_rows(source_start, source_end, parquet_path=None) -> list[SourceRow]:
    """Read a bounded slice of the processed MetroPT-3 Parquet as SourceRows
    (all 15 channels: 7 analog → sensor readings, 8 digital → operating
    state). Requires the pulseops-ml package and the locally ingested
    dataset."""
    from datetime import UTC

    import pandas as pd  # noqa: F401 — lazy: replay core must not need pandas

    from pulseops_ml.dataset import ANALOG_COLUMNS, DIGITAL_COLUMNS, load_processed

    df = load_processed(parquet_path)
    ts = pd.DatetimeIndex(df["timestamp"])
    lo = (
        pd.Timestamp(source_start).tz_localize(None)
        if pd.Timestamp(source_start).tzinfo
        else pd.Timestamp(source_start)
    )
    hi = (
        pd.Timestamp(source_end).tz_localize(None)
        if pd.Timestamp(source_end).tzinfo
        else pd.Timestamp(source_end)
    )
    window = df[(ts >= lo) & (ts <= hi)]
    return [
        (
            row.timestamp.to_pydatetime().replace(tzinfo=UTC),
            {c: float(getattr(row, c)) for c in ANALOG_COLUMNS + DIGITAL_COLUMNS},
        )
        for row in window.itertuples(index=False)
    ]
