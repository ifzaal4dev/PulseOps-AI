# Design decisions

Short records of the choices that shape the system, and why.

## ADR-001: SQLite for development and tests, PostgreSQL in containers
Zero-setup local runs and fast tests matter more than parity during
development. All access goes through SQLAlchemy, so switching is a matter of
`PULSEOPS_DATABASE_URL`. Alembic owns the schema. The same test suite runs on
both databases.

## ADR-002: A deterministic diagnosis engine is the baseline
The loop, tools, persistence, approval gate and eval harness are the
architecture; the reasoning engine plugs into it. A deterministic signature
scorer (`knowledge.py`, `diagnosis.py`) keeps the main path reproducible and
testable in CI without API keys, and gives any LLM a baseline to be measured
against.

## ADR-003: Agent state lives only in the database
`AgentEngine(session, run_id)` rebuilds everything from `agent_runs` and its
child tables. There are no in-memory queues or background workers. That gives
resumable runs and a complete audit trail.

## ADR-004: Injection defence is structural, not detector-based
The keyword whitelist is the boundary between free text and the diagnosis.
Regex detection of instruction-like text only adds audit flags, so a better or
worse detector never changes what the agent does.

## ADR-005: The approval check lives inside the execute tool
`execute_approved_action` checks for a recorded human approval itself, rather
than relying on the route or the engine, so no other call path can skip it.

## ADR-006: The synthetic facility uses a labelled mock adapter
The synthetic assets need risk scores, and there is no real data for them. The
mock is deterministic and monotonic (so verification behaves sensibly) and
stamps `model_source="mock_demo_v0"` on every response, which the UI shows.
The trained MetroPT model is served separately (`services/inference.py`) and
only scores the replayed compressor.

## ADR-007: Simulated maintenance writes recovery readings forward in time
Executing an approved action appends about six hours of readings from the
asset's observation point, shaped by the configured outcome profile.
Verification then measures the change. It's deterministic and clearly a
simulation. The readings carry the action's id in their `source_id`, so
verification only counts telemetry caused by that action (ADR-018).

## ADR-008: Keyword retrieval
Manual search and case-memory matching use keyword overlap. The tool
signatures (`search_maintenance_manuals`, `query_case_memory`) are stable, so
an embedding-based version could replace them without touching callers.

## ADR-009: A small hand-built Next.js front end
No chart library (charts are SVG) and no component kit, beyond Tailwind.
Installs stay fast and the visuals stay under direct control.

## ADR-010: The eval harness fails CI on safety gates
For example, a false approval or a diagnosis that doesn't query case memory
first exits non-zero. Accuracy metrics inform; safety metrics block.

## ADR-011: Execution and the environment's response are separate
If the execute tool wrote healthy readings itself, every approved action
would succeed. Instead `services/environment.py` decides how the asset
responds (success, partial, failure, adverse, sparse, or no telemetry), and
verification observes the result. Outcome overrides are in-process because
this is a demo simulator.

## ADR-012: Verification outcomes control the workflow
Verification returns passed, failed, inconclusive, or adverse
(`services/verification.py`). Only passed mitigates the incident and writes
validated memory. Failed re-plans within a persisted attempt budget, with a
fresh approval each time, or escalates. Inconclusive pauses until more
telemetry arrives. Adverse escalates and raises the incident's severity.

## ADR-013: Memory validity comes from verification, not from the agent
Case memory carries provenance, a validation status, and what validated it.
Only validated successes boost a diagnosis; a validated failure on the same
asset counts against it; provisional, rejected and superseded entries are
display-only. The agent can't turn its own guesses into trusted precedent.

## ADR-014: Abstain instead of proposing a generic work order
Unsupported asset types, weak evidence (`MIN_EVIDENCE_SCORE`), or tied top
hypotheses (`TIE_RATIO`) make the diagnosis abstain: it escalates with the
evidence it would need and proposes nothing.

## ADR-015: Timezone-aware UTC everywhere
All datetimes are aware UTC. `TZDateTime` maps to TIMESTAMPTZ on PostgreSQL
(sessions pinned to UTC) and to a UTC string convention on SQLite. `init_db`
migrates and seeds only an empty database, so startup is non-destructive.

## ADR-016: Integrity constraints and an event outbox
Outbox rows are written in the same transaction as the state change they
describe (run lifecycle, approvals, executions). Nothing consumes them yet;
they're an audit stream that a future worker could process. A unique
`source_id` per reading makes ingestion idempotent. A compare-and-swap makes
execution exactly-once, and a partial unique index plus an application check
allow only one active run per incident.

## ADR-017: Replay-derived data is scoped by replay run
Replaying the same interval twice produces the same source timestamps, so a
timestamp isn't a unique identity. Predictions, shadow alerts, persistence and
cooldown checks, feature reconstruction, history APIs and reset are all scoped
by `replay_run_id`. A replay whose alert was promoted can't be reset, because
its incident and investigation are part of the audit trail.

## ADR-018: The trigger score and the verification score stay separate
The MetroPT model needs all 15 historical channels; the simulated repair
produces synthetic recovery telemetry. Comparing a MetroPT score before with a
mock score after would mix two different measures. So the MetroPT score is kept
as the trigger and diagnostic evidence, and repair verification uses
`mock_demo_v0` on both sides, anchored to the trigger window before and to the
action's own telemetry after. Verification refuses a mismatched pair, and the
UI labels both.

## ADR-019: Show validated reasoning output, never hidden reasoning
The UI shows provider, model, latency and token metadata; the
schema-validated structured result; a short public rationale; and a manifest
of the evidence sent, with trust labels. Raw untrusted text stays in the
audited tool records. Hidden prompts and chain-of-thought are never stored or
shown.

## ADR-020: Model performance in the UI comes from the generated report
The Model Lab has no hand-entered metrics. A read-only API serves
`ml/reports/metropt3_eval.json`. The model loader checks the artifact's
recorded scikit-learn version before unpickling, and packaging pins it.

## ADR-021: The LLM can only rank, request evidence, or abstain
A provider's structured answer has exactly three dispositions:
`rank_hypotheses`, `request_evidence`, or `abstain`. It has no field for
proposing maintenance or escalating. Planning, phase changes, approval and
execution stay deterministic. Anything that fails validation falls back to
the deterministic diagnosis, with the reason recorded.

## ADR-022: Ambiguity pauses for independent evidence
When the top two causes are tied, the run moves to `awaiting_evidence` and
persists a request for one typed observation (for example an ultrasonic leak
survey or an oil sample result). No action is proposed. A result is never
derived from existing telemetry; it has to be submitted, with its source, and
Diagnose then resumes. Synthetic manuals, inspections, work orders and case
memory are kept out of investigations on real replayed data.

## ADR-023: Evaluate the alert rule the app actually runs
The app raises a shadow alert only after three consecutive windows over the
threshold, with hysteresis and a cooldown. `pulseops_ml/trigger.py` replays
that rule offline, a parity test keeps it in step with the app, and the report
scores it next to the single-window policy. The report also gives false
alarms per observed day, because the calendar span includes data gaps.

## ADR-024: Write requests must be JSON
The API rejects POST, PUT, PATCH and DELETE requests that aren't sent as
`application/json` (HTTP 415). That forces a CORS preflight, so another
website can't trigger an approval through a plain form or bodyless request.
This isn't authentication; there is none yet.
