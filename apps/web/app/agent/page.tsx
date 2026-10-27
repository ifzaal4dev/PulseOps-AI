"use client";

import { useRouter, useSearchParams } from "next/navigation";
import { Suspense, useCallback, useEffect, useState } from "react";
import {
  api,
  AgentRunDetail,
  AgentRunOut,
  AgentStepOut,
  DiagnosticEvidenceRequestOut,
  LlmConnectivityTestOut,
  ReasoningMeta,
} from "@/lib/api";
import { EmptyState, Panel, PriorityPill, Spinner, StatusPill } from "@/components/ui";

const PHASES = ["observe", "diagnose", "plan", "act", "verify", "learn"] as const;

const PHASE_ICONS: Record<string, string> = {
  observe: "◎",
  diagnose: "⌕",
  plan: "✎",
  act: "⚙",
  verify: "✓",
  learn: "🧠",
};

// Verification-outcome styling: the environment decides the outcome; the UI
// must render failure/adverse/inconclusive as loudly as success.
const VERIFY_LABEL: Record<string, string> = {
  passed: "RISK REDUCTION VERIFIED",
  failed: "VERIFICATION FAILED",
  inconclusive: "INCONCLUSIVE — AWAITING TELEMETRY",
  adverse: "ADVERSE — ESCALATED",
};
const VERIFY_CHIP: Record<string, string> = {
  passed: "bg-risk-low/10 text-risk-low border-risk-low/40",
  failed: "bg-risk-high/10 text-risk-high border-risk-high/40",
  inconclusive: "bg-risk-mid/10 text-risk-mid border-risk-mid/40",
  adverse: "bg-fuchsia-400/10 text-fuchsia-400 border-fuchsia-400/40",
};
const VERIFY_PANEL: Record<string, string> = {
  passed: "border-risk-low/30",
  failed: "border-risk-high/30",
  inconclusive: "border-risk-mid/30",
  adverse: "border-fuchsia-400/30",
};

function LlmTestPanel() {
  const [open, setOpen] = useState(false);
  const [report, setReport] = useState<LlmConnectivityTestOut | null>(null);
  const [testing, setTesting] = useState(false);
  const [error, setError] = useState<string | null>(null);

  async function runTest() {
    setTesting(true);
    setError(null);
    try {
      setReport(await api.testReasoning());
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setTesting(false);
    }
  }

  // Collapsed by default so the investigation is the first thing on screen.
  if (!open) {
    return (
      <Panel
        title="Test configured LLM (optional)"
        right={
          <button
            onClick={() => setOpen(true)}
            className="rounded-md border border-ink-600 px-3 py-1.5 text-xs font-semibold text-zinc-400 hover:text-zinc-200"
          >
            Show
          </button>
        }
      >
        {null}
      </Panel>
    );
  }

  return (
    <Panel
      title="Test configured LLM — production reasoning path, synthetic snapshot"
      right={
        <div className="flex gap-2">
          <button
            onClick={runTest}
            disabled={testing}
            className="rounded-md border border-pulse-400/40 bg-pulse-500/10 px-3 py-1.5 text-xs font-semibold text-pulse-400 hover:bg-pulse-500/20 disabled:opacity-50"
          >
            {testing ? "Testing…" : "Run test"}
          </button>
          <button
            onClick={() => setOpen(false)}
            className="rounded-md border border-ink-600 px-3 py-1.5 text-xs font-semibold text-zinc-400 hover:text-zinc-200"
          >
            Hide
          </button>
        </div>
      }
    >
      {!report && !error && (
        <p className="text-xs text-zinc-500">
          One click sends one bounded request through the same provider registry, evidence snapshot,
          prompt/schema, and safety validator as a real Diagnose — against a clearly labeled
          synthetic snapshot. It creates no incident, approval, or action, and never shows secrets.
          A second HTTP attempt happens only after a transient 429/503 failure.
        </p>
      )}
      {error && (
        <p className="rounded-md border border-risk-high/30 bg-risk-high/5 px-3 py-2 text-xs text-risk-high">
          Test request failed: {error}
        </p>
      )}
      {report && (
        <div className="space-y-3 text-xs">
          <div
            className={`rounded-md border px-3 py-2 font-mono text-sm font-bold ${
              report.accepted
                ? "border-risk-low/40 bg-risk-low/10 text-risk-low"
                : "border-risk-mid/40 bg-risk-mid/10 text-risk-mid"
            }`}
          >
            {report.status}
          </div>
          {report.fallback_reason && (
            <p className="rounded-md bg-ink-800/70 px-3 py-2 text-zinc-400">
              <span className="text-zinc-500">Reason (sanitized): </span>
              {report.fallback_reason}
            </p>
          )}
          <div className="grid gap-x-6 gap-y-1 sm:grid-cols-2 lg:grid-cols-3 text-zinc-400">
            <span>
              configured mode: <b className="text-zinc-200">{report.configured_mode}</b>
            </span>
            <span>
              configured provider/model:{" "}
              <b className="font-mono text-zinc-200">
                {report.configured_provider ?? "—"}
                {report.configured_model ? ` / ${report.configured_model}` : ""}
              </b>
            </span>
            <span>
              attempts: <b className="text-zinc-200">{report.attempts}</b>
            </span>
            <span>
              attempted provider/model:{" "}
              <b className="font-mono text-zinc-200">
                {report.attempted_provider ?? "—"}
                {report.attempted_model ? ` / ${report.attempted_model}` : ""}
              </b>
            </span>
            <span>
              actual provider/model:{" "}
              <b className="font-mono text-zinc-200">
                {report.actual_provider ?? "—"}
                {report.actual_model ? ` / ${report.actual_model}` : ""}
              </b>
            </span>
            <span>
              latency:{" "}
              <b className="font-mono text-zinc-200">
                {report.latency_ms != null ? `${report.latency_ms} ms` : "—"}
              </b>
            </span>
            <span>
              tokens in/out:{" "}
              <b className="font-mono text-zinc-200">
                {report.input_tokens ?? "—"} / {report.output_tokens ?? "—"}
              </b>
            </span>
            <span>
              prompt/schema:{" "}
              <b className="font-mono text-zinc-200">
                {report.prompt_version} · {report.schema_version}
              </b>
            </span>
            <span>
              finish: <b className="font-mono text-zinc-200">{report.finish_reason ?? "—"}</b>
            </span>
          </div>
          <div className="rounded-md border border-ink-700 bg-ink-900/50 p-3">
            <div className="mb-2 font-semibold text-zinc-300">
              Validated structured result{" "}
              <span className="font-normal text-zinc-500">
                (
                {report.accepted
                  ? "external response accepted by the validator"
                  : "deterministic engine — not LLM output"}
                )
              </span>
            </div>
            <div className="grid gap-2 md:grid-cols-2">
              {report.result.hypotheses.map((h, i) => (
                <div
                  key={`${h.cause_id}-${i}`}
                  className="rounded-md border border-ink-700 bg-ink-800/60 p-2.5"
                >
                  <div className="flex items-center justify-between gap-2">
                    <span className="text-zinc-200">
                      {i + 1}. {h.label}
                    </span>
                    <span className="font-mono text-pulse-400">
                      {(h.confidence * 100).toFixed(0)}%
                    </span>
                  </div>
                  <div className="mt-1.5 flex flex-wrap gap-1 font-mono text-[10px] text-zinc-500">
                    {h.supporting_refs.map((r) => (
                      <span key={r} className="chip border border-risk-low/30 text-risk-low">
                        + {r}
                      </span>
                    ))}
                    {h.contradicting_refs.map((r) => (
                      <span key={r} className="chip border border-risk-high/30 text-risk-high">
                        − {r}
                      </span>
                    ))}
                  </div>
                </div>
              ))}
            </div>
            <div className="mt-2 flex flex-wrap gap-x-5 gap-y-1 text-zinc-400">
              <span>
                diagnostic disposition:{" "}
                <b className="text-zinc-200">{report.result.disposition.replace(/_/g, " ")}</b>
              </span>
              {report.result.abstain_reason && (
                <span>
                  reason: <b className="text-zinc-200">{report.result.abstain_reason}</b>
                </span>
              )}
              <span>
                requested test:{" "}
                <b className="text-zinc-200">{report.result.requested_test ?? "none"}</b>
              </span>
            </div>
            {report.result.missing_evidence.length > 0 && (
              <p className="mt-1 text-zinc-400">
                Missing evidence: {report.result.missing_evidence.join("; ")}
              </p>
            )}
            {report.result.public_rationale && (
              <p className="mt-2 rounded bg-ink-800/70 p-2 text-zinc-300">
                <span className="text-zinc-500">Public rationale:</span>{" "}
                {report.result.public_rationale}
              </p>
            )}
          </div>
          <p className="text-[10px] text-zinc-600">
            {report.snapshot_label} · {report.note}
          </p>
        </div>
      )}
    </Panel>
  );
}

function phaseState(run: AgentRunDetail, phase: string): "done" | "current" | "pending" {
  if (run.steps.some((s) => s.phase === phase && s.status === "completed")) return "done";
  if (run.current_phase === phase && run.status !== "completed") return "current";
  return "pending";
}

function EvidenceRequestPanel({
  request,
  runId,
  onRefresh,
}: {
  request: DiagnosticEvidenceRequestOut;
  runId: number;
  onRefresh: () => void;
}) {
  const field = request.result_schema.field ?? "finding";
  const choices = request.result_schema.enum ?? [];
  const [choice, setChoice] = useState(choices[0] ?? "");
  const [source, setSource] = useState<"operator" | "laboratory" | "sensor" | "import">("operator");
  const [submittedBy, setSubmittedBy] = useState("operator");
  const [numericValue, setNumericValue] = useState("");
  const [unit, setUnit] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  async function submit() {
    if (!choice || !submittedBy.trim()) return;
    setBusy(true);
    setError(null);
    try {
      const result: Record<string, string | number> = { [field]: choice };
      if (numericValue.trim()) result.value = Number(numericValue);
      if (unit.trim()) result.unit = unit.trim();
      await api.submitEvidence(request.id, result, submittedBy.trim(), source);
      await api.advanceRun(runId, "until_blocked");
      onRefresh();
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setBusy(false);
    }
  }

  return (
    <Panel className="border-sky-400/40 bg-sky-400/5">
      <div className="panel-header mb-2 text-sky-300">Independent evidence required</div>
      <p className="text-sm font-semibold text-zinc-100">
        {request.result_schema.label ?? request.test_id}
      </p>
      <p className="mt-1 text-xs text-zinc-400">{request.result_schema.instructions}</p>
      <p className="mt-2 rounded-md border border-ink-700 bg-ink-900/60 p-2 text-[11px] text-zinc-400">
        Diagnosis is paused. PulseOps has not inferred a result and has not proposed maintenance.
        Submit only an independently observed, structured result; it will be audited and Diagnose
        will resume.
      </p>
      <div className="mt-3 grid gap-2 sm:grid-cols-3">
        <label className="text-[11px] text-zinc-500">
          Result ({field})
          <select
            value={choice}
            onChange={(e) => setChoice(e.target.value)}
            className="mt-1 w-full rounded-md border border-ink-600 bg-ink-900 px-2 py-2 text-xs text-zinc-200"
          >
            {choices.map((value) => (
              <option key={value} value={value}>
                {value.replace(/_/g, " ")}
              </option>
            ))}
          </select>
        </label>
        <label className="text-[11px] text-zinc-500">
          Evidence source
          <select
            value={source}
            onChange={(e) => setSource(e.target.value as typeof source)}
            className="mt-1 w-full rounded-md border border-ink-600 bg-ink-900 px-2 py-2 text-xs text-zinc-200"
          >
            {(["operator", "laboratory", "sensor", "import"] as const).map((value) => (
              <option key={value} value={value}>
                {value}
              </option>
            ))}
          </select>
        </label>
        <label className="text-[11px] text-zinc-500">
          Submitted by
          <input
            value={submittedBy}
            onChange={(e) => setSubmittedBy(e.target.value)}
            maxLength={120}
            className="mt-1 w-full rounded-md border border-ink-600 bg-ink-900 px-2 py-2 text-xs text-zinc-200"
          />
        </label>
      </div>
      <div className="mt-2 grid gap-2 sm:grid-cols-2">
        <label className="text-[11px] text-zinc-500">
          Numeric measurement (optional)
          <input
            type="number"
            value={numericValue}
            onChange={(e) => setNumericValue(e.target.value)}
            className="mt-1 w-full rounded-md border border-ink-600 bg-ink-900 px-2 py-2 text-xs text-zinc-200"
          />
        </label>
        <label className="text-[11px] text-zinc-500">
          Unit (optional)
          <input
            value={unit}
            onChange={(e) => setUnit(e.target.value)}
            maxLength={30}
            className="mt-1 w-full rounded-md border border-ink-600 bg-ink-900 px-2 py-2 text-xs text-zinc-200"
          />
        </label>
      </div>
      {error && <p className="mt-2 text-xs text-risk-high">{error}</p>}
      <button
        onClick={submit}
        disabled={busy || !choice || !submittedBy.trim()}
        className="mt-3 rounded-md border border-sky-400/40 bg-sky-400/10 px-3 py-2 text-xs font-semibold text-sky-300 hover:bg-sky-400/20 disabled:opacity-50"
      >
        {busy ? "Validating and resuming…" : "Submit evidence & resume Diagnose"}
      </button>
    </Panel>
  );
}

function AgentControlRoom() {
  const params = useSearchParams();
  const router = useRouter();
  const selectedId = params.get("run") ? Number(params.get("run")) : null;

  const [runs, setRuns] = useState<AgentRunOut[]>([]);
  const [run, setRun] = useState<AgentRunDetail | null>(null);
  const [busy, setBusy] = useState(false);
  const [decisionError, setDecisionError] = useState<string | null>(null);

  const refresh = useCallback(() => {
    api.runs().then(setRuns).catch(console.error);
    if (selectedId) api.run(selectedId).then(setRun).catch(console.error);
  }, [selectedId]);

  useEffect(() => {
    refresh();
    const t = setInterval(refresh, 3000);
    return () => clearInterval(t);
  }, [refresh]);

  async function decide(actionId: number, verdict: "approve" | "reject") {
    setBusy(true);
    setDecisionError(null);
    try {
      await (verdict === "approve"
        ? api.approve(actionId, "operator")
        : api.reject(actionId, "operator"));
      if (selectedId) await api.advanceRun(selectedId, "until_blocked");
      refresh();
    } catch (e) {
      setDecisionError(e instanceof Error ? e.message : String(e));
      refresh();
    } finally {
      setBusy(false);
    }
  }

  const pendingAction = run?.actions.find((a) => a.status === "proposed") ?? null;
  const pendingEvidence = run?.evidence_requests.find((r) => r.status === "pending") ?? null;

  return (
    <div className="space-y-5">
      <header>
        <h1 className="text-xl font-bold tracking-tight text-zinc-100">Agent Control Room</h1>
        <p className="text-sm text-zinc-500">
          Observe → Diagnose → Plan → Act → Verify → Learn · every step and tool call is persisted
          and auditable
        </p>
      </header>

      <LlmTestPanel />

      <div className="grid lg:grid-cols-4 gap-4">
        {/* Run list */}
        <Panel title="Investigations" className="lg:col-span-1 self-start">
          {runs.length === 0 ? (
            <EmptyState text="No runs yet. Start one from an asset page." />
          ) : (
            <div className="space-y-2">
              {runs.map((r) => (
                <button
                  key={r.id}
                  onClick={() => router.push(`/agent?run=${r.id}`)}
                  className={`w-full text-left rounded-lg border px-3 py-2.5 transition-colors ${
                    r.id === selectedId
                      ? "border-pulse-400/50 bg-pulse-500/10"
                      : "border-ink-700 bg-ink-800/60 hover:border-ink-600"
                  }`}
                >
                  <div className="flex items-center justify-between">
                    <span className="font-mono text-xs text-zinc-400">run #{r.id}</span>
                    <StatusPill status={r.status} />
                  </div>
                  <div className="mt-1 text-[11px] text-zinc-500 truncate">{r.trigger}</div>
                </button>
              ))}
            </div>
          )}
        </Panel>

        {/* Run detail */}
        <div className="lg:col-span-3 space-y-4">
          {!run ? (
            <Panel>
              <EmptyState text="Select an investigation" />
            </Panel>
          ) : (
            <>
              {/* Phase timeline */}
              <Panel
                title={`Run #${run.id} — loop progress`}
                right={
                  <div className="flex items-center gap-2">
                    <span className="chip hidden border border-ink-600 bg-ink-800 font-mono text-zinc-500 sm:inline-flex">
                      evidence: {run.evidence_domain}
                    </span>
                    <StatusPill status={run.status} />
                  </div>
                }
              >
                <div className="flex items-center">
                  {PHASES.map((p, i) => {
                    const st = phaseState(run, p);
                    return (
                      <div key={p} className="flex items-center flex-1 last:flex-none">
                        <div className="flex flex-col items-center gap-1.5">
                          <div
                            className={`h-9 w-9 rounded-full grid place-items-center border text-sm ${
                              st === "done"
                                ? "bg-pulse-500/15 border-pulse-400/50 text-pulse-400"
                                : st === "current"
                                  ? "bg-risk-mid/15 border-risk-mid/50 text-risk-mid animate-pulse"
                                  : "bg-ink-800 border-ink-700 text-zinc-600"
                            }`}
                          >
                            {PHASE_ICONS[p]}
                          </div>
                          <span
                            className={`text-[10px] uppercase tracking-wider font-medium ${
                              st === "done"
                                ? "text-pulse-400"
                                : st === "current"
                                  ? "text-risk-mid"
                                  : "text-zinc-600"
                            }`}
                          >
                            {p}
                          </span>
                        </div>
                        {i < PHASES.length - 1 && (
                          <div
                            className={`h-px flex-1 mx-2 mb-5 ${
                              st === "done" ? "bg-pulse-400/40" : "bg-ink-700"
                            }`}
                          />
                        )}
                      </div>
                    );
                  })}
                </div>
                {run.summary && (
                  <p className="mt-4 rounded-lg bg-ink-800/80 border border-ink-700 p-3 text-xs text-zinc-300">
                    {run.summary}
                  </p>
                )}
              </Panel>

              {/* Reasoning transparency */}
              <ReasoningPanel run={run} />

              {pendingEvidence && (
                <EvidenceRequestPanel
                  request={pendingEvidence}
                  runId={run.id}
                  onRefresh={refresh}
                />
              )}

              {/* Approval gate */}
              {pendingAction && (
                <Panel className="border-risk-mid/40 bg-risk-mid/5">
                  <div className="flex items-start justify-between gap-4">
                    <div>
                      <div className="panel-header text-risk-mid mb-2">
                        ⏸ Human approval required
                      </div>
                      <div className="text-sm font-semibold text-zinc-100">
                        {pendingAction.payload.title}
                      </div>
                      <p className="mt-1 text-xs text-zinc-400">
                        {pendingAction.payload.description}
                      </p>
                      <div className="mt-2 flex flex-wrap items-center gap-2 text-xs">
                        <PriorityPill priority={pendingAction.payload.priority ?? "medium"} />
                        <span className="text-zinc-500">
                          root cause:{" "}
                          <span className="text-zinc-300">{pendingAction.payload.root_cause}</span>
                        </span>
                        <span className="text-zinc-500">
                          confidence:{" "}
                          <span className="font-mono text-zinc-300">
                            {((pendingAction.payload.confidence ?? 0) * 100).toFixed(0)}%
                          </span>
                        </span>
                        <span className="text-zinc-500">
                          {pendingAction.payload.trigger_source?.startsWith("metropt3_")
                            ? "trigger anomaly score: "
                            : "trigger facility risk: "}
                          <span className="font-mono text-risk-high">
                            {pendingAction.payload.trigger_source?.startsWith("metropt3_")
                              ? (pendingAction.payload.trigger_score ?? 0).toFixed(3)
                              : `${((pendingAction.payload.trigger_score ?? pendingAction.payload.risk_before ?? 0) * 100).toFixed(0)}%`}
                          </span>
                        </span>
                        {pendingAction.payload.trigger_threshold != null && (
                          <span className="text-zinc-600">
                            threshold{" "}
                            <span className="font-mono">
                              {pendingAction.payload.trigger_threshold.toFixed(3)}
                            </span>
                          </span>
                        )}
                        {pendingAction.payload.trigger_source && (
                          <span className="chip border border-ink-600 bg-ink-800 font-mono text-zinc-500">
                            {pendingAction.payload.trigger_source}
                          </span>
                        )}
                        {pendingAction.payload.trigger_prediction_id && (
                          <span className="font-mono text-zinc-600">
                            prediction #{pendingAction.payload.trigger_prediction_id} · replay #
                            {pendingAction.payload.replay_run_id}
                          </span>
                        )}
                      </div>
                      {pendingAction.payload.risk_semantics && (
                        <p className="mt-2 text-[10px] text-zinc-600">
                          Trigger:{" "}
                          {pendingAction.payload.trigger_score_semantics || "facility risk score"}.{" "}
                          Post-action verification uses a like-for-like{" "}
                          {pendingAction.payload.risk_source} baseline (
                          {((pendingAction.payload.risk_before ?? 0) * 100).toFixed(0)}% before)
                          because the maintenance response is simulated.
                        </p>
                      )}
                    </div>
                    <div className="flex gap-2 shrink-0">
                      <button
                        onClick={() => decide(pendingAction.id, "approve")}
                        disabled={busy}
                        className="rounded-lg bg-risk-low/15 border border-risk-low/40 px-4 py-2 text-sm font-semibold text-risk-low hover:bg-risk-low/25 disabled:opacity-50"
                      >
                        ✓ Approve
                      </button>
                      <button
                        onClick={() => decide(pendingAction.id, "reject")}
                        disabled={busy}
                        className="rounded-lg bg-risk-high/10 border border-risk-high/40 px-4 py-2 text-sm font-semibold text-risk-high hover:bg-risk-high/20 disabled:opacity-50"
                      >
                        ✕ Reject
                      </button>
                    </div>
                  </div>
                  {decisionError && (
                    <p
                      role="alert"
                      className="mt-3 rounded-md border border-risk-high/30 bg-risk-high/5 px-3 py-2 text-xs text-risk-high"
                    >
                      Decision not recorded: {decisionError}
                    </p>
                  )}
                </Panel>
              )}

              {/* Verification result (latest attempt) */}
              {run.verification && (
                <Panel
                  title="Verification — before / after"
                  className={VERIFY_PANEL[run.verification.outcome]}
                >
                  <div className="flex items-center gap-8">
                    <div>
                      <div className="text-[11px] text-zinc-500 uppercase tracking-wider">
                        Risk before
                      </div>
                      <div className="font-mono text-3xl font-bold text-risk-high">
                        {(run.verification.risk_before * 100).toFixed(0)}%
                      </div>
                    </div>
                    <div className="text-2xl text-zinc-600">→</div>
                    <div>
                      <div className="text-[11px] text-zinc-500 uppercase tracking-wider">
                        Risk after
                      </div>
                      <div
                        className={`font-mono text-3xl font-bold ${
                          run.verification.outcome === "passed"
                            ? "text-risk-low"
                            : run.verification.outcome === "adverse"
                              ? "text-risk-high"
                              : "text-risk-mid"
                        }`}
                      >
                        {(run.verification.risk_after * 100).toFixed(0)}%
                      </div>
                    </div>
                    <div className="flex-1" />
                    <div
                      className={`chip border text-sm px-3 py-1.5 ${VERIFY_CHIP[run.verification.outcome]}`}
                    >
                      Δ {(run.verification.risk_delta * 100).toFixed(0)} pts ·{" "}
                      {VERIFY_LABEL[run.verification.outcome]}
                    </div>
                  </div>
                  <p className="mt-3 text-xs text-zinc-400">{run.verification.outcome_reason}</p>
                  {Boolean(
                    run.verification.details.model_source_before ||
                    run.verification.details.model_source_after,
                  ) && (
                    <p className="mt-2 text-[10px] text-zinc-600">
                      Like-for-like verification source:{" "}
                      <span className="font-mono">
                        {String(run.verification.details.model_source_before ?? "unknown")} →{" "}
                        {String(run.verification.details.model_source_after ?? "unknown")}
                      </span>
                      {Boolean(run.verification.details.trigger_source) && (
                        <>
                          {" "}
                          · original trigger{" "}
                          <span className="font-mono">
                            {String(run.verification.details.trigger_source)}
                          </span>{" "}
                          is shown separately
                        </>
                      )}
                    </p>
                  )}
                </Panel>
              )}

              {/* Step timeline */}
              <Panel title="Reasoning timeline (audit summaries)">
                <div className="space-y-2.5">
                  {run.steps.map((s) => (
                    <StepRow key={s.id} step={s} />
                  ))}
                </div>
              </Panel>

              {/* Tool audit trail */}
              <Panel title={`Tool call audit trail (${run.tool_calls.length})`}>
                <div className="space-y-1.5">
                  {run.tool_calls.map((tc) => (
                    <div
                      key={tc.id}
                      id={`toolcall-${tc.id}`}
                      className="flex items-center gap-3 rounded-md border border-ink-700 bg-ink-800/50 px-3 py-2 text-xs transition-shadow"
                    >
                      <span className="font-mono text-zinc-600 w-6 text-right">#{tc.seq}</span>
                      <span className="font-mono font-medium text-pulse-400 w-56 truncate">
                        {tc.tool_name}
                      </span>
                      <span
                        className={`chip border shrink-0 ${
                          tc.trust_label === "untrusted_evidence"
                            ? "bg-risk-mid/10 text-risk-mid border-risk-mid/30"
                            : tc.trust_label === "structured_observation"
                              ? "bg-pulse-500/10 text-pulse-400 border-pulse-400/30"
                              : "bg-sky-400/10 text-sky-400 border-sky-400/30"
                        }`}
                      >
                        {tc.trust_label === "untrusted_evidence"
                          ? "untrusted evidence"
                          : tc.trust_label === "structured_observation"
                            ? "structured observation"
                            : "trusted system"}
                      </span>
                      {tc.injection_flag && (
                        <span className="chip border bg-risk-high/15 text-risk-high border-risk-high/40">
                          ⚠ injection flagged
                        </span>
                      )}
                      <span className="text-zinc-400 truncate flex-1">{tc.result_summary}</span>
                    </div>
                  ))}
                </div>
              </Panel>
            </>
          )}
        </div>
      </div>
    </div>
  );
}

// Evidence refs → the audited tool call that produced them (the audit trail
// shows source, trust label, content summary, and injection warnings).
const REF_TOOL: Record<string, string> = {
  case_memory: "query_case_memory",
  manual_chunks: "search_maintenance_manuals",
  inspections: "get_recent_inspections",
  work_orders: "get_open_work_orders",
  diagnostic_evidence: "get_diagnostic_evidence",
  sensor_stats: "get_recent_sensor_readings",
  failure_prediction: "run_failure_prediction",
};

function jumpToEvidence(run: AgentRunDetail, ref: string) {
  const tool = REF_TOOL[ref.split(":")[0]];
  const tc = run.tool_calls.find((t) => t.tool_name === tool);
  if (!tc) return;
  const el = document.getElementById(`toolcall-${tc.id}`);
  if (!el) return;
  el.scrollIntoView({ behavior: "smooth", block: "center" });
  el.classList.add("ring-2", "ring-pulse-400");
  setTimeout(() => el.classList.remove("ring-2", "ring-pulse-400"), 2500);
}

function ReasoningPanel({ run }: { run: AgentRunDetail }) {
  const diagnose = [...run.steps].reverse().find((s) => s.phase === "diagnose");
  if (!diagnose) return null;
  const output = diagnose.output as {
    reasoning?: ReasoningMeta;
    diagnostic_tests?: {
      test_id: string;
      label: string;
      requested_by: string;
      result: Record<string, string | number>;
      source: string;
      ref: string;
    }[];
    hypotheses?: {
      cause: string;
      confidence: number;
      provider_citations?: { supporting: string[]; contradicting: string[] };
    }[];
  };
  const r = output.reasoning;
  if (!r) return null;
  const tests = output.diagnostic_tests ?? [];
  const citations = output.hypotheses?.[0]?.provider_citations;
  const validated = r.validated_result;
  const manifest = r.evidence_snapshot;
  const providerUnavailable = r.fallback_reason?.startsWith("provider unavailable:") ?? false;
  const invalidProviderOutput = r.fallback_reason?.startsWith("invalid output:") ?? false;
  return (
    <Panel
      title="Reasoning — mode, provider, grounding"
      right={
        r.fallback ? (
          <span className="chip border bg-risk-mid/10 text-risk-mid border-risk-mid/40">
            DETERMINISTIC FALLBACK
          </span>
        ) : (
          <span className="chip border bg-pulse-500/10 text-pulse-400 border-pulse-400/30 uppercase">
            {r.mode}
          </span>
        )
      }
    >
      <div className="space-y-2 text-xs">
        <div className="flex flex-wrap gap-2">
          <span className="chip border bg-ink-800 text-zinc-300 border-ink-600 font-mono">
            provider: {r.provider ?? "deterministic"}
          </span>
          {r.requested_model && (
            <span className="chip border bg-ink-800 text-zinc-300 border-ink-600 font-mono">
              requested: {r.requested_model}
            </span>
          )}
          {r.actual_model && r.actual_model !== r.requested_model && (
            <span className="chip border bg-risk-mid/10 text-risk-mid border-risk-mid/30 font-mono">
              served: {r.actual_model}
            </span>
          )}
          {r.latency_ms != null && r.latency_ms > 0 && (
            <span className="chip border bg-ink-800 text-zinc-400 border-ink-600 font-mono">
              {r.latency_ms} ms
            </span>
          )}
          {r.input_tokens != null && (
            <span className="chip border bg-ink-800 text-zinc-400 border-ink-600 font-mono">
              {r.input_tokens}→{r.output_tokens ?? "?"} tok
            </span>
          )}
          <span className="chip border bg-ink-800 text-zinc-500 border-ink-600 font-mono">
            {r.prompt_version}
          </span>
        </div>
        {r.fallback && (
          <p className="rounded-md border border-risk-mid/30 bg-risk-mid/5 px-3 py-2 text-risk-mid">
            {providerUnavailable
              ? "No LLM response was received."
              : invalidProviderOutput
                ? "An LLM response was received but rejected by the safety validator."
                : "The LLM path failed closed."}{" "}
            {r.fallback_reason}
            {r.attempted_provider && (
              <>
                {" "}
                (attempted: {r.attempted_provider}/
                {r.attempted_model ?? r.requested_model ?? "unknown model"})
              </>
            )}
            . The deterministic diagnosis below is authoritative.
          </p>
        )}
        {r.public_rationale && (
          <p className="text-zinc-300">
            <span className="text-zinc-500">rationale:</span> {r.public_rationale}
          </p>
        )}
        {citations && (citations.supporting.length > 0 || citations.contradicting.length > 0) && (
          <div className="flex flex-wrap gap-1.5 items-center">
            <span className="text-zinc-500">top-hypothesis citations:</span>
            {citations.supporting.map((ref) => (
              <button
                key={ref}
                onClick={() => jumpToEvidence(run, ref)}
                className="chip border bg-risk-low/10 text-risk-low border-risk-low/30 font-mono hover:bg-risk-low/20"
                title="supporting evidence — click to jump to the audited tool call (source, trust label, injection flags)"
              >
                {ref}
              </button>
            ))}
            {citations.contradicting.map((ref) => (
              <button
                key={ref}
                onClick={() => jumpToEvidence(run, ref)}
                className="chip border bg-risk-high/10 text-risk-high border-risk-high/30 font-mono hover:bg-risk-high/20"
                title="contradicting evidence — click to jump to the audited tool call"
              >
                ⊘ {ref}
              </button>
            ))}
          </div>
        )}
        {(r.missing_evidence?.length ?? 0) > 0 && (
          <p className="text-zinc-400">
            <span className="text-zinc-500">missing evidence:</span>{" "}
            {r.missing_evidence!.join("; ")}
          </p>
        )}
        {r.orchestrator_decision && (
          <p className="rounded-md border border-ink-700 bg-ink-900/60 px-3 py-2 text-zinc-300">
            <span className="text-zinc-500">PulseOps policy outcome:</span>{" "}
            <b>{r.orchestrator_decision.replace(/_/g, " ")}</b>
            {r.policy_requested_test && (
              <span className="text-zinc-500">
                {" "}
                · deterministic policy requested {r.policy_requested_test}
              </span>
            )}
            {r.provider_requested_test && (
              <span className="text-zinc-500">
                {" "}
                · provider requested {r.provider_requested_test}
              </span>
            )}
          </p>
        )}
        {tests.length > 0 && (
          <div className="rounded-md border border-pulse-400/25 bg-pulse-500/5 px-3 py-2">
            <span className="text-pulse-400 font-semibold">Independent evidence received:</span>{" "}
            {tests.map((t) => (
              <span key={t.test_id} className="text-zinc-300">
                {t.label} → <b>{Object.values(t.result).join(" · ")}</b>{" "}
                <span className="text-zinc-500">
                  ({t.source}; requested by {t.requested_by}; audited as {t.ref})
                </span>
              </span>
            ))}
          </div>
        )}
        {validated && (
          <details open={!r.fallback} className="rounded-md border border-ink-700 bg-ink-900/50">
            <summary className="cursor-pointer px-3 py-2 font-semibold text-zinc-300">
              Validated structured decision{" "}
              {r.fallback
                ? "(deterministic fallback: LLM output not used)"
                : r.provider === "deterministic"
                  ? "(deterministic engine, no LLM)"
                  : "(LLM response accepted)"}
            </summary>
            <div className="space-y-3 border-t border-ink-700 px-3 py-3">
              <div className="grid gap-2 md:grid-cols-3">
                {validated.hypotheses.map((h, index) => (
                  <div
                    key={`${h.cause_id}-${index}`}
                    className="rounded-md border border-ink-700 bg-ink-800/60 p-3"
                  >
                    <div className="flex items-center justify-between gap-2">
                      <span className="font-medium text-zinc-200">
                        {index + 1}. {h.label}
                      </span>
                      <span className="font-mono text-pulse-400">
                        {(h.confidence * 100).toFixed(0)}%
                      </span>
                    </div>
                    <div className="mt-2 h-1.5 overflow-hidden rounded bg-ink-700">
                      <div
                        className="h-full bg-pulse-400/75"
                        style={{ width: `${h.confidence * 100}%` }}
                      />
                    </div>
                    <div className="mt-2 flex flex-wrap gap-1">
                      {h.supporting_refs.map((ref) => (
                        <button
                          key={ref}
                          onClick={() => jumpToEvidence(run, ref)}
                          className="chip border border-risk-low/30 bg-risk-low/10 font-mono text-risk-low"
                        >
                          + {ref}
                        </button>
                      ))}
                      {h.contradicting_refs.map((ref) => (
                        <button
                          key={ref}
                          onClick={() => jumpToEvidence(run, ref)}
                          className="chip border border-risk-high/30 bg-risk-high/10 font-mono text-risk-high"
                        >
                          − {ref}
                        </button>
                      ))}
                    </div>
                  </div>
                ))}
              </div>
              <div className="flex flex-wrap gap-x-5 gap-y-1 text-zinc-400">
                <span>
                  diagnostic disposition:{" "}
                  <b className="text-zinc-200">{validated.disposition.replace(/_/g, " ")}</b>
                </span>
                {validated.requested_test && (
                  <span>
                    requested test: <b className="text-pulse-400">{validated.requested_test}</b>
                  </span>
                )}
              </div>
              {validated.missing_evidence.length > 0 && (
                <p className="text-zinc-400">
                  Missing evidence: {validated.missing_evidence.join("; ")}
                </p>
              )}
              {validated.public_rationale && (
                <p className="rounded-md bg-ink-800/70 p-2 text-zinc-300">
                  <span className="text-zinc-500">Public rationale:</span>{" "}
                  {validated.public_rationale}
                </p>
              )}
            </div>
          </details>
        )}
        {manifest && (
          <details className="rounded-md border border-ink-700 bg-ink-900/50">
            <summary className="cursor-pointer px-3 py-2 font-semibold text-zinc-300">
              Evidence supplied to the reasoning provider ({manifest.evidence?.length ?? 0} items)
            </summary>
            <div className="space-y-2 border-t border-ink-700 px-3 py-3">
              <div className="flex flex-wrap gap-2 text-[11px] text-zinc-500">
                <span>asset: {manifest.asset ?? "unknown"}</span>
                <span>allowed causes: {manifest.allowed_cause_ids?.join(", ") || "none"}</span>
                <span>
                  independent evidence types: {manifest.allowed_tests?.join(", ") || "none"}
                </span>
              </div>
              <div className="grid gap-1.5 md:grid-cols-2">
                {manifest.evidence?.map((item) => (
                  <button
                    key={item.ref}
                    onClick={() => jumpToEvidence(run, item.ref)}
                    className="rounded-md border border-ink-700 bg-ink-800/60 px-2.5 py-2 text-left hover:border-pulse-400/40"
                  >
                    <div className="flex items-center justify-between gap-2">
                      <span className="font-mono text-pulse-400">{item.ref}</span>
                      <span className="chip border border-ink-600 text-zinc-500">
                        {item.trust_label}
                      </span>
                    </div>
                    <div className="mt-1 text-[10px] text-zinc-500">
                      {item.source} · facts: {item.fact_keys.join(", ") || "text only"} ·{" "}
                      {item.text_chars} text chars
                      {item.injection_flag ? " · injection warning" : ""}
                    </div>
                  </button>
                ))}
              </div>
              <p className="text-[10px] text-zinc-600">
                This is a provenance manifest, not hidden chain-of-thought. Raw untrusted text
                remains inside audited tool records.
              </p>
            </div>
          </details>
        )}
        <p className="text-[10px] text-zinc-600">
          Every citation ref resolves to an audited tool call below (source, trust label, content,
          injection warnings). No hidden reasoning or prompts are stored.
        </p>
      </div>
    </Panel>
  );
}

function StepRow({ step }: { step: AgentStepOut }) {
  const [open, setOpen] = useState(false);
  const hypotheses = (step.output as { hypotheses?: { cause: string; confidence: number }[] })
    ?.hypotheses;
  return (
    <div className="rounded-lg border border-ink-700 bg-ink-800/60">
      <button
        onClick={() => setOpen(!open)}
        className="w-full flex items-start gap-3 px-4 py-3 text-left"
      >
        <span className="chip border bg-pulse-500/10 text-pulse-400 border-pulse-400/25 uppercase mt-0.5 w-20 justify-center shrink-0">
          {step.phase}
        </span>
        <span className="text-xs text-zinc-300 leading-relaxed">{step.reasoning_summary}</span>
      </button>
      {open && hypotheses && (
        <div className="border-t border-ink-700 px-4 py-3">
          <div className="panel-header mb-2">Ranked root-cause hypotheses</div>
          <div className="space-y-1.5">
            {hypotheses.map((h, i) => (
              <div key={h.cause} className="flex items-center gap-3 text-xs">
                <span className="font-mono text-zinc-600 w-5">{i + 1}.</span>
                <span className={i === 0 ? "text-zinc-100 font-medium" : "text-zinc-400"}>
                  {h.cause}
                </span>
                <div className="flex-1 h-1 rounded bg-ink-700 overflow-hidden max-w-48">
                  <div
                    className="h-full bg-pulse-400/70"
                    style={{ width: `${h.confidence * 100}%` }}
                  />
                </div>
                <span className="font-mono text-zinc-500">{(h.confidence * 100).toFixed(0)}%</span>
              </div>
            ))}
          </div>
        </div>
      )}
    </div>
  );
}

export default function Page() {
  return (
    <Suspense
      fallback={
        <div className="py-20 text-center">
          <Spinner />
        </div>
      }
    >
      <AgentControlRoom />
    </Suspense>
  );
}
