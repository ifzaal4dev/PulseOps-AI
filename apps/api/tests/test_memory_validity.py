"""Memory validity.

Provenance + validation fields distinguish seeded precedents, provisional
inferences, validated successes, and validated failures. Only validated
successes may boost a diagnosis, and the agent never validates its own
memories — validation always cites a VerificationResult.
"""

import json

from sqlalchemy import select

from pulseops_api.models import CaseMemory
from pulseops_api.services import approval, environment
from pulseops_api.services.agent_loop import AgentEngine


def top_hypothesis_sources(run):
    step = next(s for s in run.steps if s.phase == "diagnose")
    top = json.loads(step.output_json)["hypotheses"][0]
    return {src for ev in top["evidence"] for src in ev["sources"]}, top


def precedent_boosts(top):
    """Evidence entries from the precedent-BOOST channel specifically (the
    narrative keyword channel is separate and always allowed)."""
    return [ev for ev in top["evidence"] if ev["signal"] == "case_precedent"]


def test_seeded_precedents_are_validated_success(seeded):
    session, ids = seeded
    for case in session.scalars(select(CaseMemory)).all():
        assert case.provenance == "seeded"
        assert case.validation_status == "validated_success"
        assert case.validated_by


def test_validated_success_precedent_boosts(seeded):
    session, ids = seeded
    run = AgentEngine.start_run(
        session, ids["chiller3_id"], trigger="memory test"
    ).run_until_blocked()
    sources, top = top_hypothesis_sources(run)
    assert precedent_boosts(top), "validated precedent should boost the diagnosis"


def test_unvalidated_precedent_does_not_boost(seeded):
    session, ids = seeded
    bearing_case = session.scalars(
        select(CaseMemory).where(CaseMemory.root_cause == "compressor bearing wear")
    ).one()
    bearing_case.validation_status = "provisional"  # e.g. awaiting confirmation
    session.commit()

    run = AgentEngine.start_run(
        session, ids["chiller3_id"], trigger="memory test"
    ).run_until_blocked()
    sources, top = top_hypothesis_sources(run)
    assert precedent_boosts(top) == [], "provisional memory must not boost the diagnosis"
    # Sensor evidence alone still carries the diagnosis (the boost is a bonus, not a crutch).
    assert top["cause"] == "compressor bearing wear"


def test_successful_run_memory_is_verification_validated(seeded):
    session, ids = seeded
    engine = AgentEngine.start_run(session, ids["chiller3_id"], trigger="memory test")
    run = engine.run_until_blocked()
    approval.approve_action(session, run.actions[0].id, decided_by="operator")
    run = engine.run_until_blocked()
    assert run.status == "completed"

    postmortem = session.scalars(select(CaseMemory).where(CaseMemory.source_run_id == run.id)).one()
    assert postmortem.provenance == "agent"
    assert postmortem.validation_status == "validated_success"
    assert postmortem.validated_by.startswith("verification:")
    assert postmortem.validated_by != "verification:"  # cites a concrete result


def test_failed_run_memory_is_negative_evidence_never_success(seeded):
    session, ids = seeded
    environment.set_outcome_profile(ids["chiller3_id"], "failure")
    engine = AgentEngine.start_run(session, ids["chiller3_id"], trigger="memory test")
    run = engine.run_until_blocked()
    approval.approve_action(session, run.actions[0].id, decided_by="operator")
    run = engine.run_until_blocked()

    entries = session.scalars(select(CaseMemory).where(CaseMemory.source_run_id == run.id)).all()
    assert entries, "the failed attempt must be persisted"
    for e in entries:
        assert e.validation_status == "validated_failure"
        assert "FAILED" in e.outcome or "ADVERSE" in e.outcome
        assert e.validated_by.startswith("verification:")


def test_negative_precedent_penalizes_but_does_not_flip_strong_evidence(seeded):
    session, ids = seeded
    environment.set_outcome_profile(ids["chiller3_id"], "failure")
    engine = AgentEngine.start_run(session, ids["chiller3_id"], trigger="memory test")
    run = engine.run_until_blocked()
    approval.approve_action(session, run.actions[0].id, decided_by="operator")
    run = engine.run_until_blocked()
    assert run.status == "awaiting_approval"  # replanning after the failure

    # Attempt 2's diagnosis saw the validated failure as negative evidence...
    diagnose_steps = [s for s in run.steps if s.phase == "diagnose"]
    top2 = json.loads(diagnose_steps[-1].output_json)["hypotheses"][0]
    negative = [ev for ev in top2["evidence"] if ev["signal"] == "negative_precedent"]
    assert negative and negative[0]["weight"] < 0
    # ...but overwhelming sensor evidence still names the same cause.
    assert top2["cause"] == "compressor bearing wear"
