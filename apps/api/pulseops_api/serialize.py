"""ORM → API-schema serialization (parses *_json TEXT columns into dicts)."""

from __future__ import annotations

import json

from sqlalchemy import select
from sqlalchemy.orm import Session

from . import schemas
from .models import (
    AgentAction,
    AgentRun,
    AgentStep,
    AgentToolCall,
    Asset,
    CaseMemory,
    DiagnosticEvidenceObservation,
    DiagnosticEvidenceRequest,
    VerificationResult,
)
from .services.failure_risk import get_adapter


def asset_summary(session: Session, asset: Asset, with_risk: bool = True) -> schemas.AssetSummary:
    room = asset.room
    pred = get_adapter().predict(session, asset.id) if with_risk else None
    return schemas.AssetSummary(
        id=asset.id,
        tag=asset.tag,
        name=asset.name,
        asset_type=asset.asset_type,
        criticality=asset.criticality,
        status=asset.status,
        location=f"{room.floor.name} / {room.name}",
        risk=pred["risk"] if pred else 0.0,
        risk_source=pred["model_source"] if pred else "",
    )


def step_out(step: AgentStep) -> schemas.AgentStepOut:
    return schemas.AgentStepOut(
        id=step.id,
        seq=step.seq,
        phase=step.phase,
        status=step.status,
        reasoning_summary=step.reasoning_summary,
        output=json.loads(step.output_json or "{}"),
        started_at=step.started_at,
        completed_at=step.completed_at,
    )


def tool_call_out(tc: AgentToolCall) -> schemas.AgentToolCallOut:
    return schemas.AgentToolCallOut(
        id=tc.id,
        step_id=tc.step_id,
        seq=tc.seq,
        tool_name=tc.tool_name,
        arguments=json.loads(tc.arguments_json or "{}"),
        result_summary=tc.result_summary,
        trust_label=tc.trust_label,
        injection_flag=tc.injection_flag,
        created_at=tc.created_at,
    )


def action_out(a: AgentAction) -> schemas.AgentActionOut:
    return schemas.AgentActionOut(
        id=a.id,
        run_id=a.run_id,
        action_type=a.action_type,
        status=a.status,
        payload=json.loads(a.payload_json or "{}"),
        work_order_id=a.work_order_id,
        proposed_at=a.proposed_at,
        decided_at=a.decided_at,
        decided_by=a.decided_by,
        executed_at=a.executed_at,
    )


def verification_out(v: VerificationResult) -> schemas.VerificationOut:
    return schemas.VerificationOut(
        id=v.id,
        run_id=v.run_id,
        action_id=v.action_id,
        risk_before=v.risk_before,
        risk_after=v.risk_after,
        risk_delta=v.risk_delta,
        outcome=v.outcome,
        outcome_reason=v.outcome_reason,
        passed=v.passed,
        details=json.loads(v.details_json or "{}"),
        verified_at=v.verified_at,
    )


def case_out(c: CaseMemory) -> schemas.CaseMemoryOut:
    return schemas.CaseMemoryOut(
        id=c.id,
        asset_id=c.asset_id,
        asset_type=c.asset_type,
        title=c.title,
        evidence_domain=c.evidence_domain,
        symptoms=json.loads(c.symptoms_json or "[]"),
        root_cause=c.root_cause,
        resolution=c.resolution,
        outcome=c.outcome,
        risk_reduction=c.risk_reduction,
        narrative=c.narrative,
        source_run_id=c.source_run_id,
        provenance=c.provenance,
        validation_status=c.validation_status,
        validated_by=c.validated_by,
        created_at=c.created_at,
    )


def evidence_observation_out(
    observation: DiagnosticEvidenceObservation,
) -> schemas.DiagnosticEvidenceObservationOut:
    return schemas.DiagnosticEvidenceObservationOut(
        id=observation.id,
        submitted_by=observation.submitted_by,
        source=observation.source,
        observed_at=observation.observed_at,
        result=json.loads(observation.result_json or "{}"),
        signal=json.loads(observation.signal_json or "{}"),
        created_at=observation.created_at,
    )


def evidence_request_out(
    request: DiagnosticEvidenceRequest,
) -> schemas.DiagnosticEvidenceRequestOut:
    return schemas.DiagnosticEvidenceRequestOut(
        id=request.id,
        run_id=request.run_id,
        asset_id=request.asset_id,
        test_id=request.test_id,
        status=request.status,
        requested_by=request.requested_by,
        rationale=request.rationale,
        hypotheses=json.loads(request.hypotheses_json or "[]"),
        result_schema=json.loads(request.result_schema_json or "{}"),
        request_round=request.request_round,
        created_at=request.created_at,
        resolved_at=request.resolved_at,
        observation=(
            evidence_observation_out(request.observation) if request.observation else None
        ),
    )


def run_detail(session: Session, run: AgentRun) -> schemas.AgentRunDetail:
    verification = session.scalars(
        select(VerificationResult)
        .where(VerificationResult.run_id == run.id)
        .order_by(VerificationResult.id.desc())  # latest attempt's verification
    ).first()
    return schemas.AgentRunDetail(
        id=run.id,
        asset_id=run.asset_id,
        incident_id=run.incident_id,
        status=run.status,
        current_phase=run.current_phase,
        iterations_used=run.iterations_used,
        max_iterations=run.max_iterations,
        attempts_used=run.attempts_used,
        max_attempts=run.max_attempts,
        trigger=run.trigger,
        evidence_domain=run.evidence_domain,
        summary=run.summary,
        created_at=run.created_at,
        updated_at=run.updated_at,
        steps=[step_out(s) for s in run.steps],
        tool_calls=[tool_call_out(tc) for tc in run.tool_calls],
        actions=[action_out(a) for a in sorted(run.actions, key=lambda x: x.id)],
        evidence_requests=[evidence_request_out(r) for r in run.evidence_requests],
        verification=verification_out(verification) if verification else None,
    )
