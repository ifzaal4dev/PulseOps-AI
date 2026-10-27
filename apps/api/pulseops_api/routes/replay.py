"""Replay, online-inference, and shadow-alert routes.

The promote/dismiss endpoints are the ONLY path from a shadow alert to an
incident — an explicit human decision creating a clearly-labeled
SYNTHETIC/DEMO incident. Nothing here approves maintenance.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.orm import Session

from .. import schemas
from ..db import get_session
from ..models import Asset, ModelPrediction, ReplayRun, ShadowAlert
from ..services import replay as replay_service
from ..services import shadow_trigger
from ..services.inference import ModelSourceError, OnlineInferenceService
from ..services.model_reporting import ModelReportError, load_flagship_evaluation
from ..services.replay import ReplayError, ReplayService, create_replay_run
from ..services.replay_worker import ReplayPacer

router = APIRouter(prefix="/api")

METROPT_DATA_START = datetime(2020, 2, 1, tzinfo=UTC)
METROPT_DATA_END = datetime(2020, 9, 1, tzinfo=UTC)


def _prediction_out(p: ModelPrediction) -> schemas.ModelPredictionOut:
    out = schemas.ModelPredictionOut.model_validate(p)
    out.explanation = json.loads(p.explanation_json or "{}")
    return out


@router.get("/replay", response_model=list[schemas.ReplayRunOut])
def list_replays(session: Session = Depends(get_session)):
    runs = session.scalars(select(ReplayRun).order_by(ReplayRun.id.desc())).all()
    return [schemas.ReplayRunOut.model_validate(r) for r in runs]


@router.post("/replay", response_model=schemas.ReplayRunOut)
def create_replay(body: schemas.CreateReplayRequest, session: Session = Depends(get_session)):
    apu = session.scalars(select(Asset).where(Asset.asset_type == "air_compressor")).first()
    if apu is None:
        raise HTTPException(404, "no air_compressor asset seeded")
    start = body.source_start or METROPT_DATA_START
    if start.tzinfo is None:
        start = start.replace(tzinfo=UTC)
    # Useful errors: the dataset covers 2020-02-01 .. 2020-09-01.
    if not (METROPT_DATA_START <= start < METROPT_DATA_END):
        raise HTTPException(
            422,
            f"source_start {start.isoformat()} is outside the MetroPT-3 range "
            f"[{METROPT_DATA_START.isoformat()}, {METROPT_DATA_END.isoformat()})",
        )
    run = create_replay_run(
        session,
        apu.id,
        source_start=start,
        source_end=start + timedelta(hours=body.duration_hours),
        dataset_checksum="aab991a970e58210",
        speed=body.speed,
        model_source="metropt3_iforest_v1",
        feature_version="metropt3_fw_v1",
    )
    return schemas.ReplayRunOut.model_validate(run)


def _run_or_404(session: Session, replay_id: int) -> ReplayRun:
    run = session.get(ReplayRun, replay_id)
    if run is None:
        raise HTTPException(404, f"replay run {replay_id} not found")
    return run


@router.get("/replay/{replay_id}", response_model=schemas.ReplayRunOut)
def get_replay(replay_id: int, session: Session = Depends(get_session)):
    return schemas.ReplayRunOut.model_validate(_run_or_404(session, replay_id))


@router.post("/replay/{replay_id}/start", response_model=schemas.ReplayRunOut)
@router.post("/replay/{replay_id}/resume", response_model=schemas.ReplayRunOut)
def start_replay(replay_id: int, session: Session = Depends(get_session)):
    _run_or_404(session, replay_id)
    try:
        run = ReplayService(session, replay_id).start()
    except ReplayError as e:
        raise HTTPException(409, str(e)) from e
    return schemas.ReplayRunOut.model_validate(run)


@router.post("/replay/{replay_id}/pause", response_model=schemas.ReplayRunOut)
def pause_replay(replay_id: int, session: Session = Depends(get_session)):
    _run_or_404(session, replay_id)
    try:
        run = ReplayService(session, replay_id).pause()
    except ReplayError as e:
        raise HTTPException(409, str(e)) from e
    return schemas.ReplayRunOut.model_validate(run)


@router.post("/replay/{replay_id}/reset", response_model=schemas.ReplayRunOut)
def reset_replay(replay_id: int, session: Session = Depends(get_session)):
    _run_or_404(session, replay_id)
    try:
        run = ReplayService(session, replay_id).reset()
    except ReplayError as e:
        raise HTTPException(409, str(e)) from e
    return schemas.ReplayRunOut.model_validate(run)


@router.post("/replay/{replay_id}/advance")
def advance_replay(
    replay_id: int,
    body: schemas.AdvanceReplayRequest | None = None,
    session: Session = Depends(get_session),
):
    """Inline pacing step: ingest speed×wall_seconds of source time from the
    processed MetroPT Parquet and score completed windows (shadow mode)."""
    run = _run_or_404(session, replay_id)
    inference = None
    inference_note = ""
    try:
        inference = OnlineInferenceService(session, run.asset_id)
    except (ModelSourceError, ImportError) as e:
        inference_note = f"inference unavailable: {e}"

    def provider(start, end):
        try:
            return replay_service.load_metropt_rows(start, end)
        except FileNotFoundError as e:
            raise HTTPException(
                409,
                f"processed MetroPT dataset not available: {e} — run "
                f"`python -m pulseops_ml.ingest` first",
            ) from e

    pacer = ReplayPacer(session, replay_id, provider, inference_service=inference)
    try:
        state = pacer.step(wall_seconds=(body or schemas.AdvanceReplayRequest()).wall_seconds)
    except HTTPException:
        raise
    except ReplayError as e:
        raise HTTPException(409, str(e)) from e
    state["cursor"] = str(state.get("cursor"))
    state["sim_time"] = str(state.get("sim_time"))
    if inference_note:
        state["inference_note"] = inference_note
    return state


@router.get("/assets/{asset_id}/predictions", response_model=list[schemas.ModelPredictionOut])
def prediction_history(
    asset_id: int,
    limit: int = 100,
    replay_run_id: int | None = None,
    session: Session = Depends(get_session),
):
    q = select(ModelPrediction).where(ModelPrediction.asset_id == asset_id)
    if replay_run_id is not None:
        q = q.where(ModelPrediction.replay_run_id == replay_run_id)
    rows = session.scalars(q.order_by(ModelPrediction.window_end.desc()).limit(limit)).all()
    return [_prediction_out(p) for p in rows]


@router.get("/shadow-alerts", response_model=list[schemas.ShadowAlertOut])
def list_shadow_alerts(
    replay_run_id: int | None = None,
    status: str | None = None,
    session: Session = Depends(get_session),
):
    q = select(ShadowAlert)
    if replay_run_id is not None:
        q = q.where(ShadowAlert.replay_run_id == replay_run_id)
    if status is not None:
        q = q.where(ShadowAlert.status == status)
    rows = session.scalars(q.order_by(ShadowAlert.id.desc())).all()
    return [schemas.ShadowAlertOut.model_validate(a) for a in rows]


@router.get("/trigger-policy", response_model=schemas.TriggerPolicyOut)
def trigger_policy():
    """Runtime shadow-trigger policy constants (services/shadow_trigger.py)."""
    return schemas.TriggerPolicyOut(
        persistence_windows=shadow_trigger.PERSISTENCE_WINDOWS,
        hysteresis_low_factor=shadow_trigger.LOW_FACTOR,
        cooldown_minutes=shadow_trigger.COOLDOWN_MINUTES,
        mode="shadow",
        explanation=(
            "Shadow means the model would have triggered an incident, but "
            "automatic triggering is disabled because its evaluation did not "
            "meet the readiness bar."
        ),
    )


@router.get("/model-evaluation/metropt3_iforest_v1", response_model=schemas.FlagshipEvaluationOut)
def flagship_model_evaluation():
    """Source-backed holdout metrics generated by the ML evaluation job."""
    try:
        return schemas.FlagshipEvaluationOut.model_validate(load_flagship_evaluation())
    except ModelReportError as exc:
        raise HTTPException(503, str(exc)) from exc


@router.post("/shadow-alerts/{alert_id}/promote", response_model=schemas.IncidentOut)
def promote_alert(
    alert_id: int,
    body: schemas.ActionDecisionRequest | None = None,
    session: Session = Depends(get_session),
):
    operator = (body or schemas.ActionDecisionRequest()).decided_by
    try:
        incident = shadow_trigger.promote_shadow_alert(session, alert_id, operator)
    except shadow_trigger.ShadowTriggerError as e:
        raise HTTPException(409, str(e)) from e
    return schemas.IncidentOut.model_validate(incident)


@router.post("/shadow-alerts/{alert_id}/promote-investigate")
def promote_and_investigate_alert(
    alert_id: int,
    body: schemas.ActionDecisionRequest | None = None,
    session: Session = Depends(get_session),
):
    """Idempotent, crash-recoverable operator flow: exactly one synthetic
    incident + one investigation advanced to the human-approval gate.
    Retries and concurrent clicks converge (CAS promotion + unique-index-
    backed run creation); the two commits are recoverable, not one atomic
    transaction."""
    operator = (body or schemas.ActionDecisionRequest()).decided_by
    try:
        incident, run = shadow_trigger.promote_and_investigate(session, alert_id, operator)
    except shadow_trigger.ShadowTriggerError as e:
        raise HTTPException(409, str(e)) from e
    return {
        "incident": schemas.IncidentOut.model_validate(incident).model_dump(mode="json"),
        "run_id": run.id,
        "run_status": run.status,
    }


@router.post("/shadow-alerts/{alert_id}/dismiss", response_model=schemas.ShadowAlertOut)
def dismiss_alert(
    alert_id: int,
    body: schemas.ActionDecisionRequest | None = None,
    session: Session = Depends(get_session),
):
    operator = (body or schemas.ActionDecisionRequest()).decided_by
    try:
        alert = shadow_trigger.dismiss_shadow_alert(session, alert_id, operator)
    except shadow_trigger.ShadowTriggerError as e:
        raise HTTPException(409, str(e)) from e
    return schemas.ShadowAlertOut.model_validate(alert)
