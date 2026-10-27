"""Explicit agent tools.

Every capability the agent has is one of these functions — there is no side
channel. Each returns a ToolResult with:

  result       JSON-serializable payload persisted to agent_tool_calls.result_json
  summary      one-line human summary for the audit trail / UI
  trust_label  evidence.TRUSTED for system-of-record structured data,
               evidence.UNTRUSTED for free-text document content

Free-text results are wrapped item-by-item in evidence envelopes
(evidence.make_evidence) so injection flags travel with the content.

Write tools (`propose_work_order`, `execute_approved_action`) are the only
ones that mutate facility state. `execute_approved_action` raises
ApprovalRequiredError unless a human approved the action — this is the hard
approval gate and must never be weakened.

Retrieval is keyword-based and DB-backed. The signatures are the contract a
richer backend (for example embedding retrieval) would have to keep.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field

from sqlalchemy import func, select, update
from sqlalchemy.orm import Session

from ..models import (
    AgentAction,
    AgentRun,
    Asset,
    CaseMemory,
    Inspection,
    ManualChunk,
    ModelPrediction,
    Sensor,
    SensorReading,
    ShadowAlert,
    Technician,
    VerificationResult,
    WorkOrder,
)
from ..timeutil import utcnow
from . import diagnostic_evidence, environment, evidence, source_ids
from .events import emit_event
from .evidence import TRUSTED, UNTRUSTED
from .failure_risk import get_adapter, numeric_signals, sensor_stats
from .verification import classify_outcome


class ApprovalRequiredError(Exception):
    """Raised when execution is attempted on an action a human has not approved."""


class ToolError(Exception):
    pass


@dataclass
class ToolResult:
    result: dict
    summary: str
    trust_label: str = TRUSTED
    injection_flag: bool = field(default=False)


def _asset(session: Session, asset_id: int) -> Asset:
    asset = session.get(Asset, asset_id)
    if asset is None:
        raise ToolError(f"asset {asset_id} not found")
    return asset


# --------------------------------------------------------------------------
# Read tools
# --------------------------------------------------------------------------


def query_case_memory(
    session: Session,
    asset_id: int,
    symptoms: list[str],
    evidence_domain: str = "synthetic_facility",
) -> ToolResult:
    """Retrieve prior postmortems for this asset type ranked by symptom overlap.

    Matching is keyword overlap; an embedding-based version should keep this
    signature.
    """
    asset = _asset(session, asset_id)
    cases = session.scalars(
        select(CaseMemory).where(
            CaseMemory.asset_type == asset.asset_type,
            CaseMemory.evidence_domain.in_([evidence_domain, "generic_reference"]),
        )
    ).all()
    symptom_terms = {t for s in symptoms for t in s.lower().split()}
    matches = []
    for c in cases:
        case_symptoms = json.loads(c.symptoms_json or "[]")
        case_terms = {t for s in case_symptoms for t in s.lower().split()}
        overlap = len(symptom_terms & case_terms)
        if overlap > 0:
            matches.append((overlap, c))
    matches.sort(key=lambda pair: -pair[0])
    items = [
        {
            "case_id": c.id,
            "title": c.title,
            "symptoms": json.loads(c.symptoms_json or "[]"),
            "root_cause": c.root_cause,
            "resolution": c.resolution,
            "risk_reduction": c.risk_reduction,
            "symptom_overlap": overlap,
            "asset_id": c.asset_id,
            # Validation labels — the diagnosis engine may positively weight
            # ONLY validated_success precedents (docs/agentic-loop.md).
            "provenance": c.provenance,
            "validation_status": c.validation_status,
            "evidence_domain": c.evidence_domain,
            "narrative": evidence.make_evidence("case_memory", f"case_memory:{c.id}", c.narrative),
        }
        for overlap, c in matches[:5]
    ]
    flagged = any(i["narrative"]["injection_flags"] for i in items)
    return ToolResult(
        result={"matches": items, "queried_symptoms": symptoms},
        summary=(
            f"case_memory: {len(items)} precedent(s) for {asset.asset_type} "
            f"[domain={evidence_domain}]; "
        )
        + (f"top: '{items[0]['root_cause']}'" if items else "no match"),
        trust_label=UNTRUSTED,
        injection_flag=flagged,
    )


def get_asset_profile(session: Session, asset_id: int) -> ToolResult:
    asset = _asset(session, asset_id)
    room = asset.room
    result = {
        "id": asset.id,
        "tag": asset.tag,
        "name": asset.name,
        "asset_type": asset.asset_type,
        "manufacturer": asset.manufacturer,
        "model": asset.model,
        "install_date": asset.install_date,
        "criticality": asset.criticality,
        "status": asset.status,
        "location": f"{room.floor.building.name} / {room.floor.name} / {room.name}",
        "sensors": [
            {"id": s.id, "kind": s.kind, "unit": s.unit, "baseline": s.baseline_value}
            for s in asset.sensors
        ],
    }
    return ToolResult(
        result, f"profile: {asset.name} ({asset.asset_type}, criticality={asset.criticality})"
    )


def get_recent_sensor_readings(
    session: Session, asset_id: int, time_window_hours: int = 24, agent_run_id: int | None = None
) -> ToolResult:
    trigger = _promoted_trigger_prediction(session, agent_run_id)
    source_prefix = (
        source_ids.replay_prefix(trigger.replay_run_id)
        if trigger and trigger.replay_run_id
        else None
    )
    stats = sensor_stats(
        session,
        asset_id,
        time_window_hours,
        as_of=trigger.window_end if trigger else None,
        source_id_prefix=source_prefix,
    )
    signals = numeric_signals(stats)
    anomalies = [
        f"{st['kind']}: latest {st['latest']}{st['unit']} vs baseline {st['baseline']}{st['unit']}"
        for st in stats
        if st["baseline"] and abs(st["latest"] / st["baseline"] - 1) > 0.15
    ]
    return ToolResult(
        result={
            "window_hours": time_window_hours,
            "stats": stats,
            "signals": signals,
            "anomalies": anomalies,
        },
        summary=f"sensors: {len(stats)} series, signals={sorted(signals) or 'none'}",
    )


def get_open_work_orders(
    session: Session, asset_id: int, evidence_domain: str = "synthetic_facility"
) -> ToolResult:
    orders = session.scalars(
        select(WorkOrder).where(
            WorkOrder.asset_id == asset_id,
            WorkOrder.status.in_(["open", "in_progress"]),
            WorkOrder.evidence_domain.in_([evidence_domain, "generic_reference"]),
        )
    ).all()
    items = [
        {
            "work_order_id": w.id,
            "title": w.title,
            "status": w.status,
            "priority": w.priority,
            "evidence_domain": w.evidence_domain,
            "description": w.description,
            "notes": evidence.make_evidence("work_order_note", f"work_orders:{w.id}", w.notes),
        }
        for w in orders
    ]
    flagged = any(i["notes"]["injection_flags"] for i in items)
    return ToolResult(
        result={"open_work_orders": items},
        summary=f"work_orders: {len(items)} open [domain={evidence_domain}]",
        trust_label=UNTRUSTED,
        injection_flag=flagged,
    )


def get_recent_inspections(
    session: Session, asset_id: int, limit: int = 5, evidence_domain: str = "synthetic_facility"
) -> ToolResult:
    rows = session.scalars(
        select(Inspection)
        .where(
            Inspection.asset_id == asset_id,
            Inspection.evidence_domain.in_([evidence_domain, "generic_reference"]),
        )
        .order_by(Inspection.ts.desc())
        .limit(limit)
    ).all()
    items = [
        {
            "inspection_id": i.id,
            "ts": i.ts.isoformat(),
            "summary": i.summary,
            "evidence_domain": i.evidence_domain,
            "notes": evidence.make_evidence("inspection_note", f"inspections:{i.id}", i.notes),
        }
        for i in rows
    ]
    flagged = any(i["notes"]["injection_flags"] for i in items)
    return ToolResult(
        result={"inspections": items},
        summary=f"inspections: {len(items)} recent [domain={evidence_domain}]",
        trust_label=UNTRUSTED,
        injection_flag=flagged,
    )


def search_maintenance_manuals(
    session: Session,
    query: str,
    asset_type: str | None = None,
    evidence_domain: str = "synthetic_facility",
) -> ToolResult:
    """Keyword retrieval over manual chunks."""
    q = select(ManualChunk).where(
        ManualChunk.evidence_domain.in_([evidence_domain, "generic_reference"])
    )
    if asset_type:
        q = q.where(ManualChunk.asset_type == asset_type)
    chunks = session.scalars(q).all()
    terms = [t for t in query.lower().split() if len(t) > 2]
    scored = []
    for ch in chunks:
        text = f"{ch.section} {ch.content}".lower()
        score = sum(text.count(t) for t in terms)
        if score > 0:
            scored.append((score, ch))
    scored.sort(key=lambda pair: -pair[0])
    items = [
        {
            "chunk_id": ch.id,
            "doc_title": ch.doc_title,
            "section": ch.section,
            "score": score,
            "evidence_domain": ch.evidence_domain,
            "content": evidence.make_evidence("manual_chunk", f"manual_chunks:{ch.id}", ch.content),
        }
        for score, ch in scored[:4]
    ]
    flagged = any(i["content"]["injection_flags"] for i in items)
    return ToolResult(
        result={"query": query, "chunks": items},
        summary=(f"manuals: {len(items)} chunk(s) for '{query[:60]}' [domain={evidence_domain}]"),
        trust_label=UNTRUSTED,
        injection_flag=flagged,
    )


def _promoted_trigger_prediction(
    session: Session, agent_run_id: int | None
) -> ModelPrediction | None:
    """Resolve the exact model prediction that caused this investigation.

    The incident link is the authoritative provenance path; this never selects
    an arbitrary latest prediction from another replay.
    """
    if agent_run_id is None:
        return None
    run = session.get(AgentRun, agent_run_id)
    if run is None or run.incident_id is None:
        return None
    alert = session.scalars(
        select(ShadowAlert)
        .where(
            ShadowAlert.incident_id == run.incident_id,
            ShadowAlert.status == "promoted",
        )
        .order_by(ShadowAlert.id.desc())
    ).first()
    if alert is None:
        return None
    prediction = session.get(ModelPrediction, alert.latest_prediction_id)
    if prediction is None or prediction.asset_id != run.asset_id or alert.asset_id != run.asset_id:
        raise ToolError(
            f"incident {run.incident_id} has inconsistent shadow-trigger provenance; "
            "refusing to substitute an unrelated prediction"
        )
    return prediction


def _prediction_payload(session: Session, prediction: ModelPrediction) -> dict:
    explanation = json.loads(prediction.explanation_json or "{}")
    source_prefix = (
        source_ids.replay_prefix(prediction.replay_run_id)
        if prediction.replay_run_id is not None
        else None
    )
    stats = sensor_stats(
        session, prediction.asset_id, as_of=prediction.window_end, source_id_prefix=source_prefix
    )
    return {
        "asset_id": prediction.asset_id,
        "prediction_id": prediction.id,
        "replay_run_id": prediction.replay_run_id,
        "window_start": prediction.window_start.isoformat() if prediction.window_start else None,
        "window_end": prediction.window_end.isoformat(),
        "risk": prediction.risk,
        "threshold": prediction.threshold,
        "risk_band": "above_threshold" if prediction.alert else "below_threshold",
        "alert": prediction.alert,
        "shadow": prediction.shadow,
        "model_source": prediction.model_source,
        "feature_version": prediction.feature_version,
        "policy_mode": prediction.policy_mode,
        "policy_version": prediction.policy_version,
        "operating_mode": prediction.operating_mode,
        "explanation": explanation,
        "signals": numeric_signals(stats),
        "stats": stats,
        "score_semantics": "normalized anomaly score; not a calibrated failure probability",
    }


def run_failure_prediction(
    session: Session, asset_id: int, agent_run_id: int | None = None
) -> ToolResult:
    trigger_prediction = _promoted_trigger_prediction(session, agent_run_id)
    if trigger_prediction is not None:
        pred = _prediction_payload(session, trigger_prediction)
        return ToolResult(
            result=pred,
            summary=(
                f"triggering anomaly score {pred['risk']:.3f} vs threshold "
                f"{pred['threshold']:.3f} [model={pred['model_source']}, "
                f"replay={pred['replay_run_id']}, prediction={pred['prediction_id']}]"
            ),
        )
    pred = get_adapter().predict(session, asset_id)
    return ToolResult(
        result=pred,
        summary=f"failure risk {pred['risk']:.2f} ({pred['risk_band']}) [model={pred['model_source']}]",
    )


def run_verification_baseline(
    session: Session, asset_id: int, agent_run_id: int | None = None
) -> ToolResult:
    """Like-for-like baseline for the simulated maintenance verifier.

    A promoted replay incident anchors the mock facility-risk view to that
    replay's exact trigger window. The real MetroPT score remains separate
    trigger evidence and is never compared to this simulated response score.
    """
    trigger = _promoted_trigger_prediction(session, agent_run_id)
    source_prefix = (
        source_ids.replay_prefix(trigger.replay_run_id)
        if trigger and trigger.replay_run_id
        else None
    )
    pred = get_adapter().predict(
        session,
        asset_id,
        as_of=trigger.window_end if trigger else None,
        source_id_prefix=source_prefix,
    )
    pred["score_semantics"] = "simulated facility risk score used for like-for-like verification"
    pred["anchored_replay_run_id"] = trigger.replay_run_id if trigger else None
    pred["anchored_prediction_id"] = trigger.id if trigger else None
    return ToolResult(
        result=pred,
        summary=(
            f"verification baseline {pred['risk']:.2f} [{pred['model_source']}]"
            + (
                f" anchored to replay={trigger.replay_run_id}, prediction={trigger.id}"
                if trigger
                else ""
            )
        ),
    )


def get_feature_importance(
    session: Session, asset_id: int, agent_run_id: int | None = None
) -> ToolResult:
    trigger_prediction = _promoted_trigger_prediction(session, agent_run_id)
    if trigger_prediction is not None:
        explanation = json.loads(trigger_prediction.explanation_json or "{}")
        items = [
            {"feature": item["feature"], "deviation_abs_z": item["abs_z"]}
            for item in explanation.get("top", [])
        ]
        top = (
            ", ".join(f"{item['feature']}={item['deviation_abs_z']:.2f}|z|" for item in items[:3])
            or "none"
        )
        return ToolResult(
            result={
                "asset_id": asset_id,
                "model_source": trigger_prediction.model_source,
                "prediction_id": trigger_prediction.id,
                "explanation_kind": explanation.get("kind"),
                "explanation_note": explanation.get("note"),
                "importance": items,
            },
            summary=f"descriptive feature deviations: {top} [model={trigger_prediction.model_source}]",
        )
    imp = get_adapter().feature_importance(session, asset_id)
    top = ", ".join(f"{c['feature']}={c['contribution']}" for c in imp["importance"][:3]) or "none"
    return ToolResult(
        result=imp, summary=f"feature importance: {top} [model={imp['model_source']}]"
    )


def run_diagnostic_test(session: Session, asset_id: int, test_id: str) -> ToolResult:
    """Compatibility seam that truthfully reports missing measurements.

    Results are never derived from existing telemetry. Independent evidence
    enters through the persisted evidence-request workflow only.
    """
    spec = diagnostic_evidence.EVIDENCE_TESTS.get(test_id)
    asset = _asset(session, asset_id)
    if spec is None or spec["asset_type"] != asset.asset_type:
        raise ToolError(
            f"diagnostic evidence '{test_id}' is not on the allow-list "
            f"for asset type '{asset.asset_type}'"
        )

    return ToolResult(
        {
            "test_id": test_id,
            "status": "unavailable",
            "reason": "independent measurement not present",
            "signal": {},
            "simulated": False,
        },
        f"diagnostic evidence {test_id}: unavailable - independent measurement not present",
        trust_label="structured_observation",
    )


def get_diagnostic_evidence(session: Session, run_id: int) -> ToolResult:
    """Read schema-validated observations already resolved for this run."""
    rows = diagnostic_evidence.resolved_evidence(session, run_id)
    return ToolResult(
        {"observations": rows},
        f"diagnostic evidence: {len(rows)} resolved observation(s)",
        trust_label="structured_observation",
    )


def get_technician_availability(session: Session, asset_type: str) -> ToolResult:
    specialty = {
        "chiller": "hvac",
        "ahu": "hvac",
        "crac": "hvac",
        "pump": "plumbing",
        "boiler": "hvac",
        "air_compressor": "hvac",
    }.get(asset_type, "hvac")
    techs = session.scalars(select(Technician).where(Technician.specialty == specialty)).all()
    items = [
        {"technician_id": t.id, "name": t.name, "shift": t.shift, "available": t.available}
        for t in techs
    ]
    available = [t for t in items if t["available"]]
    return ToolResult(
        result={"specialty": specialty, "technicians": items},
        summary=f"technicians: {len(available)}/{len(items)} {specialty} available",
    )


# --------------------------------------------------------------------------
# Write tools (gated)
# --------------------------------------------------------------------------


def propose_work_order(session: Session, asset_id: int, diagnosis: dict, run_id: int) -> ToolResult:
    """Create a PROPOSED agent action. Does NOT create a work order — that only
    happens in execute_approved_action after human approval."""
    asset = _asset(session, asset_id)
    action_spec = diagnosis.get("action") or {}
    payload = {
        "asset_id": asset_id,
        "asset_tag": asset.tag,
        "root_cause": diagnosis.get("cause", "undetermined"),
        "confidence": diagnosis.get("confidence", 0.0),
        "title": action_spec.get("title", "Diagnostic inspection"),
        "description": action_spec.get("description", ""),
        "priority": action_spec.get("priority", "medium"),
        "technician_id": diagnosis.get("technician_id"),
        "evidence_refs": diagnosis.get("evidence_refs", []),
        "risk_before": diagnosis.get("risk_before"),  # snapshot for Verify
        "risk_source": diagnosis.get("risk_source", ""),
        "risk_semantics": diagnosis.get("risk_semantics", ""),
        "trigger_score": diagnosis.get("trigger_score"),
        "trigger_source": diagnosis.get("trigger_source", ""),
        "trigger_score_semantics": diagnosis.get("trigger_score_semantics", ""),
        "trigger_threshold": diagnosis.get("trigger_threshold"),
        "trigger_window_end": diagnosis.get("trigger_window_end"),
        "trigger_prediction_id": diagnosis.get("trigger_prediction_id"),
        "replay_run_id": diagnosis.get("replay_run_id"),
    }
    action = AgentAction(
        run_id=run_id,
        action_type="create_work_order",
        status="proposed",
        payload_json=json.dumps(payload),
    )
    session.add(action)
    session.flush()
    return ToolResult(
        result={
            "action_id": action.id,
            "status": action.status,
            "payload": payload,
            "approval_required": True,
        },
        summary=f"proposed action #{action.id}: '{payload['title']}' — AWAITING HUMAN APPROVAL",
    )


def execute_approved_action(session: Session, action_id: int) -> ToolResult:
    """Execute a maintenance action. HARD GATE: raises unless status == 'approved'.

    This check is server-side and unconditional. Nothing the agent reads
    (evidence, notes, manuals) and nothing the agent decides can skip it —
    only routes/approval.approve_action() sets status='approved', and only a
    human reaches that route.
    """
    action = session.get(AgentAction, action_id)
    if action is None:
        raise ToolError(f"action {action_id} not found")
    if action.status != "approved":
        raise ApprovalRequiredError(
            f"action {action_id} has status '{action.status}' — human approval is required before execution"
        )

    now = utcnow()
    # Compare-and-set: exactly-once execution. The transition approved →
    # executed happens atomically in this transaction; a concurrent executor
    # observes rowcount 0 and is refused. (The work order and telemetry below
    # commit atomically with this flip, so a crash rolls everything back.)
    claimed = session.execute(
        update(AgentAction)
        .where(AgentAction.id == action_id, AgentAction.status == "approved")
        .values(status="executed", executed_at=now)
    ).rowcount
    if claimed != 1:
        raise ApprovalRequiredError(
            f"action {action_id} was claimed by a concurrent executor — refusing duplicate execution"
        )
    session.expire(action)

    payload = json.loads(action.payload_json)
    run = session.get(AgentRun, action.run_id)
    work_order = WorkOrder(
        asset_id=payload["asset_id"],
        technician_id=payload.get("technician_id"),
        status="completed",  # simulated execution completes immediately
        priority=payload.get("priority", "medium"),
        title=payload["title"],
        description=payload.get("description", ""),
        evidence_domain=(run.evidence_domain if run else "synthetic_facility"),
        notes=f"Executed by PulseOps agent (run #{action.run_id}) after human approval by "
        f"{action.decided_by or 'operator'}. Simulated maintenance.",
        source="agent",
        created_at=now,
        completed_at=now,
    )
    session.add(work_order)
    session.flush()

    # Hand off to the environment: it decides how the asset responds (the
    # agent does NOT get to know the outcome here — verification observes it).
    # The asset's status is also left untouched until verification.
    # Record the asset-clock anchor BEFORE the environment responds, so
    # verification can count post-action telemetry on the same clock
    # (source/sim time for replayed assets, wall time otherwise).
    from .failure_risk import observation_anchor

    trigger_window = payload.get("trigger_window_end")
    replay_run_id = payload.get("replay_run_id")
    observation_source_prefix = source_ids.replay_prefix(replay_run_id) if replay_run_id else None
    from datetime import datetime as _dt

    observation_as_of = _dt.fromisoformat(trigger_window) if trigger_window else None
    anchor = observation_as_of or observation_anchor(
        session, payload["asset_id"], observation_source_prefix
    )
    payload["executed_anchor"] = anchor.isoformat()
    response_source_prefix = source_ids.action_response_prefix(action.id)
    payload["verification_source_prefix"] = response_source_prefix
    action.payload_json = json.dumps(payload)

    environment.apply_maintenance_response(
        session,
        payload["asset_id"],
        observation_as_of=anchor,
        observation_source_prefix=observation_source_prefix,
        response_source_prefix=response_source_prefix,
    )

    action.work_order_id = work_order.id
    session.flush()
    emit_event(
        session,
        "agent.action.executed",
        {
            "action_id": action.id,
            "run_id": action.run_id,
            "work_order_id": work_order.id,
            "asset_id": payload["asset_id"],
        },
    )
    return ToolResult(
        result={
            "action_id": action.id,
            "work_order_id": work_order.id,
            "executed_at": now.isoformat(),
            "simulated": True,
            "outcome": "pending_verification",
        },
        summary=f"executed action #{action.id} → work order #{work_order.id} "
        f"(simulated maintenance; outcome pending verification)",
    )


def verify_outcome(session: Session, agent_run_id: int) -> ToolResult:
    """Observe post-action telemetry, classify the outcome (passed / failed /
    inconclusive / adverse — services/verification.py), persist a
    VerificationResult. Re-verification of the same action appends a new row."""
    run = session.get(AgentRun, agent_run_id)
    if run is None:
        raise ToolError(f"run {agent_run_id} not found")
    action = session.scalars(
        select(AgentAction)
        .where(AgentAction.run_id == agent_run_id, AgentAction.status == "executed")
        .order_by(AgentAction.executed_at.desc(), AgentAction.id.desc())
    ).first()
    if action is None:
        raise ToolError(f"run {agent_run_id} has no executed action to verify")

    payload = json.loads(action.payload_json)
    risk_before = payload.get("risk_before")
    if risk_before is None:
        raise ToolError("risk_before missing from action payload — Plan phase must record it")

    # Post-action telemetry sufficiency. COVERAGE POLICY: every sensor attached
    # to the asset is REQUIRED for verification, and the effective count is the
    # MINIMUM across required sensors — one chatty sensor can never satisfy a
    # per-sensor requirement while others are silent. Per-sensor counts are
    # persisted in the verification details for audit. Counting is done on the
    # ASSET'S clock: 'post-action' means after the observation anchor recorded
    # at execution time (source/sim time for replayed assets).
    from datetime import datetime as _dt

    anchor_iso = payload.get("executed_anchor")
    post_anchor = _dt.fromisoformat(anchor_iso) if anchor_iso else action.executed_at
    sensors = session.scalars(select(Sensor).where(Sensor.asset_id == run.asset_id)).all()
    per_sensor_counts: dict[str, int] = {}
    response_source_prefix = payload.get("verification_source_prefix")
    for s in sensors:
        count_query = (
            select(func.count())
            .select_from(SensorReading)
            .where(SensorReading.sensor_id == s.id, SensorReading.ts > post_anchor)
        )
        if response_source_prefix:
            count_query = count_query.where(
                SensorReading.source_id.startswith(response_source_prefix, autoescape=True)
            )
        n = session.scalar(count_query)
        per_sensor_counts[s.kind] = int(n or 0)
    post_readings = min(per_sensor_counts.values()) if per_sensor_counts else 0

    pred_after = get_adapter().predict(
        session,
        run.asset_id,
        source_id_prefix=response_source_prefix,
    )
    expected_source = payload.get("risk_source")
    if expected_source and expected_source != pred_after["model_source"]:
        raise ToolError(
            f"verification model mismatch: before={expected_source}, "
            f"after={pred_after['model_source']}; refusing an invalid risk delta"
        )
    risk_after = pred_after["risk"]
    delta = round(risk_before - risk_after, 3)
    outcome, reason = classify_outcome(risk_before, risk_after, post_readings)

    vr = VerificationResult(
        run_id=run.id,
        action_id=action.id,
        risk_before=risk_before,
        risk_after=risk_after,
        risk_delta=delta,
        outcome=outcome,
        outcome_reason=reason,
        passed=(outcome == "passed"),
        details_json=json.dumps(
            {
                "model_source_before": expected_source or pred_after["model_source"],
                "model_source_after": pred_after["model_source"],
                "trigger_source": payload.get("trigger_source"),
                "trigger_prediction_id": payload.get("trigger_prediction_id"),
                "signals_after": pred_after["signals"],
                "stats_after": pred_after["stats"],
                "post_action_readings": post_readings,
                "coverage_policy": "min across required sensors (all sensors of the asset)",
                "per_sensor_post_readings": per_sensor_counts,
                "verification_source_prefix": response_source_prefix,
            }
        ),
    )
    session.add(vr)
    session.flush()
    return ToolResult(
        result={
            "verification_id": vr.id,
            "action_id": action.id,
            "risk_before": risk_before,
            "risk_after": risk_after,
            "risk_delta": delta,
            "outcome": outcome,
            "outcome_reason": reason,
            "passed": vr.passed,
            "post_action_readings": post_readings,
            "per_sensor_post_readings": per_sensor_counts,
            "model_source": pred_after["model_source"],
        },
        summary=f"verify: risk {risk_before:.2f} → {risk_after:.2f} (Δ {delta:+.2f}) — "
        f"{outcome.upper()}: {reason}",
    )


# Registry: name -> (callable, is_write_tool). The engine records every call.
TOOLS: dict[str, tuple] = {
    "query_case_memory": (query_case_memory, False),
    "get_asset_profile": (get_asset_profile, False),
    "get_recent_sensor_readings": (get_recent_sensor_readings, False),
    "get_open_work_orders": (get_open_work_orders, False),
    "get_recent_inspections": (get_recent_inspections, False),
    "search_maintenance_manuals": (search_maintenance_manuals, False),
    "run_failure_prediction": (run_failure_prediction, False),
    "run_verification_baseline": (run_verification_baseline, False),
    "get_feature_importance": (get_feature_importance, False),
    "get_technician_availability": (get_technician_availability, False),
    "get_diagnostic_evidence": (get_diagnostic_evidence, False),
    "propose_work_order": (propose_work_order, True),
    "execute_approved_action": (execute_approved_action, True),
    "verify_outcome": (verify_outcome, True),
}
