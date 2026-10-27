# PulseOps AI — Architecture

## System overview

```
┌─────────────────────────────────────────────────────────────────────┐
│  apps/web — Next.js command center (dark enterprise UI)             │
│  Dashboard · Asset Detail · Agent Control Room · Work Orders · ML   │
└──────────────────────────────┬──────────────────────────────────────┘
                               │ REST (typed schemas.py contract)
┌──────────────────────────────▼──────────────────────────────────────┐
│  apps/api — FastAPI                                                  │
│                                                                      │
│  routes/          facility.py (dashboard, assets, WOs, case memory)  │
│                   agent.py (runs, evidence submission, advance,      │
│                   approve/reject — audited human decisions)          │
│  services/                                                           │
│    agent_loop.py  persisted state machine (Observe→…→Learn)          │
│    tools.py       14 explicit tools, every call audited              │
│    evidence.py    trust boundary: untrusted evidence envelopes,      │
│                   whitelist signal extraction, injection flags       │
│    knowledge.py   failure-mode signatures                            │
│    diagnosis.py   deterministic scoring, abstention, next evidence   │
│    failure_risk.py labeled mock adapter + replay-anchored evidence   │
│    inference.py   frozen MetroPT artifact + shared FeatureComputer   │
│    reasoning/     constrained hybrid LLM + deterministic fallback    │
│    diagnostic_    typed asynchronous independent-evidence requests   │
│      evidence.py  and deterministic signal mappings                  │
│    approval.py    human approval gate                                │
│  models.py        facility + agent domains (SQLAlchemy)              │
│  seed/            deterministic synthetic facility + golden scenario │
└──────────────────────────────┬──────────────────────────────────────┘
                               │ SQLAlchemy
                    ┌──────────▼──────────┐
                    │ SQLite (tests/dev)  │   · PostgreSQL (containers)
                    └─────────────────────┘

  ml/    real-dataset (UCI MetroPT-3) pipeline: ingest/train/evaluate,
         frozen artifacts, holdout-only event-aware reports
  eval/  agent eval harness: 14 scenarios, md+json reports, hard safety gates
```

## The live path

```
processed MetroPT-3 Parquet (real history, gitignored)
   │  bounded slices, all 15 channels
   ▼
ReplayPacer (speed × wall clock, bounded chunks, pause-responsive)
   → ReplayService.ingest_chunk  [ONE transaction: 7 analog readings +
     1 AssetOperatingState (8 digital channels) + cursor advance;
     source_id idempotency; outbox events]
   ▼
OnlineInferenceService (air_compressor ONLY)
   → reconstructs the 15-channel frame FROM THE DATABASE (never the Parquet)
   → pulseops_ml FeatureComputer.at  (exact offline == online parity)
   → metropt3_iforest_v1 risk + threshold + policy provenance
   → replay-scoped, idempotent ModelPrediction rows (shadow=true)
   ▼
shadow_trigger.process_prediction
   persistence (3 windows) + hysteresis (0.8×) + cooldown (60 m) + dedup
   → replay-scoped ShadowAlert (would-trigger record) — NEVER an Incident/AgentRun
   ▼
operator promotion (explicit human, audited)
   → [SYNTHETIC/DEMO] Incident → investigation resolves the exact
     triggering replay + prediction (never an arbitrary latest/mock score)
   → proposal keeps TWO named measurements separate:
     (a) MetroPT anomaly score = historical trigger/diagnostic evidence;
     (b) mock_demo_v0 facility risk before/after = simulated repair verifier.
     Verification rejects a cross-model delta and counts only telemetry
     correlated to the executed action.
```

Three clocks are kept distinct end-to-end: source/event time (dataset
timestamps), simulation time (replay clock), ingestion time (wall clock).
Multiple runs may replay the same source interval without sharing telemetry,
predictions, persistence streaks, or alerts. Reset removes only that run's
unpromoted derived state; a promoted run cannot be reset because its incident
and investigation are retained as an audit record.

## The core loop

`Observe → Diagnose → Plan → Act → Verify → Learn`, persisted as
`agent_runs` / `agent_steps` / `agent_tool_calls` / `agent_actions` /
`verification_results` / `case_memory`. See docs/agentic-loop.md for the
per-phase contract.

## Key design decisions (full log in ARCHITECTURE_DECISIONS.md)

* **DB is the only agent state.** `AgentEngine` is reconstructable from a run
  id — that's what makes runs resumable and the audit trail complete.
* **Hybrid, policy-owned diagnosis.** Weighted signature scoring remains the
  authoritative baseline. In hybrid/LLM mode a provider receives a bounded,
  injection-hardened evidence snapshot and may rerank allow-listed causes,
  cite stable refs, abstain, or request one allow-listed independent evidence
  type. The provider cannot propose maintenance or decide escalation. The
  deterministic engine owns phase transitions, tools, and approval; invalid
  or unavailable provider output fails closed.
* **Evidence requests are real workflow state.** A tied diagnosis creates a
  persisted request and enters `awaiting_evidence`; no result or action is
  fabricated. A typed operator/lab/sensor/import observation resumes Diagnose
  and is cited as `diagnostic_evidence:<id>`. Oil temperature never becomes
  an oil-quality verdict.
* **Evidence domains prevent demo leakage.** Synthetic facility manuals,
  inspections, work orders, and case memory are tagged
  `synthetic_facility`; operator-promoted MetroPT investigations use
  `metropt_public_replay` and cannot silently retrieve those synthetic
  records. `generic_reference` is the only deliberate cross-domain evidence
  class.
* **Public decision trace, not hidden CoT.** The DB stores provider/model/
  latency/token metadata, the validated structured diagnosis, concise public
  rationale, and an evidence provenance manifest. It never stores hidden
  chain-of-thought. UI citations resolve to audited tool calls.
* **Testable LLM configuration.** `POST /api/agent/reasoning-test`
  (services/reasoning/connectivity.py) runs the same registry → snapshot →
  prompt/schema → validator → fallback path against a labeled synthetic
  snapshot: no DB session, at most two HTTP attempts (second only on a
  transient 429/503), credential-redacted failure detail, and exactly one of
  two status lines so fallback output can never pose as an LLM response.
  `GET /api/trigger-policy` exposes the runtime shadow-trigger constants so
  the UI hardcodes nothing; `POST /api/demo/reset` (confirmation-gated) is
  the deterministic drop → migrate → reseed demo reset.
* **Trust labels at the tool boundary.** Free-text content is wrapped in
  untrusted-evidence envelopes at retrieval time; instruction-shaped text is
  flagged for audit but never interpreted.
* **Approval is server-side and unconditional.** `execute_approved_action`
  raises unless a human approved via the API. Nothing the agent reads or
  decides can flip that bit.
* **Like-for-like verification.** The real historical anomaly score is never
  compared numerically with the simulated post-maintenance facility risk.
  The proposal and UI expose both sources; before/after verification is
  correlated to one action and refuses a model-source mismatch.
* **The mock model is always labelled.** Every response from it carries
  `model_source="mock_demo_v0"`, and the UI shows the label.
* **Performance claims are source-backed.** The Model Lab reads the generated
  `ml/reports/metropt3_eval.json` through a read-only API projection; it shows
  recall, false alarms, post-onset delay, the operating-mode breakdown, the
  app's own alert rule, and the shadow-readiness blockers, rather than a
  hand-entered accuracy claim.

## Data flow of the golden demo

1. Seed builds Chiller-03 with a 48h vibration ramp + pressure decay and an
   active critical incident.
2. Dashboard ranks assets by mock-adapter risk → CH-03 at ~0.74.
3. `POST /api/agent/runs` starts a run; `advance` executes phases.
4. Diagnose queries case memory first (finds the Chiller-01 bearing-wear
   precedent), then sensors/WOs/inspections/manuals/prediction, scores
   hypotheses → bearing wear top-1.
5. Plan proposes an urgent work order; run blocks `awaiting_approval`.
6. Human approves → Act executes simulated maintenance (appends recovery
   readings, creates the work order) → Verify recomputes risk (~0.05) →
   Learn writes the postmortem and mitigates the incident.

The deliberately staged design for onboarding another facility database,
choosing family-vs-asset model tenancy, and separating onboarding/training/
maintenance agents is documented in [data-onboarding.md](data-onboarding.md).
