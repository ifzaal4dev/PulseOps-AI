"""The persisted agentic loop: Observe → Diagnose → Plan → Act → Verify → Learn.

Design contracts (tests and eval depend on these — do not break):

  * Every phase is one AgentStep row; every tool invocation is one
    AgentToolCall row. The run is fully reconstructable from the DB.
  * Resumable: AgentEngine holds no state outside the DB. `advance()` reads
    run.current_phase / run.status, executes exactly one phase, persists, and
    returns. A process restart between phases loses nothing.
  * Diagnose queries case_memory FIRST — before any other evidence tool.
  * Plan ends with a PROPOSED action and run.status='awaiting_approval'.
    Act executes only after a human approval flips the action to 'approved'.
  * Untrusted evidence can only contribute whitelisted domain signals
    (see evidence.py); it can never reorder tools, change the proposed action,
    or trigger execution.
  * reasoning_summary is a concise audit summary, never hidden chain-of-thought.

Diagnosis is deterministic signature scoring (services/diagnosis.py). An
optional LLM may rerank causes, request evidence, or abstain, but the state
machine, tool audit trail, and gates stay here.
"""

from __future__ import annotations

import json
from typing import NamedTuple

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from ..config import settings
from ..models import (
    AgentAction,
    AgentRun,
    AgentStep,
    AgentToolCall,
    Asset,
    CaseMemory,
    Incident,
)
from ..timeutil import utcnow
from . import diagnosis, diagnostic_evidence, knowledge, tools
from .events import emit_event
from .reasoning.hybrid import run_reasoning
from .reasoning.snapshot import EvidenceItem, EvidenceSnapshot


class DuplicateRunError(Exception):
    """An incident already has an active agent run."""


# numeric signal token -> human symptom phrase (used to query case memory)
SIGNAL_SYMPTOMS = {
    "vibration_high": "high vibration",
    "vibration_ramp": "vibration ramp",
    "pressure_drop": "discharge pressure drop",
    "power_increase": "power increase",
    "power_anomaly": "power anomaly",
    "supply_temp_high": "supply temp drift",
    "flow_drop": "flow reduction",
    # APU air compressor (MetroPT replay asset)
    "air_pressure_drop": "air pressure drop",
    "compressor_duty_high": "high compressor duty",
    "motor_current_high": "high motor current",
    "oil_temp_high": "oil temperature high",
    # NOTE: vibration_low is deliberately absent — it means "vibration is
    # normal", which is a discriminating signal (e.g. for refrigerant loss)
    # but NOT a symptom; treating it as one made healthy assets retrieve
    # irrelevant precedent/manual evidence.
}


class _DiagnosisEvidence(NamedTuple):
    """Tool results gathered for one Diagnose phase."""

    cases: tools.ToolResult
    readings: tools.ToolResult
    work_orders: tools.ToolResult
    inspections: tools.ToolResult
    manuals: tools.ToolResult
    prediction: tools.ToolResult
    importance: tools.ToolResult
    observations: tools.ToolResult


class AgentEngine:
    """Drives one AgentRun. Stateless between calls — everything lives in the DB."""

    def __init__(self, session: Session, run_id: int):
        self.session = session
        self.run = session.get(AgentRun, run_id)
        if self.run is None:
            raise ValueError(f"agent run {run_id} not found")
        # Test/eval hook: inject a fake reasoning provider. None → the
        # validated factory (config-driven) decides.
        self.reasoning_provider = None

    # ------------------------------------------------------------------ setup

    @classmethod
    def start_run(
        cls,
        session: Session,
        asset_id: int,
        trigger: str = "manual",
        incident_id: int | None = None,
        max_iterations: int = 12,
        evidence_domain: str = "synthetic_facility",
    ) -> AgentEngine:
        if incident_id is None:
            incident = session.scalars(
                select(Incident).where(
                    Incident.asset_id == asset_id, Incident.status.in_(["active", "investigating"])
                )
            ).first()
            incident_id = incident.id if incident else None
        if incident_id is not None:
            # One active investigation per incident (a partial unique index on
            # agent_runs backs this up at the database level).
            active = session.scalars(
                select(AgentRun).where(
                    AgentRun.incident_id == incident_id,
                    AgentRun.status.in_(
                        ["running", "awaiting_evidence", "awaiting_approval", "paused"]
                    ),
                )
            ).first()
            if active is not None:
                raise DuplicateRunError(
                    f"incident {incident_id} already has active agent run #{active.id} "
                    f"(status: {active.status})"
                )
        run = AgentRun(
            asset_id=asset_id,
            incident_id=incident_id,
            status="running",
            current_phase="observe",
            trigger=trigger,
            max_iterations=max_iterations,
            evidence_domain=evidence_domain,
        )
        session.add(run)
        session.flush()
        emit_event(
            session,
            "agent.run.started",
            {
                "run_id": run.id,
                "asset_id": asset_id,
                "incident_id": incident_id,
                "trigger": trigger,
            },
        )
        session.commit()
        return cls(session, run.id)

    # ---------------------------------------------------------------- plumbing

    def _next_step_seq(self) -> int:
        n = self.session.scalar(
            select(func.count()).select_from(AgentStep).where(AgentStep.run_id == self.run.id)
        )
        return (n or 0) + 1

    def _next_call_seq(self) -> int:
        n = self.session.scalar(
            select(func.count())
            .select_from(AgentToolCall)
            .where(AgentToolCall.run_id == self.run.id)
        )
        return (n or 0) + 1

    def _begin_step(self, phase: str) -> AgentStep:
        step = AgentStep(
            run_id=self.run.id, seq=self._next_step_seq(), phase=phase, status="completed"
        )
        self.session.add(step)
        self.session.flush()
        return step

    def _finish_step(
        self, step: AgentStep, reasoning_summary: str, output: dict, status: str = "completed"
    ):
        step.reasoning_summary = reasoning_summary
        step.output_json = json.dumps(output)
        step.status = status
        step.completed_at = utcnow()
        self.session.flush()

    def _call(self, step: AgentStep, tool_name: str, **kwargs) -> tools.ToolResult:
        """Invoke a registered tool and persist the audit record."""
        fn, _is_write = tools.TOOLS[tool_name]
        result = fn(self.session, **kwargs)
        record = AgentToolCall(
            run_id=self.run.id,
            step_id=step.id,
            seq=self._next_call_seq(),
            tool_name=tool_name,
            arguments_json=json.dumps(kwargs, default=str),
            result_summary=result.summary,
            result_json=json.dumps(result.result, default=str),
            trust_label=result.trust_label,
            injection_flag=result.injection_flag,
        )
        self.session.add(record)
        self.session.flush()
        return result

    # ---------------------------------------------------------------- control

    def advance(self) -> AgentRun:
        """Execute exactly one phase transition and persist. Safe to call
        repeatedly; no-ops (returning current state) when blocked or done."""
        run = self.run
        if run.status in ("completed", "failed", "escalated"):
            return run
        if run.status == "awaiting_evidence":
            return run  # resumes only after a validated observation is submitted
        if run.status == "awaiting_approval":
            action = self._latest_action()
            if action is not None and action.status == "proposed":
                return run  # blocked on the human decision; polling costs no iterations
        if run.iterations_used >= run.max_iterations:
            run.status = "failed"
            run.summary = f"Aborted: max_iterations ({run.max_iterations}) reached in phase {run.current_phase}."
            self.session.commit()
            return run

        handler = {
            "observe": self._phase_observe,
            "diagnose": self._phase_diagnose,
            "plan": self._phase_plan,
            "act": self._phase_act,
            "verify": self._phase_verify,
            "learn": self._phase_learn,
        }[run.current_phase]

        if run.status == "paused":
            run.status = "running"  # explicit advance() resumes a paused run
        run.iterations_used += 1
        prev_status = run.status
        handler()
        if run.status != prev_status and run.status in (
            "awaiting_evidence",
            "awaiting_approval",
            "completed",
            "failed",
            "escalated",
            "paused",
        ):
            emit_event(
                self.session,
                f"agent.run.{run.status}",
                {
                    "run_id": run.id,
                    "asset_id": run.asset_id,
                    "incident_id": run.incident_id,
                    "phase": run.current_phase,
                },
            )
        run.updated_at = utcnow()
        self.session.commit()
        # Relationship collections may have been lazy-loaded before this phase
        # inserted rows; expire them so callers always see fresh state.
        self.session.expire(run, ["steps", "tool_calls", "actions", "evidence_requests"])
        return run

    def run_until_blocked(self) -> AgentRun:
        """Advance until awaiting approval, completed, or failed."""
        prev = None
        while self.run.status == "running" and prev != (
            self.run.current_phase,
            self.run.iterations_used,
        ):
            prev = (self.run.current_phase, self.run.iterations_used)
            self.advance()
        return self.run

    # ----------------------------------------------------------------- phases

    def _phase_observe(self):
        step = self._begin_step("observe")
        profile = self._call(step, "get_asset_profile", asset_id=self.run.asset_id)
        readings = self._call(
            step,
            "get_recent_sensor_readings",
            asset_id=self.run.asset_id,
            time_window_hours=48,
            agent_run_id=self.run.id,
        )
        signals = readings.result["signals"]
        anomalies = readings.result["anomalies"]

        if self.run.incident_id:
            incident = self.session.get(Incident, self.run.incident_id)
            if incident and incident.status == "active":
                incident.status = "investigating"

        symptoms = sorted(SIGNAL_SYMPTOMS[s] for s in signals if s in SIGNAL_SYMPTOMS)
        self._finish_step(
            step,
            reasoning_summary=(
                f"Observed {profile.result['name']}: {len(anomalies)} sensor anomaly(ies); "
                f"signals detected: {', '.join(sorted(signals)) or 'none'}."
            ),
            output={"signals": signals, "anomalies": anomalies, "symptoms": symptoms},
        )
        self.run.current_phase = "diagnose"

    def _phase_diagnose(self):
        step = self._begin_step("diagnose")
        observe = self._step_output("observe")
        symptoms: list[str] = observe.get("symptoms", []) or ["degradation"]
        asset = self.session.get(Asset, self.run.asset_id)
        ev = self._gather_diagnosis_evidence(step, asset, symptoms)

        tests_run: list[dict] = list(ev.observations.result["observations"])
        trusted = {**observe.get("signals", {}), **ev.readings.result["signals"]}
        texts = diagnosis.untrusted_texts(ev.cases, ev.work_orders, ev.inspections, ev.manuals)
        signals, sources = diagnosis.merge_evidence_signals(trusted, tests_run, texts)
        case_matches = ev.cases.result["matches"]
        hypotheses = diagnosis.score_failure_modes(
            asset.asset_type, self.run.asset_id, signals, sources, case_matches
        )
        abstain_reason, requested_evidence = diagnosis.abstention(
            asset.asset_type, asset.name, hypotheses
        )
        injected = any(
            tc.injection_flag for tc in (ev.cases, ev.work_orders, ev.inspections, ev.manuals)
        )
        injection_note = (
            " Instruction-like text detected in retrieved evidence; treated as untrusted data and ignored."
            if injected
            else ""
        )

        # The snapshot reuses the evidence recorded above. Commit before the
        # provider call so no write locks are held across its latency.
        snapshot = self._build_snapshot(
            asset,
            hypotheses,
            abstain_reason,
            requested_evidence,
            signals,
            ev.cases,
            ev.work_orders,
            ev.inspections,
            ev.manuals,
            ev.prediction,
            tests_run,
        )
        self.session.commit()
        outcome = run_reasoning(snapshot, settings, provider=self.reasoning_provider)
        result = outcome.result

        # Next-best evidence: when the top two causes are tied (or the provider
        # asks), persist a request for an independent observation and pause.
        # A result is never manufactured from existing telemetry.
        provider_test = result.requested_test if result.disposition == "request_evidence" else None
        policy_test = (
            diagnosis.discriminating_test(asset.asset_type, hypotheses, tests_run)
            if abstain_reason and "ambiguous" in abstain_reason
            else None
        )
        requested_test = provider_test or policy_test
        if requested_test and len(tests_run) < settings.max_diagnostic_rounds:
            request = diagnostic_evidence.create_request(
                self.session,
                self.run,
                requested_test,
                requested_by=(
                    outcome.meta.get("provider", "provider")
                    if provider_test
                    else "deterministic_policy"
                ),
                rationale=(
                    abstain_reason
                    or result.public_rationale
                    or "independent evidence requested before diagnosis"
                ),
                hypotheses=[h["cause"] for h in hypotheses[:2]],
            )
            output = self._diagnose_output(
                ev,
                hypotheses,
                signals,
                injected,
                abstained=abstain_reason is not None or result.disposition == "abstain",
                abstain_reason=abstain_reason,
                requested_evidence=requested_evidence,
                tests_run=tests_run,
                reasoning=self._reasoning_record(
                    outcome.meta,
                    result,
                    snapshot,
                    requested_test=requested_test,
                    decision="awaiting_evidence",
                    provider_requested_test=provider_test,
                    policy_requested_test=policy_test,
                ),
                paused_for_evidence=True,
                evidence_request_id=request.id,
            )
            self._finish_step(
                step,
                (
                    f"Queried case memory first ({len(case_matches)} precedent(s)); "
                    f"paused for independent evidence '{requested_test}'. No action proposed."
                ),
                output,
                status="blocked",
            )
            return

        # Deterministic policy applies the validated result. A provider that
        # didn't fall back may abstain or rerank within the allowed causes;
        # phase transitions stay with the loop.
        if not outcome.meta.get("fallback"):
            if result.disposition == "abstain":
                abstain_reason = abstain_reason or (result.abstain_reason or "provider abstained")
                requested_evidence = requested_evidence or result.missing_evidence
            elif (
                result.disposition == "rank_hypotheses" and not abstain_reason and result.hypotheses
            ):
                hypotheses = diagnosis.apply_provider_ranking(hypotheses, result.hypotheses)

        output = self._diagnose_output(
            ev,
            hypotheses,
            signals,
            injected,
            abstained=abstain_reason is not None,
            abstain_reason=abstain_reason,
            requested_evidence=requested_evidence,
            tests_run=tests_run,
            reasoning=self._reasoning_record(
                outcome.meta,
                result,
                snapshot,
                requested_test=result.requested_test,
                decision=(
                    "escalate_without_action" if abstain_reason is not None else "continue_to_plan"
                ),
            ),
        )

        if abstain_reason is not None:
            self._finish_step(
                step,
                reasoning_summary=(
                    f"Queried case memory first ({len(case_matches)} precedent(s)), then "
                    f"gathered evidence. ABSTAINED from diagnosis: {abstain_reason}. Requested: "
                    f"{'; '.join(requested_evidence)}.{injection_note}"
                ),
                output=output,
            )
            self.run.status = "escalated"
            self.run.summary = (
                f"Agent abstained from diagnosing {asset.name}: {abstain_reason}. "
                f"Human review requested; no maintenance action was proposed."
            )
            return

        top = hypotheses[0]
        self._finish_step(
            step,
            reasoning_summary=(
                f"Queried case memory first ({len(case_matches)} precedent(s)), then sensors, "
                f"work orders, inspections, manuals, and failure prediction "
                f"[{ev.prediction.result['model_source']}]. Top hypothesis: {top['cause']} "
                f"(confidence {top['confidence']:.0%}).{injection_note}"
            ),
            output=output,
        )
        self.run.current_phase = "plan"

    def _gather_diagnosis_evidence(self, step, asset, symptoms) -> _DiagnosisEvidence:
        """Fetch every piece of diagnosis evidence through audited tool calls.
        Case memory is always queried first."""
        run_id, asset_id, domain = self.run.id, self.run.asset_id, self.run.evidence_domain
        cases = self._call(
            step,
            "query_case_memory",
            asset_id=asset_id,
            symptoms=symptoms,
            evidence_domain=domain,
        )
        readings = self._call(
            step,
            "get_recent_sensor_readings",
            asset_id=asset_id,
            time_window_hours=48,
            agent_run_id=run_id,
        )
        work_orders = self._call(
            step, "get_open_work_orders", asset_id=asset_id, evidence_domain=domain
        )
        inspections = self._call(
            step, "get_recent_inspections", asset_id=asset_id, evidence_domain=domain
        )
        manuals = self._call(
            step,
            "search_maintenance_manuals",
            query=" ".join(symptoms) or asset.asset_type,
            asset_type=asset.asset_type,
            evidence_domain=domain,
        )
        prediction = self._call(
            step, "run_failure_prediction", asset_id=asset_id, agent_run_id=run_id
        )
        importance = self._call(
            step, "get_feature_importance", asset_id=asset_id, agent_run_id=run_id
        )
        observations = self._call(step, "get_diagnostic_evidence", run_id=run_id)
        return _DiagnosisEvidence(
            cases, readings, work_orders, inspections, manuals, prediction, importance, observations
        )

    @staticmethod
    def _reasoning_record(meta, result, snapshot, *, requested_test, decision, **extra) -> dict:
        """The persisted public reasoning record: provider metadata, the
        validated structured result, and the evidence manifest. Never hidden
        chain-of-thought."""
        return {
            **meta,
            "public_rationale": result.public_rationale,
            "missing_evidence": result.missing_evidence,
            "requested_test": requested_test,
            "disposition": result.disposition,
            "orchestrator_decision": decision,
            **extra,
            "validated_result": result.model_dump(),
            "evidence_snapshot": snapshot.public_manifest(),
        }

    @staticmethod
    def _diagnose_output(
        ev,
        hypotheses,
        signals,
        injected,
        *,
        abstained,
        abstain_reason,
        requested_evidence,
        tests_run,
        reasoning,
        **extra,
    ) -> dict:
        return {
            "hypotheses": hypotheses,
            "signals": signals,
            "risk": ev.prediction.result["risk"],
            "model_source": ev.prediction.result["model_source"],
            "feature_importance": ev.importance.result["importance"],
            "injection_detected": injected,
            "abstained": abstained,
            "abstain_reason": abstain_reason,
            "requested_evidence": requested_evidence,
            "diagnostic_tests": tests_run,
            **extra,
            "reasoning": reasoning,
        }

    def _phase_plan(self):
        step = self._begin_step("plan")
        diagnose = self._step_output("diagnose")
        top = diagnose["hypotheses"][0]

        asset = self.session.get(Asset, self.run.asset_id)
        techs = self._call(step, "get_technician_availability", asset_type=asset.asset_type)
        available = [t for t in techs.result["technicians"] if t["available"]]
        technician_id = available[0]["technician_id"] if available else None

        trigger_score = self._call(
            step, "run_failure_prediction", asset_id=self.run.asset_id, agent_run_id=self.run.id
        )
        # Verification must compare like with like. The MetroPT score is the
        # historical trigger; the simulated maintenance environment cannot
        # currently generate the full 15-channel model input, so its outcome
        # is measured before/after with the same labeled facility-risk adapter.
        verification_baseline = self._call(
            step, "run_verification_baseline", asset_id=self.run.asset_id, agent_run_id=self.run.id
        )

        diagnosis = {
            "cause": top["cause"],
            "confidence": top["confidence"],
            "action": top["action"],
            "technician_id": technician_id,
            "risk_before": verification_baseline.result["risk"],
            "risk_source": verification_baseline.result["model_source"],
            "risk_semantics": verification_baseline.result.get(
                "score_semantics", "simulated facility risk score"
            ),
            "trigger_score": trigger_score.result["risk"],
            "trigger_source": trigger_score.result["model_source"],
            "trigger_score_semantics": trigger_score.result.get(
                "score_semantics", "facility risk score"
            ),
            "trigger_threshold": trigger_score.result.get("threshold"),
            "trigger_window_end": trigger_score.result.get("window_end"),
            "trigger_prediction_id": trigger_score.result.get("prediction_id"),
            "replay_run_id": trigger_score.result.get("replay_run_id"),
            "evidence_refs": sorted({src for ev in top["evidence"] for src in ev["sources"]}),
        }
        proposal = self._call(
            step,
            "propose_work_order",
            asset_id=self.run.asset_id,
            diagnosis=diagnosis,
            run_id=self.run.id,
        )

        self._finish_step(
            step,
            reasoning_summary=(
                f"Planned '{top['action']['title']}' for root cause '{top['cause']}' "
                f"(trigger score {trigger_score.result['risk']:.3f} "
                f"[{trigger_score.result['model_source']}]; verification baseline "
                f"{verification_baseline.result['risk']:.2f} "
                f"[{verification_baseline.result['model_source']}]). "
                f"Action #{proposal.result['action_id']} "
                f"proposed — waiting for human approval."
            ),
            output={"action_id": proposal.result["action_id"], "diagnosis": diagnosis},
        )
        self.run.current_phase = "act"
        self.run.status = "awaiting_approval"

    def _phase_act(self):
        action = self._latest_action()
        if action is None:
            step = self._begin_step("act")
            self._finish_step(step, "No action to act on — failing run.", {}, status="failed")
            self.run.status = "failed"
            return

        if action.status == "proposed":
            # Still waiting on the human. Do not create a step; stay blocked.
            self.run.status = "awaiting_approval"
            return

        step = self._begin_step("act")
        if action.status == "rejected":
            self._finish_step(
                step,
                f"Action #{action.id} rejected by {action.decided_by or 'operator'}; no maintenance executed.",
                {"action_id": action.id, "decision": "rejected"},
            )
            self.run.status = "completed"
            self.run.summary = (
                "Proposed action rejected by operator. No changes made to the facility."
            )
            return

        if action.status == "approved":
            executed = self._call(step, "execute_approved_action", action_id=action.id)
            self.run.attempts_used += 1
            self._finish_step(
                step,
                f"Executed approved action #{action.id} (approved by {action.decided_by}): "
                f"work order #{executed.result['work_order_id']} completed (simulated); "
                f"outcome pending verification (attempt {self.run.attempts_used}/{self.run.max_attempts}).",
                executed.result,
            )
            self.run.status = "running"
            self.run.current_phase = "verify"
            return

        # 'executed' (re-entry after crash between act and verify): just move on.
        self.run.status = "running"
        self.run.current_phase = "verify"

    def _phase_verify(self):
        """Classify the observed outcome and branch the workflow.

        passed       → Learn (mitigation + validated-success memory happen there)
        inconclusive → pause; persist what we're waiting for; re-verify on resume
        failed       → persist a validated-failure memory; re-diagnose within the
                       attempt budget (any new action needs fresh approval) or escalate
        adverse      → persist negative memory, raise incident severity, escalate.
                       Never auto-execute another action.
        """
        step = self._begin_step("verify")
        verification = self._call(step, "verify_outcome", agent_run_id=self.run.id)
        r = verification.result
        outcome = r["outcome"]
        base = (
            f"Verified outcome: risk {r['risk_before']:.2f} → {r['risk_after']:.2f} "
            f"(Δ {r['risk_delta']:+.2f}) [{r['model_source']}] — {outcome.upper()}: {r['outcome_reason']}."
        )

        if outcome == "passed":
            self._finish_step(step, base + " Proceeding to Learn.", r)
            self.run.current_phase = "learn"
            return

        if outcome == "inconclusive":
            waiting_for = (
                f"at least {4} post-action readings per sensor with a clear risk signal "
                f"(currently {r['post_action_readings']})"
            )
            r["waiting_for"] = waiting_for
            self._finish_step(
                step,
                base + f" Pausing until more telemetry arrives (waiting for: {waiting_for}).",
                r,
                status="blocked",
            )
            # Phase stays 'verify'; an explicit advance() re-runs verification.
            self.run.status = "paused"
            self.run.summary = (
                f"Verification inconclusive after action; run paused waiting for {waiting_for}. "
                f"No success has been claimed and the incident remains open."
            )
            return

        # failed or adverse: persist the attempt truthfully as negative memory.
        self._record_failed_attempt(r, adverse=(outcome == "adverse"))

        if outcome == "adverse":
            if self.run.incident_id:
                incident = self.session.get(Incident, self.run.incident_id)
                if incident:
                    incident.severity = "critical"
                    incident.status = "investigating"
            self._finish_step(
                step,
                base + " Escalating to humans; incident severity raised. "
                "No further automatic action will be taken.",
                r,
            )
            self.run.status = "escalated"
            self.run.summary = (
                f"ADVERSE outcome after approved action: risk rose "
                f"{r['risk_before']:.2f} → {r['risk_after']:.2f}. Incident severity raised to "
                f"critical and the run escalated for human intervention."
            )
            return

        # failed
        if self.run.attempts_used < self.run.max_attempts:
            self._finish_step(
                step,
                base + f" Re-entering Diagnose to replan "
                f"(attempt {self.run.attempts_used}/{self.run.max_attempts} consumed); "
                "any new action will require fresh human approval.",
                r,
            )
            self.run.current_phase = "diagnose"
            return

        self._finish_step(
            step,
            base + f" Attempt budget exhausted "
            f"({self.run.attempts_used}/{self.run.max_attempts}); escalating to humans.",
            r,
        )
        self.run.status = "escalated"
        self.run.summary = (
            f"Verification FAILED after {self.run.attempts_used} approved attempt(s): "
            f"risk {r['risk_before']:.2f} → {r['risk_after']:.2f} did not meet the resolution "
            f"criteria. Escalated for human intervention; incident remains open."
        )

    def _record_failed_attempt(self, verify_result: dict, adverse: bool = False) -> None:
        """Persist a failed/adverse intervention as VALIDATED-FAILURE memory.

        Negative evidence: never appears as a successful resolution and never
        positively boosts a diagnosis; a matching validated failure on the same
        asset applies a small penalty (NEGATIVE_PRECEDENT_PENALTY)."""
        diagnose = self._step_output("diagnose")
        observe = self._step_output("observe")
        action = self._latest_action()
        payload = json.loads(action.payload_json) if action else {}
        asset = self.session.get(Asset, self.run.asset_id)
        top = (
            diagnose["hypotheses"][0]
            if diagnose.get("hypotheses")
            else {"cause": "unknown", "confidence": 0}
        )
        kind = "adverse reaction" if adverse else "failed intervention"
        entry = CaseMemory(
            asset_id=asset.id,
            asset_type=asset.asset_type,
            evidence_domain=self.run.evidence_domain,
            title=f"{asset.name} {kind}: '{payload.get('title', 'action')}' "
            f"(run #{self.run.id}, attempt {self.run.attempts_used})",
            symptoms_json=json.dumps(observe.get("symptoms", [])),
            root_cause=top["cause"],
            resolution=payload.get("title", ""),
            outcome=(
                f"Verification {'ADVERSE' if adverse else 'FAILED'}: risk "
                f"{verify_result['risk_before']:.2f} → {verify_result['risk_after']:.2f}. "
                f"{verify_result['outcome_reason']}"
            ),
            risk_reduction=verify_result.get("risk_delta", 0.0),
            narrative=(
                f"Hypothesis '{top['cause']}' led to approved action "
                f"'{payload.get('title', '')}', but verification observed "
                f"{'worsening conditions' if adverse else 'no adequate risk reduction'}. "
                f"Recorded as negative evidence."
            ),
            source_run_id=self.run.id,
            provenance="agent",
            validation_status="validated_failure",
            validated_by=f"verification:{verify_result.get('verification_id', '')}",
        )
        self.session.add(entry)
        self.session.flush()

    def _phase_learn(self):
        """Only a PASSED verification reaches Learn. Mitigation and
        validated-success memory happen here and nowhere else."""
        step = self._begin_step("learn")
        diagnose = self._step_output("diagnose")
        observe = self._step_output("observe")
        verify = self._step_output("verify")
        action = self._latest_action()
        payload = json.loads(action.payload_json) if action else {}
        asset = self.session.get(Asset, self.run.asset_id)

        if not verify or verify.get("outcome") != "passed":
            # Defensive: Learn must never fabricate success without a passed
            # verification (the memory-validity contract depends on it).
            self._finish_step(
                step,
                "Learn refused: no passed verification exists for this run.",
                {},
                status="failed",
            )
            self.run.status = "escalated"
            self.run.summary = "Learn phase reached without a passed verification — escalated."
            return

        top = diagnose["hypotheses"][0]
        postmortem = CaseMemory(
            asset_id=asset.id,
            asset_type=asset.asset_type,
            evidence_domain=self.run.evidence_domain,
            title=f"{asset.name} {top['cause']} (agent run #{self.run.id})",
            symptoms_json=json.dumps(observe.get("symptoms", [])),
            root_cause=top["cause"],
            resolution=payload.get("title", ""),
            outcome=(
                f"Risk reduced {verify['risk_before']:.2f} → {verify['risk_after']:.2f} "
                f"after approved maintenance (verification passed)."
            ),
            risk_reduction=verify.get("risk_delta", 0.0),
            narrative=(
                f"Symptoms: {', '.join(observe.get('symptoms', []))}. Diagnosed {top['cause']} "
                f"with confidence {top['confidence']:.0%} from sensor signals and precedent cases. "
                f"Human-approved '{payload.get('title', '')}' resolved the incident."
            ),
            source_run_id=self.run.id,
            # Validation comes from the VerificationResult, not from the agent
            # itself — see docs/agentic-loop.md §memory validity.
            provenance="agent",
            validation_status="validated_success",
            validated_by=f"verification:{verify.get('verification_id', '')}",
        )
        self.session.add(postmortem)

        asset.status = "online"  # recovery observed by the passed verification
        if self.run.incident_id:
            incident = self.session.get(Incident, self.run.incident_id)
            if incident:
                incident.status = "mitigated"

        self.session.flush()
        self._finish_step(
            step,
            f"Stored postmortem #{postmortem.id} in case memory; incident marked mitigated.",
            {"case_memory_id": postmortem.id},
        )
        self.run.status = "completed"
        self.run.summary = (
            f"{asset.name}: {top['cause']} diagnosed (confidence {top['confidence']:.0%}), "
            f"approved maintenance executed, risk {verify['risk_before']:.2f} → {verify['risk_after']:.2f}. "
            f"Postmortem saved to case memory."
        )

    # ---------------------------------------------------------------- helpers

    def _step_output(self, phase: str) -> dict:
        step = self.session.scalars(
            select(AgentStep)
            .where(
                AgentStep.run_id == self.run.id,
                AgentStep.phase == phase,
                AgentStep.status == "completed",
            )
            .order_by(AgentStep.seq.desc())
        ).first()
        return json.loads(step.output_json or "{}") if step else {}

    def _build_snapshot(
        self,
        asset,
        hypotheses,
        abstain_reason,
        requested_evidence,
        signals,
        cases,
        work_orders,
        inspections,
        manuals,
        prediction,
        diagnostic_tests_run,
    ) -> EvidenceSnapshot:
        """Immutable provider snapshot from the ALREADY-audited tool results —
        no re-retrieval side channel, free text stays JSON-escaped data."""
        items: list[EvidenceItem] = [
            EvidenceItem(
                ref="sensor_stats",
                source_type="sensor_stats",
                trust="trusted_system",
                facts={
                    "signals": signals,
                    "stats": [
                        {k: st[k] for k in ("kind", "latest", "baseline", "recent_mean")}
                        for st in prediction.result.get("stats", [])
                    ],
                },
            ),
            EvidenceItem(
                ref="failure_prediction",
                source_type="failure_prediction",
                trust="trusted_system",
                facts={
                    k: prediction.result.get(k)
                    for k in (
                        "risk",
                        "threshold",
                        "risk_band",
                        "alert",
                        "shadow",
                        "model_source",
                        "feature_version",
                        "policy_mode",
                        "policy_version",
                        "operating_mode",
                        "window_start",
                        "window_end",
                        "prediction_id",
                        "replay_run_id",
                        "explanation",
                        "score_semantics",
                    )
                },
            ),
        ]
        for case in cases.result["matches"]:
            items.append(
                EvidenceItem(
                    ref=case["narrative"]["ref"],
                    source_type="case_memory",
                    trust="untrusted_evidence",
                    facts={
                        "title": case["title"],
                        "root_cause": case["root_cause"],
                        "validation_status": case.get("validation_status"),
                        "symptom_overlap": case["symptom_overlap"],
                        "evidence_domain": case.get("evidence_domain"),
                    },
                    text=case["narrative"]["content"],
                    injection_flag=bool(case["narrative"]["injection_flags"]),
                )
            )
        for wo in work_orders.result["open_work_orders"]:
            items.append(
                EvidenceItem(
                    ref=wo["notes"]["ref"],
                    source_type="work_order_note",
                    trust="untrusted_evidence",
                    facts={
                        "title": wo["title"],
                        "status": wo["status"],
                        "priority": wo["priority"],
                        "evidence_domain": wo.get("evidence_domain"),
                    },
                    text=wo["notes"]["content"],
                    injection_flag=bool(wo["notes"]["injection_flags"]),
                )
            )
        for insp in inspections.result["inspections"]:
            items.append(
                EvidenceItem(
                    ref=insp["notes"]["ref"],
                    source_type="inspection_note",
                    trust="untrusted_evidence",
                    facts={
                        "summary": insp["summary"],
                        "evidence_domain": insp.get("evidence_domain"),
                    },
                    text=insp["notes"]["content"],
                    injection_flag=bool(insp["notes"]["injection_flags"]),
                    timestamp=insp["ts"],
                )
            )
        for chunk in manuals.result["chunks"]:
            items.append(
                EvidenceItem(
                    ref=chunk["content"]["ref"],
                    source_type="manual_chunk",
                    trust="untrusted_evidence",
                    facts={
                        "doc_title": chunk["doc_title"],
                        "section": chunk["section"],
                        "evidence_domain": chunk.get("evidence_domain"),
                    },
                    text=chunk["content"]["content"],
                    injection_flag=bool(chunk["content"]["injection_flags"]),
                )
            )
        for t in diagnostic_tests_run:
            items.append(
                EvidenceItem(
                    ref=t["ref"],
                    source_type="diagnostic_evidence",
                    trust="structured_observation",
                    facts={
                        "test_id": t["test_id"],
                        "label": t["label"],
                        "result": t["result"],
                        "source": t["source"],
                        "observed_at": t["observed_at"],
                    },
                )
            )

        refs = {i.ref for i in items}
        baseline = {
            "hypotheses": [
                {
                    "cause_id": h["cause"],
                    "label": h["cause"],
                    "confidence": h["confidence"],
                    "refs": sorted({s for ev in h["evidence"] for s in ev["sources"] if s in refs}),
                }
                for h in hypotheses
            ],
            "abstain": abstain_reason is not None,
            "abstain_reason": abstain_reason or "",
            "requested_test": None,
            "rationale": (
                f"deterministic signature scoring over {len(items)} evidence items; "
                + (
                    f"abstained: {abstain_reason}"
                    if abstain_reason
                    else f"top: {hypotheses[0]['cause']}"
                    if hypotheses
                    else "no hypotheses"
                )
            ),
        }
        completed_tests = {t["test_id"] for t in diagnostic_tests_run}
        return EvidenceSnapshot(
            asset_label=f"{asset.name} ({asset.asset_type})",
            items=tuple(items),
            baseline=baseline,
            allowed_cause_ids=tuple(
                m["cause"] for m in knowledge.failure_modes_for(asset.asset_type)
            ),
            allowed_tests=tuple(
                test_id
                for test_id in knowledge.tests_for(asset.asset_type)
                if test_id not in completed_tests
            ),
            prompt_version=settings.reasoning_prompt_version,
        )

    def _latest_action(self) -> AgentAction | None:
        return self.session.scalars(
            select(AgentAction)
            .where(AgentAction.run_id == self.run.id)
            .order_by(AgentAction.id.desc())
        ).first()
