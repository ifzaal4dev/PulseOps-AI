"""Section I: resumable-run architecture.

The engine holds no in-process state: a fresh AgentEngine constructed from the
run id must continue exactly where the previous one stopped.
"""

from sqlalchemy import select

from pulseops_api.models import WorkOrder
from pulseops_api.services import approval
from pulseops_api.services.agent_loop import AgentEngine


def test_run_stopped_after_observe_resumes_at_diagnose(seeded):
    session, ids = seeded
    engine = AgentEngine.start_run(session, ids["chiller3_id"], trigger="resume test")
    engine.advance()  # observe only
    run_id = engine.run.id
    assert engine.run.current_phase == "diagnose"
    assert [s.phase for s in engine.run.steps] == ["observe"]
    del engine  # simulate process exit

    resumed = AgentEngine(session, run_id)  # rebuild purely from the DB
    assert resumed.run.current_phase == "diagnose"
    resumed.advance()
    assert [s.phase for s in resumed.run.steps] == ["observe", "diagnose"]
    assert resumed.run.current_phase == "plan"


def test_resumed_run_with_proposed_action_keeps_waiting_not_executing(seeded):
    session, ids = seeded
    engine = AgentEngine.start_run(session, ids["chiller3_id"], trigger="resume test")
    run = engine.run_until_blocked()
    run_id = run.id
    assert run.status == "awaiting_approval"
    del engine

    resumed = AgentEngine(session, run_id)
    result = resumed.run_until_blocked()  # must NOT execute the pending action
    assert result.status == "awaiting_approval"
    assert result.actions[0].status == "proposed"
    assert session.scalars(select(WorkOrder).where(WorkOrder.source == "agent")).all() == []

    # After approval, a resumed engine finishes the loop.
    approval.approve_action(session, result.actions[0].id, decided_by="operator")
    finished = AgentEngine(session, run_id).run_until_blocked()
    assert finished.status == "completed"


def test_max_iterations_guard(seeded):
    session, ids = seeded
    engine = AgentEngine.start_run(
        session, ids["chiller3_id"], trigger="guard test", max_iterations=2
    )
    run = engine.run_until_blocked()
    # observe + diagnose used both iterations; third advance aborts the run.
    engine.advance()
    assert run.status == "failed"
    assert "max_iterations" in run.summary


def test_run_state_fields_persist(seeded):
    session, ids = seeded
    engine = AgentEngine.start_run(session, ids["chiller3_id"], trigger="state test")
    engine.advance()
    run = engine.run
    assert run.iterations_used == 1
    assert run.max_iterations == 12
    assert run.status == "running"
    assert run.current_phase == "diagnose"
