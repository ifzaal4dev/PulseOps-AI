"""Facility-domain routes: dashboard, assets, sensor series, work orders,
inspections, technicians, case memory."""

from __future__ import annotations

from datetime import datetime, timedelta

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.orm import Session

from .. import schemas, serialize
from ..config import settings
from ..db import get_session
from ..models import (
    AgentRun,
    Asset,
    CaseMemory,
    Incident,
    Inspection,
    ModelPrediction,
    Sensor,
    SensorReading,
    ShadowAlert,
    Technician,
    WorkOrder,
)
from ..services import source_ids
from ..services.failure_risk import get_adapter
from ..services.shadow_trigger import PERSISTENCE_WINDOWS
from ..timeutil import utcnow

router = APIRouter(prefix="/api")


@router.get("/dashboard", response_model=schemas.DashboardOut)
def dashboard(session: Session = Depends(get_session)):
    assets = session.scalars(select(Asset)).all()
    summaries = sorted(
        (serialize.asset_summary(session, a) for a in assets),
        key=lambda s: -s.risk,
    )
    incidents = session.scalars(
        select(Incident)
        .where(Incident.status.in_(["active", "investigating"]))
        .order_by(Incident.detected_at.desc())
    ).all()
    runs = session.scalars(select(AgentRun).order_by(AgentRun.created_at.desc()).limit(5)).all()
    open_orders = session.scalars(
        select(WorkOrder).where(WorkOrder.status.in_(["open", "in_progress"]))
    ).all()

    apu = session.scalars(select(Asset).where(Asset.asset_type == "air_compressor")).first()
    flagship = None
    if apu is not None:
        latest = session.scalars(
            select(ModelPrediction)
            .where(
                ModelPrediction.asset_id == apu.id,
                ModelPrediction.model_source == "metropt3_iforest_v1",
            )
            .order_by(ModelPrediction.predicted_at.desc(), ModelPrediction.id.desc())
        ).first()
        pending = session.scalars(
            select(ShadowAlert)
            .where(
                ShadowAlert.asset_id == apu.id,
                ShadowAlert.status == "observed",
            )
            .order_by(ShadowAlert.id.desc())
        ).all()
        streak = 0
        if latest is not None:
            recent = session.scalars(
                select(ModelPrediction)
                .where(
                    ModelPrediction.replay_run_id == latest.replay_run_id,
                    ModelPrediction.asset_id == latest.asset_id,
                    ModelPrediction.model_source == latest.model_source,
                    ModelPrediction.window_end <= latest.window_end,
                )
                .order_by(ModelPrediction.window_end.desc())
                .limit(PERSISTENCE_WINDOWS)
            ).all()
            for prediction in recent:
                if not prediction.alert:
                    break
                streak += 1
        status = (
            "review_required"
            if pending
            else "building_persistence"
            if latest is not None and latest.alert
            else "monitoring"
            if latest is not None
            else "awaiting_replay"
        )
        flagship = schemas.FlagshipModelStatus(
            asset_id=apu.id,
            asset_tag=apu.tag,
            asset_name=apu.name,
            status=status,
            model_source=latest.model_source if latest else "metropt3_iforest_v1",
            feature_version=latest.feature_version if latest else "metropt3_fw_v1",
            policy_mode=latest.policy_mode if latest else "shadow",
            replay_run_id=latest.replay_run_id if latest else None,
            prediction_id=latest.id if latest else None,
            risk=latest.risk if latest else None,
            threshold=latest.threshold if latest else None,
            threshold_crossed=latest.alert if latest else False,
            persistence_windows=streak,
            persistence_required=PERSISTENCE_WINDOWS,
            operating_mode=latest.operating_mode if latest else "",
            window_end=latest.window_end if latest else None,
            pending_shadow_alerts=len(pending),
            latest_shadow_alert_id=pending[0].id if pending else None,
        )
    return schemas.DashboardOut(
        asset_count=len(assets),
        online_count=sum(1 for a in assets if a.status == "online"),
        degraded_count=sum(1 for a in assets if a.status in ("degraded", "offline")),
        active_incidents=[schemas.IncidentOut.model_validate(i) for i in incidents],
        top_risk_assets=summaries[:5],
        recent_runs=[schemas.AgentRunOut.model_validate(r) for r in runs],
        open_work_orders=len(open_orders),
        risk_source=get_adapter().model_source,
        flagship_model=flagship,
    )


@router.post("/demo/reset")
def reset_demo(body: schemas.DemoResetRequest):
    """Destructive demo reset: drop everything, migrate, and seed fresh.

    Same path as `python -m pulseops_api.init_db --reseed`. Disabled unless
    PULSEOPS_ENABLE_DEMO_RESET is true. The confirmation string only guards
    against a stray click; it isn't access control.
    """
    if not settings.enable_demo_reset:
        raise HTTPException(403, "demo reset is disabled on this deployment")
    if body.confirm != "RESET":
        raise HTTPException(400, "pass confirm='RESET' to reset the demo database")
    from ..init_db import reset_and_seed

    ids = reset_and_seed()
    return {"status": "reset", "seeded_ids": ids, "requested_by": body.operator}


@router.get("/assets", response_model=list[schemas.AssetSummary])
def list_assets(session: Session = Depends(get_session)):
    assets = session.scalars(select(Asset)).all()
    return sorted((serialize.asset_summary(session, a) for a in assets), key=lambda s: -s.risk)


def _asset_or_404(session: Session, asset_id: int) -> Asset:
    asset = session.get(Asset, asset_id)
    if asset is None:
        raise HTTPException(404, f"asset {asset_id} not found")
    return asset


@router.get("/assets/{asset_id}", response_model=schemas.AssetDetail)
def asset_detail(asset_id: int, session: Session = Depends(get_session)):
    asset = _asset_or_404(session, asset_id)
    summary = serialize.asset_summary(session, asset)
    return schemas.AssetDetail(
        **summary.model_dump(),
        manufacturer=asset.manufacturer,
        model=asset.model,
        install_date=asset.install_date,
        sensors=[schemas.SensorOut.model_validate(s) for s in asset.sensors],
    )


@router.get("/assets/{asset_id}/readings", response_model=list[schemas.SensorSeries])
def asset_readings(
    asset_id: int,
    hours: int = 72,
    until: datetime | None = None,
    replay_run_id: int | None = None,
    session: Session = Depends(get_session),
):
    """`until` lets replay views window on SOURCE time (e.g. the replay
    cursor) instead of the wall clock. `replay_run_id` restricts the series
    to that run's ingested telemetry so concurrent replays of the same source
    interval never mix in one chart."""
    _asset_or_404(session, asset_id)
    end = until or utcnow()
    cutoff = end - timedelta(hours=hours)
    series = []
    for sensor in session.scalars(select(Sensor).where(Sensor.asset_id == asset_id)):
        q = (
            select(SensorReading)
            .where(
                SensorReading.sensor_id == sensor.id,
                SensorReading.ts >= cutoff,
                SensorReading.ts <= end,
            )
            .order_by(SensorReading.ts)
        )
        if replay_run_id is not None:
            q = q.where(
                SensorReading.source_id.startswith(
                    source_ids.replay_prefix(replay_run_id), autoescape=True
                )
            )
        rows = session.scalars(q).all()
        series.append(
            schemas.SensorSeries(
                sensor=schemas.SensorOut.model_validate(sensor),
                points=[schemas.ReadingPoint(ts=r.ts, value=r.value) for r in rows],
            )
        )
    return series


@router.get("/assets/{asset_id}/risk")
def asset_risk(asset_id: int, session: Session = Depends(get_session)):
    _asset_or_404(session, asset_id)
    return get_adapter().predict(session, asset_id)


@router.get("/assets/{asset_id}/incidents", response_model=list[schemas.IncidentOut])
def asset_incidents(asset_id: int, session: Session = Depends(get_session)):
    rows = session.scalars(
        select(Incident).where(Incident.asset_id == asset_id).order_by(Incident.detected_at.desc())
    ).all()
    return [schemas.IncidentOut.model_validate(i) for i in rows]


@router.get("/assets/{asset_id}/inspections", response_model=list[schemas.InspectionOut])
def asset_inspections(asset_id: int, session: Session = Depends(get_session)):
    rows = session.scalars(
        select(Inspection).where(Inspection.asset_id == asset_id).order_by(Inspection.ts.desc())
    ).all()
    return [schemas.InspectionOut.model_validate(i) for i in rows]


@router.get("/work-orders", response_model=list[schemas.WorkOrderOut])
def work_orders(asset_id: int | None = None, session: Session = Depends(get_session)):
    q = select(WorkOrder).order_by(WorkOrder.created_at.desc())
    if asset_id is not None:
        q = q.where(WorkOrder.asset_id == asset_id)
    return [schemas.WorkOrderOut.model_validate(w) for w in session.scalars(q).all()]


@router.get("/technicians", response_model=list[schemas.TechnicianOut])
def technicians(session: Session = Depends(get_session)):
    return [
        schemas.TechnicianOut.model_validate(t) for t in session.scalars(select(Technician)).all()
    ]


@router.get("/case-memory", response_model=list[schemas.CaseMemoryOut])
def case_memory(asset_type: str | None = None, session: Session = Depends(get_session)):
    q = select(CaseMemory).order_by(CaseMemory.created_at.desc())
    if asset_type:
        q = q.where(CaseMemory.asset_type == asset_type)
    return [serialize.case_out(c) for c in session.scalars(q).all()]
