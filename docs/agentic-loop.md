# The Agentic Loop — contracts

This is the binding contract for `apps/api/pulseops_api/services/agent_loop.py`.
Tests in `apps/api/tests/` and the eval harness assert all of it. Breaking any
item below is a regression, not a refactor.

## State machine

Phases (canonical order): `observe → diagnose → plan → act → verify → learn`.
Diagnose/Plan/Act/Verify may REPEAT when verification fails, bounded by the
run's `attempts_used`/`max_attempts` budget (an attempt = one executed action).

Run statuses:

| status | meaning |
|---|---|
| running | agent is progressing |
| awaiting_evidence | Diagnose requested one typed, independent observation and is paused; no action exists. A validated submission explicitly resumes Diagnose. |
| awaiting_approval | a proposed action is blocked on a human decision |
| paused | waiting for more observations (inconclusive verification); explicit `advance()` resumes |
| completed | passed verification → learned; or action rejected cleanly |
| failed | internal error/iteration cap |
| escalated | human intervention required: abstention, adverse outcome, or attempt budget exhausted. Terminal for the agent. |

Persistence: one `agent_steps` row per phase execution, one `agent_tool_calls`
row per tool invocation (seq, args, result summary + full result JSON, trust
label, injection flag). `AgentEngine` holds no state outside the DB —
resumable from the run id after process death. Key transitions also write
transactional `outbox_events` rows.

At most one ACTIVE run (running/awaiting_evidence/awaiting_approval/paused) per incident —
enforced in `start_run` (DuplicateRunError → HTTP 409) and by a partial unique
index.

## Per-phase contracts

| Phase | Must do | Output (step.output_json) |
|---|---|---|
| observe | get_asset_profile, get_recent_sensor_readings; move incident → investigating | `signals`, `anomalies`, `symptoms` |
| diagnose | **query_case_memory FIRST**, then readings, open WOs, inspections, manuals, prediction, feature deviations, and previously submitted independent evidence; score failure modes; request evidence or abstain when the record cannot support a diagnosis | `hypotheses` (ranked, per-signal evidence + sources), `risk`, `model_source`, `injection_detected`, `abstained`, `abstain_reason`, `requested_evidence`, `diagnostic_tests`, `reasoning` |
| plan | technician availability, risk snapshot, propose_work_order → action `proposed`; run → `awaiting_approval` | `action_id`, `diagnosis` (incl. `risk_before`) |
| act | proposed: stay blocked · rejected: complete run, touch nothing · approved: execute_approved_action (consumes one attempt) | execution result (`outcome: pending_verification`) |
| verify | verify_outcome → VerificationResult with **outcome ∈ passed/failed/inconclusive/adverse**; branch the workflow (below) | verification payload (+`waiting_for` when inconclusive) |
| learn | ONLY reached on a passed verification: validated-success postmortem, asset back online, incident mitigated, run summary | `case_memory_id` |

## Verification outcomes control the workflow

Classifier: `services/verification.py` (documented thresholds: MIN_REDUCTION
0.10, RESOLVED_RISK_MAX 0.35, ADVERSE_MARGIN 0.05, MIN_POST_READINGS 4).

| outcome | consequences |
|---|---|
| passed | → Learn. The ONLY path to incident mitigation and validated-success memory. |
| failed | Persist a validated-failure memory (negative evidence). Re-enter Diagnose within the attempt budget — any new action requires a FRESH human approval — else escalate. Never mitigates. |
| inconclusive | Run pauses (`paused`), persists what it's waiting for; explicit resume re-runs verification. No success claims. |
| adverse | Persist negative memory, raise incident severity to critical, escalate. Never auto-execute another action. |

The environment's response to an executed action is decided independently in
`services/environment.py` (outcome profiles success/partial/failure/adverse/
sparse/none). Execution does not know or choose its outcome; verification
observes it. The simulated fast-forward of post-action telemetry is isolated
in that module.

## Memory validity

`case_memory` rows carry `provenance` (seeded|agent), `validation_status`
(validated_success | validated_failure | provisional | rejected | superseded)
and `validated_by`.

* Only **validated_success** precedents may positively boost a diagnosis
  (CASE_PRECEDENT_BOOST).
* **validated_failure** entries are negative evidence: never presented as
  resolutions; a matching failure on the same asset applies
  NEGATIVE_PRECEDENT_PENALTY.
* provisional / rejected / superseded entries are surfaced but never scored.
* The agent NEVER validates its own memory: `validated_by` cites the
  VerificationResult (`verification:<id>`) or a human/historical source.

Runs and every retrievable free-text record (manual, inspection, work order,
case memory) also carry an `evidence_domain`. Retrieval accepts only the
run's domain plus deliberately shared `generic_reference` records. A
`metropt_public_replay` investigation therefore cannot silently use seeded
`synthetic_facility` narratives as operational evidence.

## Abstention

The deterministic diagnostician abstains — escalates with concrete
`requested_evidence`, proposing NO action — when (`services/knowledge.py`):

* the asset type has no failure-mode signatures (no generic fallback exists),
* the top hypothesis scores below MIN_EVIDENCE_SCORE (2.0), or
* the runner-up is materially tied (≥ TIE_RATIO 0.8 of the leader).

## Independent evidence, not synthetic tests

When a typed observation can resolve a tied diagnosis and the persisted
`max_diagnostic_rounds` budget permits it, Diagnose creates one
`diagnostic_evidence_requests` row and moves the run to `awaiting_evidence`.
The result is not inferred from telemetry already in the snapshot. An
operator, laboratory, sensor integration, or import must submit one of the
schema-enumerated outcomes through the evidence API. The corresponding
`diagnostic_evidence_observations` row records identity, source, timestamp,
raw structured result, and its deterministic signal mapping; Diagnose then
re-runs from case-memory-first.

In particular, oil temperature is not oil quality. `oil_quality_bad` can
only come from a submitted `oil_sample_analysis` observation. Independent
oil pressure, oil-cooler delta, ultrasonic leak survey, and pressure-decay
results likewise use distinct signals. Free text is not accepted in result
objects and cannot influence scoring. If the budget is exhausted or no
allowed observation can discriminate the hypotheses, the agent abstains and
escalates without proposing maintenance.

## Tools (services/tools.py)

14 explicit tools; the `TOOLS` registry is the agent's entire capability
surface. Free-text results are wrapped per-item in untrusted-evidence
envelopes (`evidence.make_evidence`).

`get_diagnostic_evidence` is read-only and surfaces only schema-validated,
persisted observations. The legacy `run_diagnostic_test` compatibility
function is not in the tool registry and can only report `unavailable`; it
cannot manufacture a result.

Write tools:
* `propose_work_order` — creates a PROPOSED AgentAction only. Never a WorkOrder.
* `execute_approved_action` — hard gate: raises ApprovalRequiredError unless
  status is `approved`; a compare-and-set makes approved→executed exactly-once
  (concurrent executors are refused). Work order + environment response commit
  atomically with the flip.
* `verify_outcome` — observes post-action telemetry, classifies, persists.
  Re-verification appends a new VerificationResult.

## Hybrid reasoning and transparency

`agent_steps.reasoning_summary` is a concise audit line (eval gates ≤600
chars). Never store hidden chain-of-thought. Gemini/OpenRouter/Anthropic
providers receive a bounded JSON-escaped evidence snapshot and return the
strict `reasoning_v2` diagnostic contract: rank registered hypotheses,
request one allow-listed independent evidence type, or abstain. They have no
fields for proposing work, escalation, approval, execution, or incident
state. Store only the validated structured result, short public rationale,
citations, and provider metadata; deterministic policy owns every workflow
transition and all write gates.

## Injection resistance (see docs/security.md)

Untrusted evidence influences diagnosis exclusively through
`evidence.extract_signals` — exact keyword→signal lookups against
`knowledge.KEYWORD_SIGNALS`. Instructions embedded in evidence cannot reorder
tools, change the proposed action, or trigger execution.
`test_prompt_injection.py` proves this by diffing a clean run against a
poisoned run.

## Extending

* New reasoning providers: keep the narrow diagnostic-only interface,
  case-memory-first, deterministic fallback, JSON-escaped evidence snapshot,
  strict schema/citation validation, and the approval boundary.
* New failure modes: signatures + seed data + an eval scenario in the same PR.
* New write tools: propose → human approve → execute. No exceptions.
