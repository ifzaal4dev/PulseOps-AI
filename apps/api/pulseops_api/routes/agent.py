"""Agent-domain routes: runs, stepping, approval decisions, verification.

The approval endpoints are the ONLY path that flips an action to
approved/rejected — they represent the human in the loop.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.orm import Session

from .. import schemas, serialize
from ..db import get_session
from ..models import (
    AgentAction,
    AgentRun,
    Asset,
    DiagnosticEvidenceRequest,
    VerificationResult,
)
from ..services import approval, diagnostic_evidence
from ..services.agent_loop import AgentEngine, DuplicateRunError

router = APIRouter(prefix="/api/agent")


@router.get("/runs", response_model=list[schemas.AgentRunOut])
def list_runs(session: Session = Depends(get_session)):
    runs = session.scalars(select(AgentRun).order_by(AgentRun.created_at.desc())).all()
    return [schemas.AgentRunOut.model_validate(r) for r in runs]


@router.post("/runs", response_model=schemas.AgentRunDetail)
def start_run(body: schemas.StartRunRequest, session: Session = Depends(get_session)):
    if session.get(Asset, body.asset_id) is None:
        raise HTTPException(404, f"asset {body.asset_id} not found")
    try:
        engine = AgentEngine.start_run(session, body.asset_id, trigger=body.trigger)
    except DuplicateRunError as e:
        raise HTTPException(409, str(e)) from e
    return serialize.run_detail(session, engine.run)


def _run_or_404(session: Session, run_id: int) -> AgentRun:
    run = session.get(AgentRun, run_id)
    if run is None:
        raise HTTPException(404, f"run {run_id} not found")
    return run


@router.get("/runs/{run_id}", response_model=schemas.AgentRunDetail)
def get_run(run_id: int, session: Session = Depends(get_session)):
    return serialize.run_detail(session, _run_or_404(session, run_id))


@router.post("/runs/{run_id}/advance", response_model=schemas.AgentRunDetail)
def advance_run(
    run_id: int,
    body: schemas.AdvanceRunRequest | None = None,
    session: Session = Depends(get_session),
):
    _run_or_404(session, run_id)
    engine = AgentEngine(session, run_id)
    mode = body.mode if body else "until_blocked"
    run = engine.advance() if mode == "one_phase" else engine.run_until_blocked()
    return serialize.run_detail(session, run)


@router.get("/runs/{run_id}/verification", response_model=schemas.VerificationOut)
def run_verification(run_id: int, session: Session = Depends(get_session)):
    _run_or_404(session, run_id)
    # A replanned run has one row per attempt; the latest is the current one
    # (same rule as serialize.run_detail).
    vr = session.scalars(
        select(VerificationResult)
        .where(VerificationResult.run_id == run_id)
        .order_by(VerificationResult.id.desc())
    ).first()
    if vr is None:
        raise HTTPException(404, f"run {run_id} has no verification result yet")
    return serialize.verification_out(vr)


@router.get(
    "/runs/{run_id}/evidence-requests", response_model=list[schemas.DiagnosticEvidenceRequestOut]
)
def list_evidence_requests(run_id: int, session: Session = Depends(get_session)):
    _run_or_404(session, run_id)
    rows = session.scalars(
        select(DiagnosticEvidenceRequest)
        .where(
            DiagnosticEvidenceRequest.run_id == run_id,
        )
        .order_by(DiagnosticEvidenceRequest.id)
    ).all()
    return [serialize.evidence_request_out(row) for row in rows]


@router.post(
    "/evidence-requests/{request_id}/submit", response_model=schemas.DiagnosticEvidenceRequestOut
)
def submit_evidence(
    request_id: int,
    body: schemas.EvidenceSubmissionRequest,
    session: Session = Depends(get_session),
):
    try:
        request = diagnostic_evidence.submit_observation(
            session,
            request_id,
            result=body.result,
            submitted_by=body.submitted_by,
            source=body.source,
            observed_at=body.observed_at,
        )
    except diagnostic_evidence.DiagnosticEvidenceError as exc:
        raise HTTPException(409, str(exc)) from exc
    return serialize.evidence_request_out(request)


@router.get("/actions", response_model=list[schemas.AgentActionOut])
def list_actions(status: str | None = None, session: Session = Depends(get_session)):
    q = select(AgentAction).order_by(AgentAction.proposed_at.desc())
    if status:
        q = q.where(AgentAction.status == status)
    return [serialize.action_out(a) for a in session.scalars(q).all()]


@router.post("/actions/{action_id}/approve", response_model=schemas.AgentActionOut)
def approve(
    action_id: int,
    body: schemas.ActionDecisionRequest | None = None,
    session: Session = Depends(get_session),
):
    try:
        action = approval.approve_action(
            session, action_id, (body or schemas.ActionDecisionRequest()).decided_by
        )
    except approval.ApprovalError as e:
        raise HTTPException(409, str(e)) from e
    return serialize.action_out(action)


@router.post("/reasoning-test", response_model=schemas.LlmConnectivityTestOut)
def reasoning_connectivity_test():
    """Manual 'Test configured LLM' check.

    Exercises the production provider registry, snapshot rendering,
    prompt/schema versions, validator, and deterministic fallback against a
    labeled SYNTHETIC/DEMO snapshot. Deliberately takes no DB session: it can
    never create an incident, work order, approval, action, or run.
    """
    from ..config import settings
    from ..services.reasoning import connectivity

    return schemas.LlmConnectivityTestOut.model_validate(
        connectivity.run_connectivity_test(settings)
    )


@router.post("/actions/{action_id}/reject", response_model=schemas.AgentActionOut)
def reject(
    action_id: int,
    body: schemas.ActionDecisionRequest | None = None,
    session: Session = Depends(get_session),
):
    try:
        action = approval.reject_action(
            session, action_id, (body or schemas.ActionDecisionRequest()).decided_by
        )
    except approval.ApprovalError as e:
        raise HTTPException(409, str(e)) from e
    return serialize.action_out(action)
