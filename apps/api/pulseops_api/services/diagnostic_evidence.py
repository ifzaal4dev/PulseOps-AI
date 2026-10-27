"""Typed, asynchronous diagnostic-evidence workflow.

The previous ``apu_oil_analysis`` derived an alleged oil-sample result from
the already-used oil-temperature signal. That was duplicate, misleading
evidence. This module never synthesizes measurements: it defines the allowed
result shapes, persists requests, validates independently supplied results,
and maps only those structured results to deterministic signals.
"""

from __future__ import annotations

import json
import math
from datetime import timedelta

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..models import (
    AgentRun,
    Asset,
    DiagnosticEvidenceObservation,
    DiagnosticEvidenceRequest,
)
from ..timeutil import utcnow
from .events import emit_event


class DiagnosticEvidenceError(Exception):
    pass


SOURCES = {"operator", "laboratory", "sensor", "import"}


# Each result has one required categorical field. Optional numeric measurements
# are retained for audit/display but never converted to a conclusion without a
# domain-owned categorical interpretation supplied by the submitter/integration.
EVIDENCE_TESTS: dict[str, dict] = {
    "ultrasonic_leak_survey": {
        "asset_type": "air_compressor",
        "label": "Ultrasonic leak survey",
        "instructions": (
            "Survey dryer towers, pneumatic fittings, drains, reservoir connections, "
            "and unloader valves; record whether a leak is independently detected."
        ),
        "field": "finding",
        "enum": ["leak_detected", "no_leak_detected", "inconclusive"],
        "signals": {
            "leak_detected": {"air_leak_confirmed": 1.0},
            "no_leak_detected": {"air_leak_not_detected": 1.0},
            "inconclusive": {},
        },
        "discriminates": ["compressed-air leak", "oil-system abnormality (synthetic scenario)"],
    },
    "pressure_decay_test": {
        "asset_type": "air_compressor",
        "label": "Compressor pressure-decay test",
        "instructions": (
            "Record an independently performed, safely isolated pressure-decay test. "
            "Any operational isolation remains outside the agent and requires site procedure."
        ),
        "field": "finding",
        "enum": ["leak_detected", "no_leak_detected", "inconclusive"],
        "signals": {
            "leak_detected": {"air_leak_confirmed": 1.0},
            "no_leak_detected": {"air_leak_not_detected": 1.0},
            "inconclusive": {},
        },
        "discriminates": ["compressed-air leak", "oil-system abnormality (synthetic scenario)"],
    },
    "oil_sample_analysis": {
        "asset_type": "air_compressor",
        "label": "Independent oil-sample analysis",
        "instructions": (
            "Submit a genuine laboratory/sample result (for example particle count, "
            "viscosity, water, or acidity). Oil temperature alone is not oil quality."
        ),
        "field": "quality",
        "enum": ["normal", "degraded", "inconclusive"],
        "signals": {
            "normal": {"oil_quality_ok": 1.0},
            "degraded": {"oil_quality_bad": 1.0},
            "inconclusive": {},
        },
        "discriminates": ["compressed-air leak", "oil-system abnormality (synthetic scenario)"],
    },
    "oil_pressure_measurement": {
        "asset_type": "air_compressor",
        "label": "Independent oil-pressure measurement",
        "instructions": "Submit an oil-pressure status interpreted against the applicable OEM range.",
        "field": "status",
        "enum": ["normal", "low", "high", "inconclusive"],
        "signals": {
            "normal": {"oil_pressure_ok": 1.0},
            "low": {"oil_pressure_abnormal": 1.0},
            "high": {"oil_pressure_abnormal": 1.0},
            "inconclusive": {},
        },
        "discriminates": ["compressed-air leak", "oil-system abnormality (synthetic scenario)"],
    },
    "oil_cooler_temperature_delta": {
        "asset_type": "air_compressor",
        "label": "Oil-cooler inlet/outlet temperature check",
        "instructions": (
            "Submit a cooler delta interpretation from independent inlet/outlet measurements."
        ),
        "field": "status",
        "enum": ["normal", "abnormal", "inconclusive"],
        "signals": {
            "normal": {"oil_cooler_ok": 1.0},
            "abnormal": {"oil_cooler_abnormal": 1.0},
            "inconclusive": {},
        },
        "discriminates": ["compressed-air leak", "oil-system abnormality (synthetic scenario)"],
    },
}


def tests_for(asset_type: str) -> dict[str, dict]:
    return {
        key: value for key, value in EVIDENCE_TESTS.items() if value["asset_type"] == asset_type
    }


def public_schema(test_id: str) -> dict:
    spec = EVIDENCE_TESTS[test_id]
    return {
        "label": spec["label"],
        "instructions": spec["instructions"],
        "field": spec["field"],
        "enum": list(spec["enum"]),
        "optional_numeric": {"value": "number", "unit": "string"},
        "note": "structured observation; no free text influences scoring",
    }


def _validate_result(test_id: str, raw: dict) -> tuple[dict, dict[str, float]]:
    spec = EVIDENCE_TESTS.get(test_id)
    if spec is None:
        raise DiagnosticEvidenceError(f"unknown diagnostic evidence type '{test_id}'")
    if not isinstance(raw, dict):
        raise DiagnosticEvidenceError("evidence result must be an object")
    allowed = {spec["field"], "value", "unit"}
    unknown = set(raw) - allowed
    if unknown:
        raise DiagnosticEvidenceError(
            f"unsupported evidence field(s): {', '.join(sorted(unknown))}"
        )
    choice = raw.get(spec["field"])
    if choice not in spec["enum"]:
        raise DiagnosticEvidenceError(f"{spec['field']} must be one of {spec['enum']}")
    if "value" in raw and (
        isinstance(raw["value"], bool)
        or not isinstance(raw["value"], (int, float))
        or not math.isfinite(float(raw["value"]))
    ):
        raise DiagnosticEvidenceError("optional value must be numeric")
    if "unit" in raw and (not isinstance(raw["unit"], str) or len(raw["unit"]) > 30):
        raise DiagnosticEvidenceError("optional unit must be a short string")
    result = {spec["field"]: choice}
    if "value" in raw:
        result["value"] = float(raw["value"])
    if "unit" in raw:
        result["unit"] = raw["unit"].strip()
    return result, dict(spec["signals"][choice])


def create_request(
    session: Session,
    run: AgentRun,
    test_id: str,
    *,
    requested_by: str,
    rationale: str,
    hypotheses: list[str],
) -> DiagnosticEvidenceRequest:
    spec = EVIDENCE_TESTS.get(test_id)
    asset = session.get(Asset, run.asset_id)
    if spec is None or asset is None or spec["asset_type"] != asset.asset_type:
        raise DiagnosticEvidenceError(
            f"diagnostic evidence '{test_id}' is not allowed for run asset"
        )
    existing = session.scalars(
        select(DiagnosticEvidenceRequest).where(
            DiagnosticEvidenceRequest.run_id == run.id,
            DiagnosticEvidenceRequest.test_id == test_id,
        )
    ).first()
    if existing is not None:
        if existing.status == "pending":
            run.status = "awaiting_evidence"
            run.current_phase = "diagnose"
        return existing
    count = len(run.evidence_requests)
    request = DiagnosticEvidenceRequest(
        run=run,
        asset_id=run.asset_id,
        test_id=test_id,
        requested_by=requested_by[:80],
        rationale=rationale,
        hypotheses_json=json.dumps(hypotheses),
        result_schema_json=json.dumps(public_schema(test_id)),
        request_round=count + 1,
    )
    session.add(request)
    session.flush()
    run.status = "awaiting_evidence"
    run.current_phase = "diagnose"
    run.summary = (
        f"Awaiting independent evidence: {spec['label']}. No maintenance action has been proposed."
    )
    emit_event(
        session,
        "agent.evidence.requested",
        {
            "run_id": run.id,
            "asset_id": run.asset_id,
            "evidence_request_id": request.id,
            "test_id": test_id,
        },
    )
    return request


def submit_observation(
    session: Session,
    request_id: int,
    *,
    result: dict,
    submitted_by: str,
    source: str,
    observed_at=None,
) -> DiagnosticEvidenceRequest:
    # Serialize competing submissions on PostgreSQL. SQLite ignores
    # FOR UPDATE but the unique observation constraint remains the backstop.
    request = session.scalars(
        select(DiagnosticEvidenceRequest)
        .where(DiagnosticEvidenceRequest.id == request_id)
        .with_for_update()
    ).first()
    if request is None:
        raise DiagnosticEvidenceError(f"evidence request {request_id} not found")
    if source not in SOURCES:
        raise DiagnosticEvidenceError(f"source must be one of {sorted(SOURCES)}")
    submitted_by = submitted_by.strip()
    if not submitted_by:
        raise DiagnosticEvidenceError("submitted_by must identify the evidence submitter")
    clean, signals = _validate_result(request.test_id, result)
    if request.status == "resolved":
        existing = json.loads(request.observation.result_json)
        if existing == clean:
            return request
        raise DiagnosticEvidenceError(
            f"evidence request {request_id} is already resolved with a different result"
        )
    if request.status != "pending":
        raise DiagnosticEvidenceError(
            f"evidence request {request_id} is {request.status}, not pending"
        )

    now = utcnow()
    observed_at = observed_at or now
    if observed_at.tzinfo is None:
        observed_at = observed_at.replace(tzinfo=now.tzinfo)
    if observed_at > now + timedelta(minutes=5):
        raise DiagnosticEvidenceError("observed_at cannot be in the future")
    if observed_at < request.created_at - timedelta(days=7):
        raise DiagnosticEvidenceError("observation is too old for this diagnostic request")

    observation = DiagnosticEvidenceObservation(
        request_id=request.id,
        submitted_by=submitted_by[:120],
        source=source,
        observed_at=observed_at,
        result_json=json.dumps(clean),
        signal_json=json.dumps(signals),
    )
    session.add(observation)
    request.status = "resolved"
    request.resolved_at = now
    run = request.run
    run.status = "running"
    run.current_phase = "diagnose"
    run.summary = (
        f"Independent evidence received for {EVIDENCE_TESTS[request.test_id]['label']}; "
        "ready to resume Diagnose."
    )
    emit_event(
        session,
        "agent.evidence.received",
        {
            "run_id": run.id,
            "asset_id": run.asset_id,
            "evidence_request_id": request.id,
            "test_id": request.test_id,
            "source": source,
        },
    )
    session.commit()
    return request


def resolved_evidence(session: Session, run_id: int) -> list[dict]:
    requests = session.scalars(
        select(DiagnosticEvidenceRequest)
        .where(
            DiagnosticEvidenceRequest.run_id == run_id,
            DiagnosticEvidenceRequest.status == "resolved",
        )
        .order_by(DiagnosticEvidenceRequest.id)
    ).all()
    rows = []
    for request in requests:
        if request.observation is None:
            continue
        rows.append(
            {
                "request_id": request.id,
                "test_id": request.test_id,
                "label": EVIDENCE_TESTS[request.test_id]["label"],
                "requested_by": request.requested_by,
                "result": json.loads(request.observation.result_json),
                "signal": json.loads(request.observation.signal_json),
                "source": request.observation.source,
                "submitted_by": request.observation.submitted_by,
                "observed_at": request.observation.observed_at.isoformat(),
                "ref": f"diagnostic_evidence:{request.id}",
            }
        )
    return rows
