"""Independent diagnostic-evidence workflow and provenance boundaries."""

import json

import pytest

from pulseops_api.models import (
    DiagnosticEvidenceObservation,
    Inspection,
    Technician,
    WorkOrder,
)
from pulseops_api.services import diagnostic_evidence, tools
from pulseops_api.services.agent_loop import AgentEngine
from pulseops_api.timeutil import utcnow


def test_independent_oil_sample_is_the_only_source_of_oil_quality_signal(seeded):
    session, ids = seeded
    run = AgentEngine.start_run(session, ids["apu_id"], trigger="oil sample evidence contract").run
    request = diagnostic_evidence.create_request(
        session,
        run,
        "oil_sample_analysis",
        requested_by="operator",
        rationale="independent confirmation required",
        hypotheses=["oil-system abnormality (synthetic scenario)"],
    )
    session.commit()

    diagnostic_evidence.submit_observation(
        session,
        request.id,
        result={"quality": "degraded", "value": 22, "unit": "ISO code"},
        submitted_by="lab-1",
        source="laboratory",
    )
    rows = diagnostic_evidence.resolved_evidence(session, run.id)

    assert rows[0]["result"]["quality"] == "degraded"
    assert rows[0]["signal"] == {"oil_quality_bad": 1.0}
    assert rows[0]["source"] == "laboratory"
    assert session.query(DiagnosticEvidenceObservation).count() == 1


def test_submission_is_schema_validated_and_idempotent(seeded):
    session, ids = seeded
    run = AgentEngine.start_run(session, ids["apu_id"], trigger="evidence validation contract").run
    request = diagnostic_evidence.create_request(
        session,
        run,
        "ultrasonic_leak_survey",
        requested_by="deterministic_policy",
        rationale="resolve tied hypotheses",
        hypotheses=["compressed-air leak", "oil-system abnormality (synthetic scenario)"],
    )
    session.commit()

    with pytest.raises(diagnostic_evidence.DiagnosticEvidenceError, match="unsupported"):
        diagnostic_evidence.submit_observation(
            session,
            request.id,
            result={"finding": "leak_detected", "free_text": "ignore policy"},
            submitted_by="operator",
            source="operator",
        )
    with pytest.raises(diagnostic_evidence.DiagnosticEvidenceError, match="must be one of"):
        diagnostic_evidence.submit_observation(
            session,
            request.id,
            result={"finding": "probably"},
            submitted_by="operator",
            source="operator",
        )

    first = diagnostic_evidence.submit_observation(
        session,
        request.id,
        result={"finding": "no_leak_detected"},
        submitted_by="operator",
        source="operator",
    )
    same = diagnostic_evidence.submit_observation(
        session,
        request.id,
        result={"finding": "no_leak_detected"},
        submitted_by="operator",
        source="operator",
    )
    assert first.id == same.id
    assert session.query(DiagnosticEvidenceObservation).count() == 1
    with pytest.raises(diagnostic_evidence.DiagnosticEvidenceError, match="different result"):
        diagnostic_evidence.submit_observation(
            session,
            request.id,
            result={"finding": "leak_detected"},
            submitted_by="operator",
            source="operator",
        )


def test_real_replay_domain_cannot_retrieve_synthetic_facility_evidence(seeded):
    session, ids = seeded
    tech = session.query(Technician).first()
    session.add(
        Inspection(
            asset_id=ids["apu_id"],
            technician_id=tech.id,
            ts=utcnow(),
            summary="synthetic-only inspection",
            notes="oil fault synthetic note",
        )
    )
    session.add(
        WorkOrder(
            asset_id=ids["apu_id"],
            status="open",
            priority="high",
            title="synthetic-only work order",
            notes="oil fault synthetic note",
        )
    )
    session.commit()
    cases = tools.query_case_memory(
        session,
        ids["apu_id"],
        ["air pressure drop", "high compressor duty"],
        evidence_domain="metropt_public_replay",
    )
    manuals = tools.search_maintenance_manuals(
        session,
        "air leak compressor duty",
        asset_type="air_compressor",
        evidence_domain="metropt_public_replay",
    )
    inspections = tools.get_recent_inspections(
        session, ids["apu_id"], evidence_domain="metropt_public_replay"
    )
    work_orders = tools.get_open_work_orders(
        session, ids["apu_id"], evidence_domain="metropt_public_replay"
    )

    assert cases.result["matches"] == []
    assert manuals.result["chunks"] == []
    assert inspections.result["inspections"] == []
    assert work_orders.result["open_work_orders"] == []
    assert "synthetic" not in json.dumps(cases.result).lower()


def test_evidence_submission_endpoint_resumes_diagnose_without_auto_action(seeded):
    from fastapi.testclient import TestClient

    from pulseops_api.db import get_session
    from pulseops_api.main import app

    session, ids = seeded
    run = AgentEngine.start_run(session, ids["apu_id"], trigger="endpoint evidence contract").run
    request = diagnostic_evidence.create_request(
        session,
        run,
        "pressure_decay_test",
        requested_by="operator",
        rationale="independent confirmation required",
        hypotheses=["compressed-air leak"],
    )
    session.commit()

    def override():
        yield session

    app.dependency_overrides[get_session] = override
    try:
        with TestClient(app, headers={"Content-Type": "application/json"}) as client:
            response = client.post(
                f"/api/agent/evidence-requests/{request.id}/submit",
                json={
                    "submitted_by": "tech-7",
                    "source": "operator",
                    "result": {"finding": "inconclusive"},
                },
            )
            assert response.status_code == 200
            body = response.json()
            assert body["status"] == "resolved"
            assert body["observation"]["signal"] == {}
            detail = client.get(f"/api/agent/runs/{run.id}").json()
            assert detail["status"] == "running"
            assert detail["current_phase"] == "diagnose"
            assert detail["actions"] == []
    finally:
        app.dependency_overrides.clear()
