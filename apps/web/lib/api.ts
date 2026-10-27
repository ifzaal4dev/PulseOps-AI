// Typed client for the PulseOps API. Shapes mirror apps/api/pulseops_api/schemas.py.

export const API_BASE = process.env.NEXT_PUBLIC_API_BASE_URL ?? "http://127.0.0.1:8000";

export interface AssetSummary {
  id: number;
  tag: string;
  name: string;
  asset_type: string;
  criticality: string;
  status: string;
  location: string;
  risk: number;
  risk_source: string;
}

export interface SensorOut {
  id: number;
  kind: string;
  unit: string;
  baseline_value: number;
}

export interface AssetDetail extends AssetSummary {
  manufacturer: string;
  model: string;
  install_date: string;
  sensors: SensorOut[];
}

export interface ReadingPoint {
  ts: string;
  value: number;
}

export interface SensorSeries {
  sensor: SensorOut;
  points: ReadingPoint[];
}

export interface IncidentOut {
  id: number;
  asset_id: number;
  severity: string;
  status: string;
  title: string;
  summary: string;
  detected_at: string;
}

export interface WorkOrderOut {
  id: number;
  asset_id: number;
  status: string;
  priority: string;
  title: string;
  description: string;
  evidence_domain: string;
  notes: string;
  source: string;
  technician_id: number | null;
  created_at: string;
  completed_at: string | null;
}

export interface InspectionOut {
  id: number;
  asset_id: number;
  technician_id: number;
  ts: string;
  summary: string;
  evidence_domain: string;
  notes: string;
}

export interface AgentRunOut {
  id: number;
  asset_id: number;
  incident_id: number | null;
  status: string; // running | paused | awaiting_evidence | awaiting_approval | completed | failed | escalated
  current_phase: string;
  iterations_used: number;
  max_iterations: number;
  attempts_used: number;
  max_attempts: number;
  trigger: string;
  evidence_domain: string;
  summary: string;
  created_at: string;
  updated_at: string;
}

export interface AgentStepOut {
  id: number;
  seq: number;
  phase: string;
  status: string;
  reasoning_summary: string;
  output: Record<string, unknown>;
  started_at: string;
  completed_at: string | null;
}

export interface AgentToolCallOut {
  id: number;
  step_id: number;
  seq: number;
  tool_name: string;
  arguments: Record<string, unknown>;
  result_summary: string;
  trust_label: string;
  injection_flag: boolean;
  created_at: string;
}

export interface AgentActionOut {
  id: number;
  run_id: number;
  action_type: string;
  status: string;
  payload: {
    asset_tag?: string;
    root_cause?: string;
    confidence?: number;
    title?: string;
    description?: string;
    priority?: string;
    risk_before?: number;
    risk_source?: string;
    risk_semantics?: string;
    trigger_score?: number | null;
    trigger_source?: string;
    trigger_score_semantics?: string;
    trigger_threshold?: number | null;
    trigger_prediction_id?: number | null;
    replay_run_id?: number | null;
    evidence_refs?: string[];
  };
  work_order_id: number | null;
  proposed_at: string;
  decided_at: string | null;
  decided_by: string;
  executed_at: string | null;
}

export interface VerificationOut {
  id: number;
  run_id: number;
  action_id: number;
  risk_before: number;
  risk_after: number;
  risk_delta: number;
  outcome: "passed" | "failed" | "inconclusive" | "adverse";
  outcome_reason: string;
  passed: boolean; // compatibility alias: outcome === "passed"
  details: Record<string, unknown>;
  verified_at: string;
}

export interface AgentRunDetail extends AgentRunOut {
  steps: AgentStepOut[];
  tool_calls: AgentToolCallOut[];
  actions: AgentActionOut[];
  evidence_requests: DiagnosticEvidenceRequestOut[];
  verification: VerificationOut | null;
}

export interface DiagnosticEvidenceObservationOut {
  id: number;
  submitted_by: string;
  source: "operator" | "laboratory" | "sensor" | "import";
  observed_at: string;
  result: Record<string, string | number>;
  signal: Record<string, number>;
  created_at: string;
}

export interface DiagnosticEvidenceRequestOut {
  id: number;
  run_id: number;
  asset_id: number;
  test_id: string;
  status: "pending" | "resolved" | "expired";
  requested_by: string;
  rationale: string;
  hypotheses: string[];
  result_schema: {
    label?: string;
    instructions?: string;
    field?: string;
    enum?: string[];
    optional_numeric?: Record<string, string>;
    note?: string;
  };
  request_round: number;
  created_at: string;
  resolved_at: string | null;
  observation: DiagnosticEvidenceObservationOut | null;
}

export interface CaseMemoryOut {
  id: number;
  asset_id: number | null;
  asset_type: string;
  evidence_domain: string;
  title: string;
  symptoms: string[];
  root_cause: string;
  resolution: string;
  outcome: string;
  risk_reduction: number;
  narrative: string;
  source_run_id: number | null;
  provenance: "seeded" | "agent";
  validation_status:
    "validated_success" | "validated_failure" | "provisional" | "rejected" | "superseded";
  validated_by: string;
  created_at: string;
}

export interface DashboardOut {
  asset_count: number;
  online_count: number;
  degraded_count: number;
  active_incidents: IncidentOut[];
  top_risk_assets: AssetSummary[];
  recent_runs: AgentRunOut[];
  open_work_orders: number;
  risk_source: string;
  flagship_model: FlagshipModelStatus | null;
}

export interface FlagshipModelStatus {
  asset_id: number;
  asset_tag: string;
  asset_name: string;
  status: "awaiting_replay" | "monitoring" | "building_persistence" | "review_required";
  model_source: string;
  feature_version: string;
  policy_mode: string;
  replay_run_id: number | null;
  prediction_id: number | null;
  risk: number | null;
  threshold: number | null;
  threshold_crossed: boolean;
  persistence_windows: number;
  persistence_required: number;
  operating_mode: string;
  window_end: string | null;
  pending_shadow_alerts: number;
  latest_shadow_alert_id: number | null;
  disclaimer: string;
}

export interface ReplayRunOut {
  id: number;
  dataset_name: string;
  dataset_checksum: string;
  asset_id: number;
  source_start: string; // source/event time bounds
  source_end: string;
  cursor: string | null; // last ingested source time
  sim_time: string | null; // simulation clock
  speed: number; // source seconds per wall second
  status: "ready" | "running" | "paused" | "completed" | "failed";
  last_error: string;
  model_source: string;
  feature_version: string;
  created_at: string;
  updated_at: string; // ingestion-time clock
}

export interface ModelPredictionOut {
  id: number;
  asset_id: number;
  window_start: string | null;
  window_end: string;
  predicted_at: string;
  risk: number;
  threshold: number;
  alert: boolean;
  shadow: boolean;
  model_source: string;
  feature_version: string;
  policy_mode: string;
  policy_version: string;
  operating_mode: string;
  explanation: {
    kind?: string;
    note?: string;
    top?: {
      feature: string;
      abs_z: number;
      observed?: number;
      healthy_mean?: number;
      healthy_std?: number;
    }[];
  };
  replay_run_id: number | null;
}

export interface ShadowAlertOut {
  id: number;
  replay_run_id: number | null;
  asset_id: number;
  model_source: string;
  policy_mode: string;
  policy_version: string;
  episode_key: string;
  first_prediction_id: number;
  latest_prediction_id: number;
  high_threshold: number;
  low_threshold: number;
  consecutive_windows: number;
  hysteresis_state: string;
  cooldown_until: string | null;
  trigger_reason: string;
  status: "observed" | "promoted" | "dismissed";
  decided_by: string;
  decided_at: string | null;
  incident_id: number | null;
  created_at: string;
}

export interface ModelPolicyEvaluation {
  policy: string;
  threshold: string;
  threshold_selection: string;
  event_recall: number;
  events_detected: number;
  events_missed: number;
  per_event: {
    start: string;
    end: string;
    kind: string;
    detected: boolean;
    first_alert: string | null;
    lead_time_hours: number | null;
  }[];
  false_alarm_episodes: number;
  false_alarms_per_operating_day: number;
  false_alarms_per_observed_day: number | null;
  alert_window_rate: number;
  outside_event_loaded_alert_rate: number;
  outside_event_idle_alert_rate: number;
}

export interface FlagshipEvaluationOut {
  generated_at: string;
  task: string;
  model_source: string;
  feature_version: string;
  evaluated_start_exclusive: string | null;
  evaluated_end: string | null;
  holdout_windows: number;
  eligible_days: number;
  events_total: number;
  observed_days: number | null;
  primary_policy: ModelPolicyEvaluation;
  // Same model and threshold under the app's persistence/hysteresis/cooldown.
  persistent_policy: ModelPolicyEvaluation | null;
  training_mode_windows: Record<string, number | null>;
  monthly_alert_rate: Record<string, number>;
  false_alarm_episode_intervals: {
    start: string;
    end: string;
    n_alert_windows: number;
    loaded_fraction: number | null;
  }[];
  monthly_mode_alert_rate: Record<
    string,
    Record<string, { windows: number; alert_rate: number | null }>
  >;
  trigger_mode: string;
  readiness_bar: Record<string, number>;
  readiness_blockers: string[];
  decision_rationale: string;
  limitations: string[];
}

export interface TriggerPolicyOut {
  persistence_windows: number;
  hysteresis_low_factor: number;
  cooldown_minutes: number;
  mode: string;
  explanation: string;
}

// Public reasoning record persisted by the Diagnose phase (validated
// structured output plus provider metadata; never hidden reasoning).
export interface ReasoningMeta {
  mode?: string;
  provider?: string;
  requested_model?: string | null;
  actual_model?: string | null;
  latency_ms?: number | null;
  input_tokens?: number | null;
  output_tokens?: number | null;
  fallback?: boolean;
  fallback_reason?: string | null;
  attempted_provider?: string | null;
  public_rationale?: string;
  missing_evidence?: string[];
  requested_test?: string | null;
  disposition?: "rank_hypotheses" | "request_evidence" | "abstain";
  orchestrator_decision?: "awaiting_evidence" | "escalate_without_action" | "continue_to_plan";
  provider_requested_test?: string | null;
  policy_requested_test?: string | null;
  prompt_version?: string;
  schema_version?: string;
  attempted_model?: string | null;
  validated_result?: ValidatedDiagnosis;
  evidence_snapshot?: EvidenceManifest;
}

export interface ValidatedHypothesis {
  cause_id: string;
  label: string;
  confidence: number;
  supporting_refs: string[];
  contradicting_refs: string[];
}

export interface ValidatedDiagnosis {
  hypotheses: ValidatedHypothesis[];
  missing_evidence: string[];
  disposition: "rank_hypotheses" | "request_evidence" | "abstain";
  abstain_reason: string;
  requested_test: string | null;
  public_rationale: string;
}

export interface EvidenceManifest {
  asset?: string;
  allowed_cause_ids?: string[];
  allowed_tests?: string[];
  evidence?: {
    ref: string;
    source: string;
    trust_label: string;
    injection_flag: boolean;
    fact_keys: string[];
    text_chars: number;
  }[];
}

export interface LlmConnectivityTestOut {
  status: string; // exactly "LIVE LLM RESPONSE ACCEPTED" or "LLM NOT USED — DETERMINISTIC FALLBACK"
  accepted: boolean;
  attempts: number;
  configured_mode: string;
  configured_provider: string | null;
  configured_model: string | null;
  attempted_provider: string | null;
  attempted_model: string | null;
  actual_provider: string | null;
  actual_model: string | null;
  request_id: string | null;
  latency_ms: number | null;
  input_tokens: number | null;
  output_tokens: number | null;
  finish_reason: string | null;
  prompt_version: string;
  schema_version: string;
  fallback_reason: string | null;
  snapshot_label: string;
  evidence_manifest: EvidenceManifest;
  result: ValidatedDiagnosis;
  note: string;
}

async function get<T>(path: string): Promise<T> {
  const res = await fetch(`${API_BASE}${path}`, { cache: "no-store" });
  if (!res.ok) throw await responseError(path, res);
  return res.json();
}

async function responseError(path: string, res: Response): Promise<Error> {
  let detail = "";
  try {
    const payload = await res.json();
    detail = typeof payload?.detail === "string" ? payload.detail : "";
  } catch {
    // Preserve the stable status-only fallback for non-JSON proxy errors.
  }
  return new Error(`${path} → ${res.status}${detail ? `: ${detail}` : ""}`);
}

async function post<T>(path: string, body?: unknown): Promise<T> {
  const res = await fetch(`${API_BASE}${path}`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: body ? JSON.stringify(body) : undefined,
  });
  if (!res.ok) throw await responseError(path, res);
  return res.json();
}

export const api = {
  dashboard: () => get<DashboardOut>("/api/dashboard"),
  assets: () => get<AssetSummary[]>("/api/assets"),
  asset: (id: number) => get<AssetDetail>(`/api/assets/${id}`),
  readings: (id: number, hours = 72, until?: string, replayRunId?: number) =>
    get<SensorSeries[]>(
      `/api/assets/${id}/readings?hours=${hours}${until ? `&until=${encodeURIComponent(until)}` : ""}${replayRunId ? `&replay_run_id=${replayRunId}` : ""}`,
    ),
  triggerPolicy: () => get<TriggerPolicyOut>("/api/trigger-policy"),
  incidents: (id: number) => get<IncidentOut[]>(`/api/assets/${id}/incidents`),
  inspections: (id: number) => get<InspectionOut[]>(`/api/assets/${id}/inspections`),
  workOrders: (assetId?: number) =>
    get<WorkOrderOut[]>(`/api/work-orders${assetId ? `?asset_id=${assetId}` : ""}`),
  caseMemory: () => get<CaseMemoryOut[]>("/api/case-memory"),
  runs: () => get<AgentRunOut[]>("/api/agent/runs"),
  run: (id: number) => get<AgentRunDetail>(`/api/agent/runs/${id}`),
  startRun: (assetId: number, trigger: string) =>
    post<AgentRunDetail>("/api/agent/runs", { asset_id: assetId, trigger }),
  advanceRun: (id: number, mode: "one_phase" | "until_blocked" = "until_blocked") =>
    post<AgentRunDetail>(`/api/agent/runs/${id}/advance`, { mode }),
  evidenceRequests: (runId: number) =>
    get<DiagnosticEvidenceRequestOut[]>(`/api/agent/runs/${runId}/evidence-requests`),
  submitEvidence: (
    requestId: number,
    result: Record<string, string | number>,
    submittedBy = "operator",
    source: "operator" | "laboratory" | "sensor" | "import" = "operator",
  ) =>
    post<DiagnosticEvidenceRequestOut>(`/api/agent/evidence-requests/${requestId}/submit`, {
      result,
      submitted_by: submittedBy,
      source,
    }),
  approve: (actionId: number, decidedBy = "operator") =>
    post<AgentActionOut>(`/api/agent/actions/${actionId}/approve`, { decided_by: decidedBy }),
  reject: (actionId: number, decidedBy = "operator") =>
    post<AgentActionOut>(`/api/agent/actions/${actionId}/reject`, { decided_by: decidedBy }),
  testReasoning: () => post<LlmConnectivityTestOut>("/api/agent/reasoning-test"),
  resetDemo: (operator = "operator") =>
    post<{ status: string }>("/api/demo/reset", { confirm: "RESET", operator }),
  // Replay, online inference, and shadow alerts
  replays: () => get<ReplayRunOut[]>("/api/replay"),
  createReplay: (durationHours = 4, speed = 600, sourceStart?: string) =>
    post<ReplayRunOut>("/api/replay", {
      duration_hours: durationHours,
      speed,
      ...(sourceStart ? { source_start: sourceStart } : {}),
    }),
  startReplay: (id: number) => post<ReplayRunOut>(`/api/replay/${id}/start`),
  pauseReplay: (id: number) => post<ReplayRunOut>(`/api/replay/${id}/pause`),
  resetReplay: (id: number) => post<ReplayRunOut>(`/api/replay/${id}/reset`),
  advanceReplay: (id: number, wallSeconds = 1) =>
    post<Record<string, unknown>>(`/api/replay/${id}/advance`, { wall_seconds: wallSeconds }),
  predictions: (assetId: number, limit = 60, replayRunId?: number) =>
    get<ModelPredictionOut[]>(
      `/api/assets/${assetId}/predictions?limit=${limit}${replayRunId ? `&replay_run_id=${replayRunId}` : ""}`,
    ),
  shadowAlerts: (replayRunId?: number, status?: ShadowAlertOut["status"]) => {
    const params = new URLSearchParams();
    if (replayRunId) params.set("replay_run_id", String(replayRunId));
    if (status) params.set("status", status);
    const query = params.toString();
    return get<ShadowAlertOut[]>(`/api/shadow-alerts${query ? `?${query}` : ""}`);
  },
  flagshipEvaluation: () => get<FlagshipEvaluationOut>("/api/model-evaluation/metropt3_iforest_v1"),
  promoteShadowAlert: (id: number, operator: string) =>
    post<IncidentOut>(`/api/shadow-alerts/${id}/promote`, { decided_by: operator }),
  promoteAndInvestigate: (id: number, operator: string) =>
    post<{ incident: IncidentOut; run_id: number; run_status: string }>(
      `/api/shadow-alerts/${id}/promote-investigate`,
      { decided_by: operator },
    ),
  dismissShadowAlert: (id: number, operator: string) =>
    post<ShadowAlertOut>(`/api/shadow-alerts/${id}/dismiss`, { decided_by: operator }),
};

export function riskBg(risk: number): string {
  if (risk >= 0.7) return "bg-risk-high";
  if (risk >= 0.4) return "bg-risk-mid";
  return "bg-risk-low";
}
