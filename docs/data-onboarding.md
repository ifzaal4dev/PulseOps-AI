# Data onboarding and model-tenancy design

This is the recommended extension path for PulseOps beyond the single
MetroPT-3 demonstration. It is a design target, not a claim that arbitrary
customer databases are already supported.

## Decision

Do not build a free-running “database agent” that is allowed to inspect an
unknown schema, invent features, train a model, and deploy it in one step.
That combines the highest-risk data, ML, and production decisions behind an
unverifiable prompt.

Build a persisted onboarding workflow whose deterministic services do the
work and whose LLM may assist with bounded mapping and explanation:

1. **Connect and inventory.** A read-only connector records source identity,
   tables/topics, schema, units, timezone, cadence, and a content checksum.
   Credentials remain in a secret manager; only a connection reference is
   persisted.
2. **Land immutable raw data.** Copy a bounded snapshot to versioned object
   storage and register its manifest in PostgreSQL. Never train directly from
   mutable production tables.
3. **Profile and validate.** Deterministic checks quantify missingness,
   duplicates, monotonicity, cadence gaps, unit/range violations, operating
   modes, label coverage, asset-to-sensor cardinality, and temporal leakage.
4. **Map to an asset-family contract.** An LLM may propose sensor aliases,
   units, and candidate semantic roles from the schema and data dictionary.
   It cannot read unrestricted rows, create SQL, or approve its own mapping.
   A human accepts a typed `SensorMappingSpec` before any backfill.
5. **Build one versioned feature path.** A code-owned `FeatureSpec` produces
   both offline and online features. Future-mutation and offline/online parity
   tests are mandatory.
6. **Train candidates with frozen splits.** Splits, thresholds, event labels,
   and readiness bars are declared before holdout scoring. Synthetic data can
   test plumbing but cannot train or validate the flagship failure model.
7. **Evaluate and register.** Store artifacts, dependency versions, data and
   feature manifests, metrics by site/mode/month, limitations, and approval
   state. A model is deployable only after a human accepts the evaluation.
8. **Shadow, then promote.** Online inference writes predictions first.
   Alert and incident automation remain shadow-only until observed drift,
   false-alert, recall/delay, and operational cost criteria pass on the target
   environment.

Every stage has `pending → running → awaiting_approval → completed|failed`
state, idempotency keys, an audit trail, and resumable checkpoints. The
onboarding orchestrator never shares the maintenance agent's action tools.

## Model granularity

Default to **one model per asset family and operating regime**, optionally
site-conditioned, not one model per physical asset.

An asset-family model usually has enough history to learn useful normal
variation, generalizes to new members, and is maintainable. Include asset or
site identity only through controlled conditioning/calibration when tests
show it improves generalization without memorizing the holdout.

Use a per-asset model only when that asset has sufficient representative
history across seasons and operating modes, its physics differs materially
from peers, and a family model demonstrably fails. A practical hierarchy is:

`global family model → site/mode calibration → per-asset residual or threshold`

New or data-poor assets start with the approved family model and remain in
shadow while their local baseline accumulates.

## Storage architecture

Keep PostgreSQL as the transactional source for facilities, investigations,
approvals, model registry metadata, predictions, and audit records. For a
portfolio-scale deployment, use TimescaleDB-compatible hypertables or a
dedicated time-series store for high-volume telemetry while retaining stable
sensor/asset IDs in PostgreSQL. Store raw snapshots and trained artifacts in
versioned object storage, addressed by checksums; do not put large Parquet or
model binaries in relational rows.

The current repository can remain plain PostgreSQL for the portfolio demo.
Adding a second database now would increase operational surface without
improving the demonstrated contracts.

## Recommended bounded agents

* **Onboarding assistant:** proposes schema/unit mappings and explains data
  quality findings; no credentials, arbitrary SQL, training, or deployment.
* **Training orchestrator:** deterministic job state machine that executes
  approved feature/train/evaluate code and registers immutable outputs.
* **Monitoring service:** measures drift and performance by mode/site and can
  recommend retraining; it cannot promote a model.
* **Maintenance diagnostic assistant:** the existing evidence-grounded hybrid
  reasoner. It consumes only data from an approved deployed model and the
  investigation's evidence domain.

This separation makes the product flexible without pretending that one LLM
can safely understand every facility, sensor, failure definition, and cost
model on first contact.
