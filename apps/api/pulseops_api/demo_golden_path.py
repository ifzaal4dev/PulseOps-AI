"""Headless golden-path demo: the full Chiller-03 incident loop in the terminal.

    cd apps/api
    python -m pulseops_api.demo_golden_path

Re-seeds the SQLite database, then drives Observe → Diagnose → Plan →
(human approval) → Act → Verify → Learn, printing the audit trail. This is the
surviving proof that the engine works even without the UI.
"""

from __future__ import annotations

import json
import sys

from sqlalchemy import select

from .db import Base, SessionLocal, engine
from .models import Asset, CaseMemory, WorkOrder
from .seed.seed_db import seed
from .services import approval
from .services.agent_loop import AgentEngine


def banner(text: str):
    print(f"\n{'=' * 72}\n{text}\n{'=' * 72}")


def main() -> None:
    # Windows consoles often default to cp1252; keep the arrows printable.
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    banner("PulseOps AI — Chiller-03 golden path (headless demo)")
    Base.metadata.drop_all(engine)
    Base.metadata.create_all(engine)
    session = SessionLocal()
    ids = seed(session)
    ch3 = session.get(Asset, ids["chiller3_id"])
    print(f"Seeded facility. Incident asset: {ch3.name} ({ch3.tag}), status={ch3.status}")

    banner("1. Agent investigation: Observe → Diagnose → Plan")
    agent = AgentEngine.start_run(session, ch3.id, trigger="demo: critical vibration incident")
    run = agent.run_until_blocked()
    for step in run.steps:
        print(f"  [{step.phase.upper():8}] {step.reasoning_summary}")
    print("\n  Tool audit trail:")
    for tc in run.tool_calls:
        flag = "  ⚠ INJECTION-FLAGGED" if tc.injection_flag else ""
        print(f"    #{tc.seq:2} {tc.tool_name:28} [{tc.trust_label:18}] {tc.result_summary}{flag}")

    action = run.actions[0]
    payload = json.loads(action.payload_json)
    banner(f"2. HUMAN APPROVAL GATE — run status: {run.status}")
    print(f"  Proposed action #{action.id}: {payload['title']}")
    print(f"  Root cause: {payload['root_cause']}   priority: {payload['priority']}")
    print(f"  Risk before action: {payload['risk_before']:.2f}")
    print("  → approving as 'demo_operator' ...")
    approval.approve_action(session, action.id, decided_by="demo_operator")

    banner("3. Act → Verify → Learn")
    run = agent.run_until_blocked()
    for step in run.steps[3:]:
        print(f"  [{step.phase.upper():8}] {step.reasoning_summary}")

    banner("4. Outcome")
    print(f"  Run status: {run.status}")
    print(f"  Summary: {run.summary}")
    order = session.scalars(select(WorkOrder).where(WorkOrder.source == "agent")).one()
    print(f"  Work order #{order.id}: {order.title} [{order.status}]")
    postmortem = session.scalars(select(CaseMemory).where(CaseMemory.source_run_id == run.id)).one()
    print(f"  Case memory #{postmortem.id}: {postmortem.title}")
    print(f"  Risk reduction recorded: {postmortem.risk_reduction:+.2f}")
    session.close()
    print("\nDemo complete. Start the API + web app to see the same loop in the UI.")


if __name__ == "__main__":
    main()
