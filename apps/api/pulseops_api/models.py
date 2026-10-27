"""SQLAlchemy models.

Two domains share one database:

  Facility domain — buildings/floors/rooms/assets/sensors/readings, technicians,
  inspections, work orders, maintenance-manual chunks, incidents. All synthetic.

  Agent domain — agent_runs / agent_steps / agent_tool_calls / agent_actions /
  verification_results / case_memory. This is the persisted state of the
  Observe → Diagnose → Plan → Act → Verify → Learn loop and is the contract the
  frontend, eval harness, and future phases depend on. Do not rename columns
  casually; see docs/agentic-loop.md.

Conventions:
  - JSON payloads are stored as TEXT columns holding JSON (SQLite-friendly);
    (de)serialization happens in services. Column names end in `_json`.
  - agent_steps.reasoning_summary holds a concise, human-readable summary only —
    never hidden chain-of-thought.
  - agent_tool_calls.trust_label marks retrieved document content as
    `untrusted_evidence`; system-of-record lookups are `trusted_system`.
"""

from datetime import datetime

from sqlalchemy import (
    Boolean,
    Float,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
    text,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from .db import Base, TZDateTime
from .timeutil import utcnow

# ---------------------------------------------------------------------------
# Facility domain
# ---------------------------------------------------------------------------


class Building(Base):
    __tablename__ = "buildings"

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(120))
    address: Mapped[str] = mapped_column(String(200), default="")

    floors: Mapped[list["Floor"]] = relationship(back_populates="building")


class Floor(Base):
    __tablename__ = "floors"

    id: Mapped[int] = mapped_column(primary_key=True)
    building_id: Mapped[int] = mapped_column(ForeignKey("buildings.id"))
    level: Mapped[int] = mapped_column(Integer)
    name: Mapped[str] = mapped_column(String(120))

    building: Mapped[Building] = relationship(back_populates="floors")
    rooms: Mapped[list["Room"]] = relationship(back_populates="floor")


class Room(Base):
    __tablename__ = "rooms"

    id: Mapped[int] = mapped_column(primary_key=True)
    floor_id: Mapped[int] = mapped_column(ForeignKey("floors.id"))
    name: Mapped[str] = mapped_column(String(120))
    kind: Mapped[str] = mapped_column(
        String(60), default="mechanical"
    )  # mechanical|office|lab|server

    floor: Mapped[Floor] = relationship(back_populates="rooms")
    assets: Mapped[list["Asset"]] = relationship(back_populates="room")


class Asset(Base):
    __tablename__ = "assets"

    id: Mapped[int] = mapped_column(primary_key=True)
    tag: Mapped[str] = mapped_column(String(40), unique=True, index=True)  # e.g. "CH-03"
    name: Mapped[str] = mapped_column(String(120))  # e.g. "Chiller-03"
    asset_type: Mapped[str] = mapped_column(String(60), index=True)  # chiller|pump|ahu|boiler|crac
    manufacturer: Mapped[str] = mapped_column(String(120), default="")
    model: Mapped[str] = mapped_column(String(120), default="")
    install_date: Mapped[str] = mapped_column(String(20), default="")  # ISO date string
    criticality: Mapped[str] = mapped_column(
        String(20), default="medium"
    )  # low|medium|high|critical
    status: Mapped[str] = mapped_column(
        String(20), default="online"
    )  # online|degraded|offline|maintenance
    room_id: Mapped[int] = mapped_column(ForeignKey("rooms.id"))

    room: Mapped[Room] = relationship(back_populates="assets")
    sensors: Mapped[list["Sensor"]] = relationship(back_populates="asset")


class Sensor(Base):
    __tablename__ = "sensors"

    id: Mapped[int] = mapped_column(primary_key=True)
    asset_id: Mapped[int] = mapped_column(ForeignKey("assets.id"), index=True)
    kind: Mapped[str] = mapped_column(
        String(60)
    )  # vibration_rms|discharge_pressure|supply_temp|power_kw|...
    unit: Mapped[str] = mapped_column(String(20))
    baseline_value: Mapped[float] = mapped_column(Float, default=0.0)  # nominal healthy value

    asset: Mapped[Asset] = relationship(back_populates="sensors")
    readings: Mapped[list["SensorReading"]] = relationship(back_populates="sensor")


class SensorReading(Base):
    __tablename__ = "sensor_readings"
    __table_args__ = (
        Index("ix_readings_sensor_ts", "sensor_id", "ts"),
        # Idempotent ingestion (replay/streaming): a reading carrying
        # a source_id may be inserted at most once per sensor. NULL source_id
        # (seed/simulator writes) is exempt in both SQLite and PostgreSQL.
        UniqueConstraint("sensor_id", "source_id", name="uq_reading_sensor_source"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    sensor_id: Mapped[int] = mapped_column(ForeignKey("sensors.id"))
    ts: Mapped[datetime] = mapped_column(TZDateTime)
    value: Mapped[float] = mapped_column(Float)
    # Stable identifier from the ingestion source (e.g. "<replay_run>:<row>").
    source_id: Mapped[str | None] = mapped_column(String(120), nullable=True)

    sensor: Mapped[Sensor] = relationship(back_populates="readings")


class Technician(Base):
    __tablename__ = "technicians"

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(120))
    specialty: Mapped[str] = mapped_column(String(60))  # hvac|electrical|plumbing|controls
    shift: Mapped[str] = mapped_column(String(20), default="day")
    available: Mapped[bool] = mapped_column(Boolean, default=True)


class Inspection(Base):
    __tablename__ = "inspections"

    id: Mapped[int] = mapped_column(primary_key=True)
    asset_id: Mapped[int] = mapped_column(ForeignKey("assets.id"), index=True)
    technician_id: Mapped[int] = mapped_column(ForeignKey("technicians.id"))
    ts: Mapped[datetime] = mapped_column(TZDateTime)
    summary: Mapped[str] = mapped_column(String(200))
    evidence_domain: Mapped[str] = mapped_column(
        String(60), default="synthetic_facility", index=True
    )
    notes: Mapped[str] = mapped_column(
        Text, default=""
    )  # free text — UNTRUSTED evidence to the agent


class WorkOrder(Base):
    __tablename__ = "work_orders"

    id: Mapped[int] = mapped_column(primary_key=True)
    asset_id: Mapped[int] = mapped_column(ForeignKey("assets.id"), index=True)
    technician_id: Mapped[int | None] = mapped_column(ForeignKey("technicians.id"), nullable=True)
    status: Mapped[str] = mapped_column(
        String(20), default="open"
    )  # open|in_progress|completed|cancelled
    priority: Mapped[str] = mapped_column(String(20), default="medium")  # low|medium|high|urgent
    title: Mapped[str] = mapped_column(String(200))
    description: Mapped[str] = mapped_column(Text, default="")
    evidence_domain: Mapped[str] = mapped_column(
        String(60), default="synthetic_facility", index=True
    )
    notes: Mapped[str] = mapped_column(
        Text, default=""
    )  # free text — UNTRUSTED evidence to the agent
    source: Mapped[str] = mapped_column(String(20), default="human")  # human|agent
    created_at: Mapped[datetime] = mapped_column(TZDateTime, default=utcnow)
    completed_at: Mapped[datetime | None] = mapped_column(TZDateTime, nullable=True)


class ManualChunk(Base):
    __tablename__ = "manual_chunks"

    id: Mapped[int] = mapped_column(primary_key=True)
    doc_title: Mapped[str] = mapped_column(String(200))
    section: Mapped[str] = mapped_column(String(200))
    asset_type: Mapped[str] = mapped_column(String(60), index=True)
    # Retrieval boundary: synthetic demo material must not silently become
    # operational evidence for a real-dataset/customer investigation.
    evidence_domain: Mapped[str] = mapped_column(
        String(60), default="synthetic_facility", index=True
    )
    content: Mapped[str] = mapped_column(Text)  # free text — UNTRUSTED evidence to the agent


class Incident(Base):
    __tablename__ = "incidents"

    id: Mapped[int] = mapped_column(primary_key=True)
    asset_id: Mapped[int] = mapped_column(ForeignKey("assets.id"), index=True)
    severity: Mapped[str] = mapped_column(String(20), default="warning")  # info|warning|critical
    status: Mapped[str] = mapped_column(
        String(20), default="active"
    )  # active|investigating|mitigated|closed
    title: Mapped[str] = mapped_column(String(200))
    summary: Mapped[str] = mapped_column(Text, default="")
    detected_at: Mapped[datetime] = mapped_column(TZDateTime, default=utcnow)


# ---------------------------------------------------------------------------
# Agent domain
# ---------------------------------------------------------------------------

# Loop phases in canonical order. `awaiting_approval` is a *run status*, not a
# phase: the run pauses inside `act` until a human decides. Diagnose/Plan/Act/
# Verify may repeat (bounded by max_attempts) when verification fails.
AGENT_PHASES = ["observe", "diagnose", "plan", "act", "verify", "learn"]

# paused      — waiting for more observations (e.g. inconclusive verification)
# escalated   — human intervention required (abstention, adverse outcome,
#               or attempt budget exhausted); the agent will not act further.
RUN_STATUSES = [
    "running",
    "paused",
    "awaiting_evidence",
    "awaiting_approval",
    "completed",
    "failed",
    "escalated",
]
ACTION_STATUSES = ["proposed", "approved", "rejected", "executed"]

# Verification outcomes — see services/verification.py for the classifier.
VERIFICATION_OUTCOMES = ["passed", "failed", "inconclusive", "adverse"]

# Case-memory provenance/validation — see docs/agentic-loop.md §memory validity.
MEMORY_PROVENANCE = ["seeded", "agent"]
MEMORY_VALIDATION = [
    "validated_success",  # verified (or historically confirmed) successful resolution
    "validated_failure",  # verified failed/adverse intervention — negative evidence
    "provisional",  # agent inference not yet independently validated
    "rejected",  # human-rejected
    "superseded",  # replaced by a newer validated entry
]


class AgentRun(Base):
    __tablename__ = "agent_runs"
    __table_args__ = (
        # At most one ACTIVE run per incident (best-effort partial unique
        # index; AgentEngine.start_run also enforces this in application code
        # with a clearer error).
        Index(
            "uq_active_run_per_incident",
            "incident_id",
            unique=True,
            sqlite_where=text(
                "incident_id IS NOT NULL AND status IN "
                "('running','awaiting_evidence','awaiting_approval','paused')"
            ),
            postgresql_where=text(
                "incident_id IS NOT NULL AND status IN "
                "('running','awaiting_evidence','awaiting_approval','paused')"
            ),
        ),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    asset_id: Mapped[int] = mapped_column(ForeignKey("assets.id"), index=True)
    incident_id: Mapped[int | None] = mapped_column(ForeignKey("incidents.id"), nullable=True)
    status: Mapped[str] = mapped_column(String(30), default="running")
    current_phase: Mapped[str] = mapped_column(String(20), default="observe")
    iterations_used: Mapped[int] = mapped_column(Integer, default=0)
    max_iterations: Mapped[int] = mapped_column(Integer, default=12)
    # Executed-action attempts (verification-failed replans consume the budget).
    attempts_used: Mapped[int] = mapped_column(Integer, default=0)
    max_attempts: Mapped[int] = mapped_column(Integer, default=2)
    trigger: Mapped[str] = mapped_column(String(200), default="")  # what started this run
    evidence_domain: Mapped[str] = mapped_column(
        String(60), default="synthetic_facility", index=True
    )
    summary: Mapped[str] = mapped_column(Text, default="")  # filled at Learn
    created_at: Mapped[datetime] = mapped_column(TZDateTime, default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(TZDateTime, default=utcnow, onupdate=utcnow)

    steps: Mapped[list["AgentStep"]] = relationship(back_populates="run", order_by="AgentStep.seq")
    tool_calls: Mapped[list["AgentToolCall"]] = relationship(
        back_populates="run", order_by="AgentToolCall.seq"
    )
    actions: Mapped[list["AgentAction"]] = relationship(back_populates="run")
    evidence_requests: Mapped[list["DiagnosticEvidenceRequest"]] = relationship(
        back_populates="run", order_by="DiagnosticEvidenceRequest.id"
    )


class AgentStep(Base):
    __tablename__ = "agent_steps"

    id: Mapped[int] = mapped_column(primary_key=True)
    run_id: Mapped[int] = mapped_column(ForeignKey("agent_runs.id"), index=True)
    seq: Mapped[int] = mapped_column(Integer)
    phase: Mapped[str] = mapped_column(String(20))
    status: Mapped[str] = mapped_column(String(20), default="completed")  # completed|blocked|failed
    reasoning_summary: Mapped[str] = mapped_column(
        Text, default=""
    )  # concise summary — NEVER raw CoT
    output_json: Mapped[str] = mapped_column(Text, default="{}")  # structured phase output
    started_at: Mapped[datetime] = mapped_column(TZDateTime, default=utcnow)
    completed_at: Mapped[datetime | None] = mapped_column(TZDateTime, nullable=True)

    run: Mapped[AgentRun] = relationship(back_populates="steps")


class AgentToolCall(Base):
    __tablename__ = "agent_tool_calls"

    id: Mapped[int] = mapped_column(primary_key=True)
    run_id: Mapped[int] = mapped_column(ForeignKey("agent_runs.id"), index=True)
    step_id: Mapped[int] = mapped_column(ForeignKey("agent_steps.id"))
    seq: Mapped[int] = mapped_column(Integer)
    tool_name: Mapped[str] = mapped_column(String(60))
    arguments_json: Mapped[str] = mapped_column(Text, default="{}")
    result_summary: Mapped[str] = mapped_column(Text, default="")
    result_json: Mapped[str] = mapped_column(Text, default="{}")
    # trusted_system: structured system-of-record data (sensor stats, asset profile).
    # untrusted_evidence: free-text document content (manuals, notes, postmortems).
    # structured_observation: typed operator/lab/sensor result; audited, not system-derived.
    trust_label: Mapped[str] = mapped_column(String(30), default="trusted_system")
    injection_flag: Mapped[bool] = mapped_column(
        Boolean, default=False
    )  # sanitizer detected instruction-like text
    created_at: Mapped[datetime] = mapped_column(TZDateTime, default=utcnow)

    run: Mapped[AgentRun] = relationship(back_populates="tool_calls")


class AgentAction(Base):
    __tablename__ = "agent_actions"

    id: Mapped[int] = mapped_column(primary_key=True)
    run_id: Mapped[int] = mapped_column(ForeignKey("agent_runs.id"), index=True)
    action_type: Mapped[str] = mapped_column(String(60), default="create_work_order")
    status: Mapped[str] = mapped_column(String(20), default="proposed")
    payload_json: Mapped[str] = mapped_column(Text, default="{}")  # proposed work order content
    work_order_id: Mapped[int | None] = mapped_column(ForeignKey("work_orders.id"), nullable=True)
    proposed_at: Mapped[datetime] = mapped_column(TZDateTime, default=utcnow)
    decided_at: Mapped[datetime | None] = mapped_column(TZDateTime, nullable=True)
    decided_by: Mapped[str] = mapped_column(String(120), default="")  # human approver identity
    executed_at: Mapped[datetime | None] = mapped_column(TZDateTime, nullable=True)

    run: Mapped[AgentRun] = relationship(back_populates="actions")


class DiagnosticEvidenceRequest(Base):
    """Persisted request for genuinely independent diagnostic evidence.

    A request never fabricates a measurement and never changes facility state.
    It pauses Diagnose until a human, lab, sensor, or import supplies a typed
    observation. A test type may be requested only once per run so the round
    budget remains durable across restarts and retries.
    """

    __tablename__ = "diagnostic_evidence_requests"
    __table_args__ = (
        UniqueConstraint("run_id", "test_id", name="uq_evidence_request_run_test"),
        Index("ix_evidence_requests_run_status", "run_id", "status"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    run_id: Mapped[int] = mapped_column(ForeignKey("agent_runs.id"), index=True)
    asset_id: Mapped[int] = mapped_column(ForeignKey("assets.id"), index=True)
    test_id: Mapped[str] = mapped_column(String(80))
    status: Mapped[str] = mapped_column(String(20), default="pending")
    requested_by: Mapped[str] = mapped_column(String(80), default="deterministic_policy")
    rationale: Mapped[str] = mapped_column(Text, default="")
    hypotheses_json: Mapped[str] = mapped_column(Text, default="[]")
    result_schema_json: Mapped[str] = mapped_column(Text, default="{}")
    request_round: Mapped[int] = mapped_column(Integer, default=1)
    created_at: Mapped[datetime] = mapped_column(TZDateTime, default=utcnow)
    resolved_at: Mapped[datetime | None] = mapped_column(TZDateTime, nullable=True)

    run: Mapped[AgentRun] = relationship(back_populates="evidence_requests")
    observation: Mapped["DiagnosticEvidenceObservation"] = relationship(
        back_populates="request", uselist=False
    )


class DiagnosticEvidenceObservation(Base):
    """Schema-validated observation submitted in response to a request.

    Structured results are evidence, not instructions. Their signal mapping is
    deterministic and stored separately so free text cannot alter weights.
    """

    __tablename__ = "diagnostic_evidence_observations"
    __table_args__ = (UniqueConstraint("request_id", name="uq_evidence_observation_request"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    request_id: Mapped[int] = mapped_column(
        ForeignKey("diagnostic_evidence_requests.id"), index=True
    )
    submitted_by: Mapped[str] = mapped_column(String(120))
    source: Mapped[str] = mapped_column(String(30))
    observed_at: Mapped[datetime] = mapped_column(TZDateTime)
    result_json: Mapped[str] = mapped_column(Text, default="{}")
    signal_json: Mapped[str] = mapped_column(Text, default="{}")
    created_at: Mapped[datetime] = mapped_column(TZDateTime, default=utcnow)

    request: Mapped[DiagnosticEvidenceRequest] = relationship(back_populates="observation")


class VerificationResult(Base):
    __tablename__ = "verification_results"

    id: Mapped[int] = mapped_column(primary_key=True)
    run_id: Mapped[int] = mapped_column(ForeignKey("agent_runs.id"), index=True)
    action_id: Mapped[int] = mapped_column(ForeignKey("agent_actions.id"))
    risk_before: Mapped[float] = mapped_column(Float)
    risk_after: Mapped[float] = mapped_column(Float)
    risk_delta: Mapped[float] = mapped_column(Float)
    # outcome ∈ VERIFICATION_OUTCOMES; `passed` kept as a compatibility alias
    # (passed == (outcome == "passed")).
    outcome: Mapped[str] = mapped_column(String(20), default="inconclusive")
    outcome_reason: Mapped[str] = mapped_column(String(300), default="")
    passed: Mapped[bool] = mapped_column(Boolean, default=False)
    details_json: Mapped[str] = mapped_column(Text, default="{}")  # per-sensor before/after
    verified_at: Mapped[datetime] = mapped_column(TZDateTime, default=utcnow)


class CaseMemory(Base):
    __tablename__ = "case_memory"

    id: Mapped[int] = mapped_column(primary_key=True)
    asset_id: Mapped[int | None] = mapped_column(ForeignKey("assets.id"), nullable=True)
    asset_type: Mapped[str] = mapped_column(String(60), index=True)
    evidence_domain: Mapped[str] = mapped_column(
        String(60), default="synthetic_facility", index=True
    )
    title: Mapped[str] = mapped_column(String(200))
    symptoms_json: Mapped[str] = mapped_column(Text, default="[]")  # list of symptom keywords
    root_cause: Mapped[str] = mapped_column(String(200))
    resolution: Mapped[str] = mapped_column(Text, default="")
    outcome: Mapped[str] = mapped_column(String(200), default="")
    risk_reduction: Mapped[float] = mapped_column(Float, default=0.0)
    narrative: Mapped[str] = mapped_column(
        Text, default=""
    )  # free text — UNTRUSTED evidence to the agent
    source_run_id: Mapped[int | None] = mapped_column(ForeignKey("agent_runs.id"), nullable=True)
    # Provenance + validation (MEMORY_PROVENANCE / MEMORY_VALIDATION).
    # Only validated_success entries may positively boost a diagnosis; the
    # agent never validates its own memories — validation comes from a
    # VerificationResult (validated_by="verification:<id>") or a human.
    provenance: Mapped[str] = mapped_column(String(20), default="agent")
    validation_status: Mapped[str] = mapped_column(String(30), default="provisional")
    validated_by: Mapped[str] = mapped_column(String(120), default="")
    created_at: Mapped[datetime] = mapped_column(TZDateTime, default=utcnow)


class ReplayRun(Base):
    """Persisted state of an accelerated historical-telemetry replay.

    Three clocks are kept distinct:
      * source/event time  — timestamps inside the historical dataset
        (source_start/source_end/cursor/sim_time are all in source time);
      * simulation time    — where the replay clock currently stands
        (sim_time == cursor after a chunk commits);
      * ingestion time     — when rows were actually written
        (sensor_readings rows carry no extra column; the outbox event and
        updated_at record wall-clock ingestion).

    The DB row — not process memory — is the source of truth: a fresh service
    instance constructed from this id resumes exactly at `cursor`.
    """

    __tablename__ = "replay_runs"

    id: Mapped[int] = mapped_column(primary_key=True)
    dataset_name: Mapped[str] = mapped_column(String(60))  # e.g. "metropt3"
    dataset_checksum: Mapped[str] = mapped_column(String(80), default="")
    asset_id: Mapped[int] = mapped_column(ForeignKey("assets.id"), index=True)
    source_start: Mapped[datetime] = mapped_column(TZDateTime)
    source_end: Mapped[datetime] = mapped_column(TZDateTime)
    # Last source timestamp already ingested (exclusive resume point); NULL = not started.
    cursor: Mapped[datetime | None] = mapped_column(TZDateTime, nullable=True)
    sim_time: Mapped[datetime | None] = mapped_column(TZDateTime, nullable=True)
    speed: Mapped[float] = mapped_column(Float, default=60.0)  # source seconds per wall second
    status: Mapped[str] = mapped_column(
        String(20), default="ready"
    )  # ready|running|paused|completed|failed
    last_error: Mapped[str] = mapped_column(Text, default="")
    # Provenance for predictions generated from this replay (when inference is wired).
    model_source: Mapped[str] = mapped_column(String(60), default="")
    feature_version: Mapped[str] = mapped_column(String(60), default="")
    created_at: Mapped[datetime] = mapped_column(TZDateTime, default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(TZDateTime, default=utcnow, onupdate=utcnow)


class ModelPrediction(Base):
    """Versioned online inference output. One row per
    (asset, model, feature-window end) — the unique constraint makes replayed
    windows idempotent. `shadow=True` means the prediction is stored for
    observation only and MUST NOT trigger incidents
    (ml/model_artifacts/trigger_policy.json governs this)."""

    __tablename__ = "model_predictions"
    __table_args__ = (
        UniqueConstraint(
            "replay_run_id",
            "asset_id",
            "model_source",
            "window_end",
            name="uq_prediction_replay_asset_model_window",
        ),
        Index("ix_predictions_asset_window", "asset_id", "window_end"),
        Index("ix_predictions_replay_window", "replay_run_id", "window_end"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    asset_id: Mapped[int] = mapped_column(ForeignKey("assets.id"))
    window_start: Mapped[datetime | None] = mapped_column(TZDateTime, nullable=True)
    window_end: Mapped[datetime] = mapped_column(TZDateTime)  # event time of the feature window
    predicted_at: Mapped[datetime] = mapped_column(
        TZDateTime, default=utcnow
    )  # ingestion/creation time
    risk: Mapped[float] = mapped_column(Float)
    threshold: Mapped[float] = mapped_column(Float)
    alert: Mapped[bool] = mapped_column(Boolean, default=False)
    shadow: Mapped[bool] = mapped_column(Boolean, default=True)
    model_source: Mapped[str] = mapped_column(String(60))  # e.g. "metropt3_iforest_v1"
    feature_version: Mapped[str] = mapped_column(String(60), default="")
    policy_mode: Mapped[str] = mapped_column(String(20), default="shadow")  # shadow|auto_trigger
    policy_version: Mapped[str] = mapped_column(
        String(60), default=""
    )  # trigger_policy generated_at
    operating_mode: Mapped[str] = mapped_column(String(20), default="")  # loaded|idle
    # DESCRIPTIVE standardized feature deviations (vs training scaler) — NOT
    # causal importances; IsolationForest provides none.
    explanation_json: Mapped[str] = mapped_column(Text, default="{}")
    replay_run_id: Mapped[int | None] = mapped_column(ForeignKey("replay_runs.id"), nullable=True)


class AssetOperatingState(Base):
    """Replayed digital operating-state inputs. The eight MetroPT
    digital channels are NOT sensors — they are operating-state inputs the
    feature pipeline consumes. Persisting them here means online scoring
    reconstructs its full input frame from replayed DATABASE state and never
    peeks at the source Parquet (no future leakage)."""

    __tablename__ = "asset_operating_states"
    __table_args__ = (
        UniqueConstraint("asset_id", "source_id", name="uq_opstate_asset_source"),
        Index("ix_opstate_asset_ts", "asset_id", "ts"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    replay_run_id: Mapped[int | None] = mapped_column(ForeignKey("replay_runs.id"), nullable=True)
    asset_id: Mapped[int] = mapped_column(ForeignKey("assets.id"))
    ts: Mapped[datetime] = mapped_column(TZDateTime)  # source/event time
    sim_time: Mapped[datetime | None] = mapped_column(TZDateTime, nullable=True)
    ingested_at: Mapped[datetime] = mapped_column(TZDateTime, default=utcnow)
    source_id: Mapped[str] = mapped_column(String(120))
    COMP: Mapped[float] = mapped_column(Float)
    DV_eletric: Mapped[float] = mapped_column(Float)  # dataset's own spelling
    Towers: Mapped[float] = mapped_column(Float)
    MPG: Mapped[float] = mapped_column(Float)
    LPS: Mapped[float] = mapped_column(Float)
    Pressure_switch: Mapped[float] = mapped_column(Float)
    Oil_level: Mapped[float] = mapped_column(Float)
    Caudal_impulses: Mapped[float] = mapped_column(Float)


class ShadowAlert(Base):
    """Durable would-trigger record. In shadow mode the trigger
    rules run and their decisions are persisted, but NO Incident and NO
    AgentRun is ever created automatically — only an operator promotion
    (services/shadow_trigger.promote_shadow_alert) creates a clearly-labeled
    synthetic incident."""

    __tablename__ = "shadow_alerts"
    __table_args__ = (
        UniqueConstraint(
            "replay_run_id",
            "asset_id",
            "policy_version",
            "episode_key",
            name="uq_shadow_replay_episode",
        ),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    replay_run_id: Mapped[int | None] = mapped_column(
        ForeignKey("replay_runs.id"), nullable=True, index=True
    )
    asset_id: Mapped[int] = mapped_column(ForeignKey("assets.id"), index=True)
    model_source: Mapped[str] = mapped_column(String(60))
    policy_mode: Mapped[str] = mapped_column(String(20), default="shadow")
    policy_version: Mapped[str] = mapped_column(String(60), default="")
    episode_key: Mapped[str] = mapped_column(
        String(120)
    )  # idempotency: first window of the episode
    first_prediction_id: Mapped[int] = mapped_column(ForeignKey("model_predictions.id"))
    latest_prediction_id: Mapped[int] = mapped_column(ForeignKey("model_predictions.id"))
    high_threshold: Mapped[float] = mapped_column(Float)
    low_threshold: Mapped[float] = mapped_column(Float)
    consecutive_windows: Mapped[int] = mapped_column(Integer, default=0)
    hysteresis_state: Mapped[str] = mapped_column(String(20), default="active")  # active|cleared
    cooldown_until: Mapped[datetime | None] = mapped_column(TZDateTime, nullable=True)
    trigger_reason: Mapped[str] = mapped_column(Text, default="")
    status: Mapped[str] = mapped_column(
        String(20), default="observed"
    )  # observed|promoted|dismissed
    decided_by: Mapped[str] = mapped_column(String(120), default="")
    decided_at: Mapped[datetime | None] = mapped_column(TZDateTime, nullable=True)
    incident_id: Mapped[int | None] = mapped_column(ForeignKey("incidents.id"), nullable=True)
    created_at: Mapped[datetime] = mapped_column(TZDateTime, default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(TZDateTime, default=utcnow, onupdate=utcnow)


class OutboxEvent(Base):
    """Transactional outbox: durable domain events written in the same
    transaction as the state change they describe. Nothing consumes them
    yet; they serve as a durable audit stream, and a future worker can
    claim rows by setting processed_at."""

    __tablename__ = "outbox_events"
    __table_args__ = (Index("ix_outbox_unprocessed", "processed_at", "id"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    topic: Mapped[str] = mapped_column(String(80), index=True)  # e.g. "agent.action.executed"
    payload_json: Mapped[str] = mapped_column(Text, default="{}")
    created_at: Mapped[datetime] = mapped_column(TZDateTime, default=utcnow)
    processed_at: Mapped[datetime | None] = mapped_column(TZDateTime, nullable=True)
    attempts: Mapped[int] = mapped_column(Integer, default=0)
