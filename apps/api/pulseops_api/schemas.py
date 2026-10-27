"""Pydantic response/request schemas — the API contract consumed by apps/web.

Keep field names stable: the frontend (apps/web/lib/api.ts) and the eval
harness both rely on these shapes.
"""

from datetime import datetime
from typing import Any

from pydantic import BaseModel, ConfigDict, Field


class ORMModel(BaseModel):
    model_config = ConfigDict(from_attributes=True)


# --- Facility -----------------------------------------------------------


class AssetSummary(ORMModel):
    id: int
    tag: str
    name: str
    asset_type: str
    criticality: str
    status: str
    location: str = ""
    risk: float = 0.0
    risk_source: str = ""  # "mock_demo_v0" until the real model is integrated


class SensorOut(ORMModel):
    id: int
    kind: str
    unit: str
    baseline_value: float


class ReadingPoint(BaseModel):
    ts: datetime
    value: float


class SensorSeries(BaseModel):
    sensor: SensorOut
    points: list[ReadingPoint]


class AssetDetail(AssetSummary):
    manufacturer: str = ""
    model: str = ""
    install_date: str = ""
    sensors: list[SensorOut] = []


class WorkOrderOut(ORMModel):
    id: int
    asset_id: int
    status: str
    priority: str
    title: str
    description: str
    evidence_domain: str
    notes: str
    source: str
    technician_id: int | None = None
    created_at: datetime
    completed_at: datetime | None = None


class InspectionOut(ORMModel):
    id: int
    asset_id: int
    technician_id: int
    ts: datetime
    summary: str
    evidence_domain: str
    notes: str


class IncidentOut(ORMModel):
    id: int
    asset_id: int
    severity: str
    status: str
    title: str
    summary: str
    detected_at: datetime


class TechnicianOut(ORMModel):
    id: int
    name: str
    specialty: str
    shift: str
    available: bool


# --- Agent --------------------------------------------------------------


class AgentStepOut(ORMModel):
    id: int
    seq: int
    phase: str
    status: str
    reasoning_summary: str
    output: dict[str, Any] = {}
    started_at: datetime
    completed_at: datetime | None = None


class AgentToolCallOut(ORMModel):
    id: int
    step_id: int
    seq: int
    tool_name: str
    arguments: dict[str, Any] = {}
    result_summary: str
    trust_label: str
    injection_flag: bool
    created_at: datetime


class AgentActionOut(ORMModel):
    id: int
    run_id: int
    action_type: str
    status: str
    payload: dict[str, Any] = {}
    work_order_id: int | None = None
    proposed_at: datetime
    decided_at: datetime | None = None
    decided_by: str = ""
    executed_at: datetime | None = None


class VerificationOut(ORMModel):
    id: int
    run_id: int
    action_id: int
    risk_before: float
    risk_after: float
    risk_delta: float
    outcome: str  # passed | failed | inconclusive | adverse
    outcome_reason: str
    passed: bool  # compatibility alias: outcome == "passed"
    details: dict[str, Any] = {}
    verified_at: datetime


class CaseMemoryOut(ORMModel):
    id: int
    asset_id: int | None
    asset_type: str
    evidence_domain: str
    title: str
    symptoms: list[str] = []
    root_cause: str
    resolution: str
    outcome: str
    risk_reduction: float
    narrative: str
    source_run_id: int | None
    provenance: str  # seeded | agent
    validation_status: (
        str  # validated_success | validated_failure | provisional | rejected | superseded
    )
    validated_by: str
    created_at: datetime


class AgentRunOut(ORMModel):
    id: int
    asset_id: int
    incident_id: int | None
    status: str
    current_phase: str
    iterations_used: int
    max_iterations: int
    attempts_used: int
    max_attempts: int
    trigger: str
    evidence_domain: str
    summary: str
    created_at: datetime
    updated_at: datetime


class DiagnosticEvidenceObservationOut(ORMModel):
    id: int
    submitted_by: str
    source: str
    observed_at: datetime
    result: dict[str, Any] = {}
    signal: dict[str, float] = {}
    created_at: datetime


class DiagnosticEvidenceRequestOut(ORMModel):
    id: int
    run_id: int
    asset_id: int
    test_id: str
    status: str
    requested_by: str
    rationale: str
    hypotheses: list[str] = []
    result_schema: dict[str, Any] = {}
    request_round: int
    created_at: datetime
    resolved_at: datetime | None = None
    observation: DiagnosticEvidenceObservationOut | None = None


class AgentRunDetail(AgentRunOut):
    steps: list[AgentStepOut] = []
    tool_calls: list[AgentToolCallOut] = []
    actions: list[AgentActionOut] = []
    evidence_requests: list[DiagnosticEvidenceRequestOut] = []
    verification: VerificationOut | None = None


class StartRunRequest(BaseModel):
    asset_id: int
    trigger: str = "manual"


class AdvanceRunRequest(BaseModel):
    # Advance one phase (for stepping in the UI) or run until blocked/done.
    mode: str = "until_blocked"  # "one_phase" | "until_blocked"


class ActionDecisionRequest(BaseModel):
    decided_by: str = "operator"


class EvidenceSubmissionRequest(BaseModel):
    submitted_by: str = Field(default="operator", min_length=1, max_length=120)
    source: str = Field(default="operator", pattern="^(operator|laboratory|sensor|import)$")
    observed_at: datetime | None = None
    result: dict[str, Any]


class ReplayRunOut(ORMModel):
    id: int
    dataset_name: str
    dataset_checksum: str
    asset_id: int
    source_start: datetime
    source_end: datetime
    cursor: datetime | None
    sim_time: datetime | None
    speed: float
    status: str
    last_error: str
    model_source: str
    feature_version: str
    created_at: datetime
    updated_at: datetime  # ingestion-time clock


class CreateReplayRequest(BaseModel):
    source_start: datetime | None = None  # defaults to the dataset start (2020-02-01)
    duration_hours: float = Field(default=4.0, gt=0, le=24 * 14)  # bounded: max two source weeks
    speed: float = Field(default=600.0, gt=0, le=86400)  # source seconds per wall second


class AdvanceReplayRequest(BaseModel):
    wall_seconds: float = 1.0  # source progress = wall_seconds * speed


class ModelPredictionOut(ORMModel):
    id: int
    asset_id: int
    window_start: datetime | None
    window_end: datetime
    predicted_at: datetime
    risk: float
    threshold: float
    alert: bool
    shadow: bool
    model_source: str
    feature_version: str
    policy_mode: str
    policy_version: str
    operating_mode: str
    explanation: dict[str, Any] = {}
    replay_run_id: int | None


class ShadowAlertOut(ORMModel):
    id: int
    replay_run_id: int | None
    asset_id: int
    model_source: str
    policy_mode: str
    policy_version: str
    episode_key: str
    first_prediction_id: int
    latest_prediction_id: int
    high_threshold: float
    low_threshold: float
    consecutive_windows: int
    hysteresis_state: str
    cooldown_until: datetime | None
    trigger_reason: str
    status: str
    decided_by: str
    decided_at: datetime | None
    incident_id: int | None
    created_at: datetime


class FlagshipModelStatus(BaseModel):
    """Command-center projection of the real MetroPT shadow monitor."""

    asset_id: int
    asset_tag: str
    asset_name: str
    status: str  # awaiting_replay | monitoring | building_persistence | review_required
    model_source: str
    feature_version: str
    policy_mode: str
    replay_run_id: int | None = None
    prediction_id: int | None = None
    risk: float | None = None
    threshold: float | None = None
    threshold_crossed: bool = False
    persistence_windows: int = 0
    persistence_required: int = 3
    operating_mode: str = ""
    window_end: datetime | None = None
    pending_shadow_alerts: int = 0
    latest_shadow_alert_id: int | None = None
    disclaimer: str = "Anomaly score, not a calibrated failure probability."


class ModelEventEvaluation(BaseModel):
    start: str
    end: str
    kind: str
    detected: bool
    first_alert: str | None = None
    lead_time_hours: float | None = None


class ModelPolicyEvaluation(BaseModel):
    policy: str
    threshold: str
    threshold_selection: str
    event_recall: float
    events_detected: int
    events_missed: int
    per_event: list[ModelEventEvaluation]
    false_alarm_episodes: int
    false_alarms_per_operating_day: float
    false_alarms_per_observed_day: float | None = None
    alert_window_rate: float
    outside_event_loaded_alert_rate: float
    outside_event_idle_alert_rate: float


class FalseAlarmEpisode(BaseModel):
    start: str
    end: str
    n_alert_windows: int
    loaded_fraction: float | None = None


class ModeAlertRate(BaseModel):
    windows: int
    alert_rate: float | None = None


class FlagshipEvaluationOut(BaseModel):
    generated_at: str
    task: str
    model_source: str
    feature_version: str
    evaluated_start_exclusive: str | None
    evaluated_end: str | None
    holdout_windows: int
    eligible_days: float
    events_total: int
    observed_days: float | None = None
    primary_policy: ModelPolicyEvaluation
    # The same model and threshold under the app's persistence/hysteresis/
    # cooldown rules: the policy that actually raises shadow alerts.
    persistent_policy: ModelPolicyEvaluation | None = None
    training_mode_windows: dict[str, int | None] = {}
    monthly_alert_rate: dict[str, float]
    false_alarm_episode_intervals: list[FalseAlarmEpisode] = []
    monthly_mode_alert_rate: dict[str, dict[str, ModeAlertRate]] = {}
    trigger_mode: str
    readiness_bar: dict[str, float]
    readiness_blockers: list[str]
    decision_rationale: str
    limitations: list[str]


class TriggerPolicyOut(BaseModel):
    """Runtime shadow-trigger constants — the UI must not hardcode these."""

    persistence_windows: int
    hysteresis_low_factor: float
    cooldown_minutes: int
    mode: str
    explanation: str


class DemoResetRequest(BaseModel):
    """Explicit confirmation guard for the destructive demo reset."""

    confirm: str = ""
    operator: str = "operator"


class LlmConnectivityTestOut(BaseModel):
    """Result of the manual 'Test configured LLM' check (no side effects)."""

    status: str  # exactly STATUS_ACCEPTED or STATUS_FALLBACK
    accepted: bool
    attempts: int
    configured_mode: str
    configured_provider: str | None
    configured_model: str | None
    attempted_provider: str | None
    attempted_model: str | None
    actual_provider: str | None
    actual_model: str | None
    request_id: str | None
    latency_ms: int | None
    input_tokens: int | None
    output_tokens: int | None
    finish_reason: str | None
    prompt_version: str
    schema_version: str
    fallback_reason: str | None
    snapshot_label: str
    evidence_manifest: dict
    result: dict
    note: str


class DashboardOut(BaseModel):
    asset_count: int
    online_count: int
    degraded_count: int
    active_incidents: list[IncidentOut]
    top_risk_assets: list[AssetSummary]
    recent_runs: list[AgentRunOut]
    open_work_orders: int
    risk_source: str
    flagship_model: FlagshipModelStatus | None = None
