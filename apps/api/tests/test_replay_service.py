"""The persisted replay service.

Constructed source rows only (no dataset/pandas needed) except the final
local-mode smoke test, which runs only when the real processed Parquet exists.
"""

from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from sqlalchemy import select

from pulseops_api.models import OutboxEvent, SensorReading
from pulseops_api.services import replay
from pulseops_api.services.replay import ReplayError, ReplayService, create_replay_run

T0 = datetime(2020, 2, 1, tzinfo=UTC)


def make_rows(start_offset_s: int, n: int, cadence_s: int = 10):
    return [
        (
            T0 + timedelta(seconds=start_offset_s + i * cadence_s),
            {
                "TP2": 8.0 + i * 0.01,
                "TP3": 8.9,
                "H1": 0.1,
                "DV_pressure": 0.05,
                "Reservoirs": 8.9,
                "Oil_temperature": 60.0,
                "Motor_current": 5.5,
                "COMP": 0.0,
                "DV_eletric": 1.0,
                "Towers": 1.0,
                "MPG": 1.0,
                "LPS": 0.0,
                "Pressure_switch": 1.0,
                "Oil_level": 1.0,
                "Caudal_impulses": 1.0,
            },
        )
        # 7 analog -> sensor readings; 8 digital -> one AssetOperatingState row
        for i in range(n)
    ]


def new_run(session, ids, span_s=600):
    return create_replay_run(
        session,
        ids["apu_id"],
        source_start=T0,
        source_end=T0 + timedelta(seconds=span_s),
        dataset_checksum="test",
        speed=120.0,
    )


def apu_reading_count(session, ids):
    return len(
        session.scalars(select(SensorReading).where(SensorReading.source_id.isnot(None))).all()
    )


def test_chunk_ingestion_advances_clock_and_maps_channels(seeded):
    session, ids = seeded
    run = new_run(session, ids)
    svc = ReplayService(session, run.id)
    svc.start()

    result = svc.ingest_chunk(make_rows(10, 12))
    assert result["inserted"] == 12 * 8  # 7 analog readings + 1 operating-state row per source row
    assert result["skipped"] == 0
    assert run.cursor == T0 + timedelta(seconds=10 + 11 * 10)
    assert run.sim_time == run.cursor
    assert run.status == "running"

    # source_id carries run + source timestamp (idempotency key).
    a_reading = session.scalars(
        select(SensorReading).where(SensorReading.source_id.isnot(None))
    ).first()
    assert a_reading.source_id.startswith(f"replay:{run.id}:2020-02-01")


def test_duplicate_chunk_redelivery_inserts_nothing(seeded):
    session, ids = seeded
    run = new_run(session, ids)
    svc = ReplayService(session, run.id)
    svc.start()

    rows = make_rows(10, 12)
    svc.ingest_chunk(rows)
    before = apu_reading_count(session, ids)

    result = svc.ingest_chunk(rows)  # exact redelivery
    assert result["inserted"] == 0
    assert result["skipped"] == 12
    assert apu_reading_count(session, ids) == before

    # Overlapping chunk: only genuinely new rows land.
    result = svc.ingest_chunk(make_rows(10, 18))  # 12 old + 6 new
    assert result["inserted"] == 6 * 8
    assert result["skipped"] == 12


def test_restart_resumes_exactly_from_persisted_cursor(seeded):
    session, ids = seeded
    run = new_run(session, ids)
    run_id = run.id
    svc = ReplayService(session, run_id)
    svc.start()
    svc.ingest_chunk(make_rows(10, 6))
    cursor_before = run.cursor
    del svc  # process dies

    session.expunge_all()
    svc2 = ReplayService(session, run_id)  # fresh instance, DB only
    assert svc2.run.status == "running"
    assert svc2.run.cursor == cursor_before
    result = svc2.ingest_chunk(make_rows(10, 12))  # redelivers 6, adds 6
    assert result["inserted"] == 6 * 8
    assert result["skipped"] == 6


def test_pause_blocks_ingestion_and_resume_continues(seeded):
    session, ids = seeded
    run = new_run(session, ids)
    svc = ReplayService(session, run.id)
    svc.start()
    svc.ingest_chunk(make_rows(10, 3))

    svc.pause()
    with pytest.raises(ReplayError, match="not running"):
        svc.ingest_chunk(make_rows(40, 3))

    svc.start()  # resume
    assert svc.ingest_chunk(make_rows(40, 3))["inserted"] == 3 * 8


def test_completion_and_outbox_lifecycle(seeded):
    session, ids = seeded
    run = new_run(session, ids, span_s=100)
    svc = ReplayService(session, run.id)
    svc.start()
    svc.ingest_chunk(make_rows(0, 11))  # reaches ts=100 == source_end

    assert run.status == "completed"
    topics = [e.topic for e in session.scalars(select(OutboxEvent)).all()]
    for expected in [
        "replay.created",
        "replay.started",
        "replay.chunk_ingested",
        "replay.completed",
    ]:
        assert expected in topics

    with pytest.raises(ReplayError):
        svc.start()  # completed runs don't restart


def test_reset_is_deterministic(seeded):
    session, ids = seeded
    run = new_run(session, ids)
    svc = ReplayService(session, run.id)
    svc.start()
    svc.ingest_chunk(make_rows(10, 12))
    assert apu_reading_count(session, ids) > 0

    svc.reset()
    assert run.status == "ready"
    assert run.cursor is None and run.sim_time is None
    assert apu_reading_count(session, ids) == 0

    # Re-running after reset reproduces the identical state.
    svc.start()
    result = svc.ingest_chunk(make_rows(10, 12))
    assert result["inserted"] == 12 * 8


REAL_PARQUET = (
    Path(__file__).resolve().parents[3] / "ml" / "datasets" / "processed" / "metropt3.parquet"
)


@pytest.mark.skipif(not REAL_PARQUET.exists(), reason="real MetroPT parquet not ingested locally")
def test_local_mode_loads_and_ingests_real_metropt_slice(seeded):
    """Local-only smoke: a 30-minute slice of the real dataset flows through
    the loader into sensor_readings. Skipped in CI (no dataset)."""
    session, ids = seeded
    start = datetime(2020, 2, 1, 0, 0, tzinfo=UTC)
    end = datetime(2020, 2, 1, 0, 30, tzinfo=UTC)
    rows = replay.load_metropt_rows(start, end, REAL_PARQUET)
    assert 150 <= len(rows) <= 200  # ~10s cadence over 30 min (inclusive bounds)

    run = create_replay_run(
        session, ids["apu_id"], source_start=start, source_end=end, dataset_checksum="aab991a9"
    )
    svc = ReplayService(session, run.id)
    svc.start()
    result = svc.ingest_chunk(rows)
    assert result["inserted"] == len(rows) * 8
    # The dataset's last row rarely lands exactly on source_end; the cursor
    # must sit within one cadence of it (completion semantics are covered by
    # the constructed-row tests above).
    assert run.cursor is not None
    assert (end - run.cursor).total_seconds() <= 20
    assert run.status in ("running", "completed")
