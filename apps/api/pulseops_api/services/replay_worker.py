"""Replay pacing worker.

Wraps the pull-based ReplayService with source-time pacing: each tick
converts elapsed wall-clock time into a source-time span via the run's
`speed` (source seconds per wall second), pulls that bounded slice from the
row provider, ingests it, and runs online inference for any newly completed
feature windows.

Invariants:
  * the DATABASE is the source of truth — the pacer re-reads run status every
    tick, so pause takes effect within one tick and a fresh pacer built from
    the run id resumes exactly at the persisted cursor;
  * ticks are short (TICK_SECONDS) — no long uninterruptible sleeps;
  * chunks are bounded (MAX_SOURCE_SPAN_S per tick);
  * `step()` is the deterministic inline unit used by tests and API "advance"
    calls; `run_loop()` is the same logic paced by a sleep function.

Row provider: callable (start, end) -> list[SourceRow]. Production uses
replay.load_metropt_rows (processed Parquet); tests inject synthetic rows.
"""

from __future__ import annotations

import time
from collections.abc import Callable
from datetime import timedelta

from sqlalchemy.orm import Session

from ..models import ReplayRun
from .replay import ReplayService, SourceRow

TICK_SECONDS = 0.5
MAX_SOURCE_SPAN_S = 3600.0  # never ingest more than an hour of source time per tick

RowProvider = Callable[[object, object], list[SourceRow]]


class ReplayPacer:
    def __init__(
        self,
        session: Session,
        replay_run_id: int,
        row_provider: RowProvider,
        inference_service=None,
    ):
        self.session = session
        self.service = ReplayService(session, replay_run_id)
        self.row_provider = row_provider
        self.inference = inference_service  # optional OnlineInferenceService

    @property
    def run(self) -> ReplayRun:
        return self.service.run

    def source_span(self, wall_seconds: float) -> float:
        """Source-time seconds corresponding to `wall_seconds` at run speed."""
        return min(wall_seconds * self.run.speed, MAX_SOURCE_SPAN_S)

    def step(self, wall_seconds: float = TICK_SECONDS) -> dict:
        """One deterministic pacing step: ingest speed*wall_seconds of source
        time from the cursor (or source_start) and score completed windows."""
        self.session.refresh(self.run)  # DB is the source of truth (pause/restart)
        if self.run.status != "running":
            return {"status": self.run.status, "inserted": 0, "predictions": 0}

        start = self.run.cursor or (self.run.source_start - timedelta(seconds=1))
        span = self.source_span(wall_seconds)
        end = min(start + timedelta(seconds=span), self.run.source_end)
        try:
            rows = self.row_provider(start, end)
        except Exception as e:
            self.run.status = "failed"
            self.run.last_error = f"row provider: {e}"[:2000]
            self.session.commit()
            raise
        try:
            result = self.service.ingest_chunk(rows)
        except Exception:
            raise  # ReplayService already persisted status=failed + last_error
        predictions = 0
        if self.inference is not None and self.run.cursor is not None:
            predictions = len(self.inference.score_pending(self.run))
        return {
            "status": self.run.status,
            "inserted": result["inserted"],
            "skipped": result["skipped"],
            "cursor": self.run.cursor,
            "sim_time": self.run.sim_time,
            "predictions": predictions,
        }

    def run_loop(self, max_ticks: int | None = None, sleep_fn=time.sleep) -> str:
        """Paced loop: same step logic, short interruptible sleeps. Returns the
        final run status. A worker process calls this; tests pass a no-op
        sleep_fn and a tick bound."""
        ticks = 0
        while True:
            state = self.step(TICK_SECONDS)
            if state["status"] in ("completed", "failed", "paused", "ready"):
                return state["status"]
            ticks += 1
            if max_ticks is not None and ticks >= max_ticks:
                return state["status"]
            sleep_fn(TICK_SECONDS)
