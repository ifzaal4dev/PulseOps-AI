"use client";

import Link from "next/link";
import { useCallback, useEffect, useState } from "react";
import {
  api,
  AssetSummary,
  FlagshipEvaluationOut,
  ModelPredictionOut,
  ReplayRunOut,
  SensorSeries,
  ShadowAlertOut,
  TriggerPolicyOut,
} from "@/lib/api";
import { EmptyState, Panel, Spinner, StatusPill } from "@/components/ui";
import {
  InvestigationTimeline,
  PredictionDetailPanel,
  TriggerStateMachine,
} from "@/components/InvestigationTimeline";

const fmt = (s: string | null | undefined) =>
  s ? new Date(s).toISOString().replace("T", " ").slice(0, 19) + "Z" : "—";

function PresetRow({
  busy,
  onCreate,
}: {
  busy: boolean;
  onCreate: (hours: number, speed: number, sourceStart?: string) => void;
}) {
  const [customStart, setCustomStart] = useState("2020-04-18T00:00");
  const [customHours, setCustomHours] = useState(6);
  return (
    <div className="flex flex-wrap items-center gap-2">
      <button
        onClick={() => onCreate(4, 600)}
        disabled={busy}
        className="rounded-lg bg-pulse-500/15 border border-pulse-400/40 px-3 py-2 text-xs font-semibold text-pulse-400 hover:bg-pulse-500/25 disabled:opacity-50"
        title="Feb 1 2020 — normal compressor operation"
      >
        Healthy baseline (Feb 1 · 4h · 600×)
      </button>
      <button
        onClick={() => onCreate(10, 3600, "2020-06-05T10:00:00Z")}
        disabled={busy}
        className="rounded-lg bg-risk-high/10 border border-risk-high/40 px-3 py-2 text-xs font-semibold text-risk-high hover:bg-risk-high/20 disabled:opacity-50"
        title="Documented MetroPT-3 air-leak failure report #3 (Jun 5–7 2020) — produces a shadow would-trigger alert"
      >
        ⚠ June 5 air-leak event (10h · 3600×)
      </button>
      <span className="flex items-center gap-1.5 text-xs text-zinc-500">
        custom:
        <input
          type="datetime-local"
          value={customStart}
          min="2020-02-01T00:00"
          max="2020-08-31T23:00"
          onChange={(e) => setCustomStart(e.target.value)}
          className="rounded-md bg-ink-800 border border-ink-700 px-2 py-1.5 font-mono text-[11px] text-zinc-300"
        />
        <input
          type="number"
          value={customHours}
          min={1}
          max={72}
          onChange={(e) => setCustomHours(Number(e.target.value))}
          className="w-14 rounded-md bg-ink-800 border border-ink-700 px-2 py-1.5 font-mono text-[11px] text-zinc-300"
        />
        h
        <button
          onClick={() => onCreate(customHours, 3600, customStart + ":00Z")}
          disabled={busy}
          className="rounded-lg bg-ink-800 border border-ink-600 px-3 py-1.5 text-xs font-semibold text-zinc-300 hover:border-pulse-400/40 disabled:opacity-50"
        >
          Create
        </button>
      </span>
    </div>
  );
}

export default function ReplayLab() {
  const [runs, setRuns] = useState<ReplayRunOut[]>([]);
  const [apu, setApu] = useState<AssetSummary | null>(null);
  const [series, setSeries] = useState<SensorSeries[]>([]);
  const [preds, setPreds] = useState<ModelPredictionOut[]>([]);
  const [alerts, setAlerts] = useState<ShadowAlertOut[]>([]);
  const [policy, setPolicy] = useState<TriggerPolicyOut | null>(null);
  const [evaluation, setEvaluation] = useState<FlagshipEvaluationOut | null>(null);
  const [selectedPrediction, setSelectedPrediction] = useState<ModelPredictionOut | null>(null);
  const [runsByIncident, setRunsByIncident] = useState<Record<number, number>>({});
  const [busy, setBusy] = useState(false);
  const [note, setNote] = useState<string | null>(null);

  const run = runs[0] ?? null;
  const latestPrediction = preds[0] ?? null;
  const ascendingPreds = [...preds].reverse(); // API returns newest first
  let thresholdStreak = 0;
  for (const prediction of preds) {
    if (!prediction.alert) break;
    thresholdStreak += 1;
  }
  const pendingAlerts = alerts.filter((a) => a.status === "observed");

  const refresh = useCallback(async () => {
    const [runList, assets] = await Promise.all([api.replays(), api.assets()]);
    const currentRun = runList[0] ?? null;
    const alertList = await api.shadowAlerts(currentRun?.id);
    setRuns(runList);
    const apuAsset = assets.find((a) => a.asset_type === "air_compressor") ?? null;
    setApu(apuAsset);
    setAlerts(alertList);
    if (alertList.some((a) => a.incident_id)) {
      const agentRuns = await api.runs();
      const map: Record<number, number> = {};
      for (const r of agentRuns) if (r.incident_id != null) map[r.incident_id] = r.id;
      setRunsByIncident(map);
    }
    if (apuAsset) {
      // Window telemetry on SOURCE time (the replay cursor), not wall time,
      // scoped to THIS run's ingested rows and covering its full interval.
      const cursor = currentRun?.cursor ?? undefined;
      const spanHours =
        currentRun?.source_start && cursor
          ? Math.max(
              1,
              Math.ceil(
                (new Date(cursor).getTime() - new Date(currentRun.source_start).getTime()) /
                  3600_000,
              ) + 1,
            )
          : 6;
      const [s, p] = await Promise.all([
        api.readings(apuAsset.id, spanHours, cursor ?? undefined, currentRun?.id),
        api.predictions(apuAsset.id, 500, currentRun?.id),
      ]);
      setSeries(s.filter((x) => x.points.length > 0));
      setPreds(p);
    }
  }, []);

  useEffect(() => {
    refresh().catch(console.error);
    api.triggerPolicy().then(setPolicy).catch(console.error);
    api
      .flagshipEvaluation()
      .then(setEvaluation)
      .catch(() => setEvaluation(null));
    const t = setInterval(() => refresh().catch(console.error), 4000);
    return () => clearInterval(t);
  }, [refresh]);

  async function act(fn: () => Promise<unknown>) {
    setBusy(true);
    setNote(null);
    try {
      const result = (await fn()) as Record<string, unknown> | undefined;
      if (result && typeof result.inference_note === "string") setNote(result.inference_note);
      await refresh();
    } catch (e) {
      setNote(String(e));
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="space-y-5">
      <header>
        <h1 className="text-xl font-bold tracking-tight text-zinc-100">Replay Lab</h1>
        <p className="text-sm text-zinc-500">
          Real MetroPT-3 compressor history replayed into the live database, scored by the real
          trained model — in shadow mode.
        </p>
      </header>

      {/* Provenance banner — the distinctions must be unmistakable */}
      <div className="rounded-xl border border-risk-mid/40 bg-risk-mid/5 px-5 py-3.5 text-xs leading-relaxed">
        <span className="chip border bg-risk-mid/15 text-risk-mid border-risk-mid/40 font-semibold mr-2">
          EXPERIMENTAL — SHADOW MODE
        </span>
        <span className="text-zinc-300">
          Telemetry is <b>real public MetroPT-3 history</b> (UCI, CC BY 4.0). APU-01 is a{" "}
          <b>synthetic PulseOps facility representation</b> of that unit. Risk scores come from the{" "}
          <b>real trained anomaly detector</b>{" "}
          <code className="font-mono">metropt3_iforest_v1</code>, whose trigger policy is{" "}
          <b>shadow-only</b>: would-trigger alerts are recorded but{" "}
          <b>never open incidents automatically</b>. Promoted incidents are operator-created{" "}
          <b>synthetic/demo</b> objects. All other assets remain on the labeled{" "}
          <code className="font-mono">mock_demo_v0</code> adapter.
        </span>
      </div>

      <div className="grid grid-cols-2 gap-2 md:grid-cols-6">
        {[
          ["1", "Replay telemetry"],
          ["2", "Build 30m window"],
          ["3", "Score anomaly"],
          ["4", "Persist 3 crossings"],
          ["5", "Human promotes"],
          ["6", "Agent investigates"],
        ].map(([n, label]) => (
          <div key={n} className="rounded-lg border border-ink-700 bg-ink-800/50 px-3 py-2">
            <span className="mr-2 font-mono text-pulse-400">{n}</span>
            <span className="text-[11px] text-zinc-400">{label}</span>
          </div>
        ))}
      </div>

      {/* Replay control */}
      <Panel title="Replay run" right={run ? <StatusPill status={run.status} /> : undefined}>
        {!run ? (
          <div className="space-y-3">
            <EmptyState text="No replay run yet — pick a preset below." />
            <PresetRow
              busy={busy}
              onCreate={(h, s, start) => act(() => api.createReplay(h, s, start))}
            />
          </div>
        ) : (
          <div className="space-y-3">
            <div className="grid grid-cols-2 lg:grid-cols-4 gap-3 text-xs font-mono">
              <div className="rounded-lg border border-ink-700 bg-ink-800/60 p-3">
                <div className="text-zinc-500 uppercase tracking-wider text-[10px]">
                  Source interval (event time)
                </div>
                <div className="text-zinc-200 mt-1">{fmt(run.source_start)}</div>
                <div className="text-zinc-200">→ {fmt(run.source_end)}</div>
              </div>
              <div className="rounded-lg border border-ink-700 bg-ink-800/60 p-3">
                <div className="text-zinc-500 uppercase tracking-wider text-[10px]">
                  Simulation clock
                </div>
                <div className="text-pulse-400 mt-1 text-sm">{fmt(run.sim_time)}</div>
                <div className="text-zinc-500">cursor {fmt(run.cursor)}</div>
              </div>
              <div className="rounded-lg border border-ink-700 bg-ink-800/60 p-3">
                <div className="text-zinc-500 uppercase tracking-wider text-[10px]">
                  Ingestion (wall clock)
                </div>
                <div className="text-zinc-200 mt-1">{fmt(run.updated_at)}</div>
                <div className="text-zinc-500">speed {run.speed}× source/wall</div>
              </div>
              <div className="rounded-lg border border-ink-700 bg-ink-800/60 p-3">
                <div className="text-zinc-500 uppercase tracking-wider text-[10px]">
                  Model provenance
                </div>
                <div className="text-zinc-200 mt-1">{run.model_source || "—"}</div>
                <div className="text-zinc-500">{run.feature_version || "—"}</div>
              </div>
            </div>
            <div className="flex flex-wrap gap-2">
              {run.status !== "running" && run.status !== "completed" && (
                <button
                  onClick={() => act(() => api.startReplay(run.id))}
                  disabled={busy}
                  className="rounded-lg bg-risk-low/15 border border-risk-low/40 px-4 py-2 text-xs font-semibold text-risk-low disabled:opacity-50"
                >
                  ▶ {run.status === "paused" ? "Resume" : "Start"}
                </button>
              )}
              {run.status === "running" && (
                <>
                  <button
                    onClick={() => act(() => api.advanceReplay(run.id, 1))}
                    disabled={busy}
                    className="rounded-lg bg-pulse-500/15 border border-pulse-400/40 px-4 py-2 text-xs font-semibold text-pulse-400 disabled:opacity-50"
                  >
                    Advance {run.speed >= 3600 ? `${run.speed / 3600}h` : `${run.speed}s`} source
                    time
                  </button>
                  <button
                    onClick={() => act(() => api.pauseReplay(run.id))}
                    disabled={busy}
                    className="rounded-lg bg-risk-mid/10 border border-risk-mid/40 px-4 py-2 text-xs font-semibold text-risk-mid disabled:opacity-50"
                  >
                    ⏸ Pause
                  </button>
                </>
              )}
              <button
                onClick={() => act(() => api.resetReplay(run.id))}
                disabled={busy}
                className="rounded-lg bg-risk-high/10 border border-risk-high/40 px-4 py-2 text-xs font-semibold text-risk-high disabled:opacity-50"
              >
                Reset (deterministic)
              </button>
              {busy && <Spinner />}
            </div>
            {run.last_error && (
              <p className="text-xs text-risk-high font-mono">last error: {run.last_error}</p>
            )}
            {note && <p className="text-xs text-risk-mid">{note}</p>}
            <div className="grid gap-3 border-t border-ink-700 pt-3 md:grid-cols-3">
              <div className="rounded-lg border border-ink-700 bg-ink-800/50 p-3">
                <div className="panel-header">Latest anomaly score</div>
                <div className="mt-1 font-mono text-2xl font-bold text-zinc-100">
                  {latestPrediction ? latestPrediction.risk.toFixed(3) : "—"}
                </div>
                <div className="text-[10px] text-zinc-500">
                  threshold {latestPrediction ? latestPrediction.threshold.toFixed(3) : "—"}
                </div>
              </div>
              <div className="rounded-lg border border-ink-700 bg-ink-800/50 p-3">
                <div className="panel-header">Threshold persistence</div>
                <div className="mt-2 flex gap-1.5">
                  {Array.from({ length: policy?.persistence_windows ?? 3 }).map((_, i) => (
                    <span
                      key={i}
                      className={`h-2.5 flex-1 rounded-full ${i < thresholdStreak ? "bg-risk-high" : "bg-ink-700"}`}
                    />
                  ))}
                </div>
                <div className="mt-1 text-[10px] text-zinc-500">
                  {Math.min(thresholdStreak, policy?.persistence_windows ?? 3)}/
                  {policy?.persistence_windows ?? 3} consecutive crossings required
                </div>
              </div>
              <div className="rounded-lg border border-ink-700 bg-ink-800/50 p-3">
                <div className="panel-header">Human review queue</div>
                <div
                  className={`mt-1 font-mono text-2xl font-bold ${pendingAlerts.length ? "text-risk-mid" : "text-zinc-500"}`}
                >
                  {pendingAlerts.length}
                </div>
                <div className="text-[10px] text-zinc-500">
                  pending shadow alerts · no auto-incidents
                </div>
              </div>
            </div>
            <details className="pt-2 border-t border-ink-700">
              <summary className="cursor-pointer panel-header">
                Start another isolated replay
              </summary>
              <div className="mt-2">
                <PresetRow
                  busy={busy}
                  onCreate={(h, s, start) => act(() => api.createReplay(h, s, start))}
                />
              </div>
            </details>
          </div>
        )}
      </Panel>

      {/* Synchronized investigation timeline — real stored data only */}
      {run && (
        <Panel
          title={`Investigation timeline — real MetroPT telemetry + ${preds[0]?.model_source ?? "metropt3_iforest_v1"} (${preds[0]?.feature_version ?? "…"})`}
          right={
            <span className="chip border border-ink-600 bg-ink-800 font-mono text-[10px] text-zinc-500">
              replay #{run.id}
            </span>
          }
        >
          <div className="space-y-3">
            <InvestigationTimeline
              series={series}
              predictions={ascendingPreds}
              alerts={alerts}
              policy={policy}
              failureEvents={evaluation?.primary_policy.per_event ?? []}
              run={run}
              selectedId={selectedPrediction?.id ?? null}
              onSelect={setSelectedPrediction}
            />
            {selectedPrediction && (
              <PredictionDetailPanel
                prediction={selectedPrediction}
                onClose={() => setSelectedPrediction(null)}
              />
            )}
            {preds.length > 0 && (
              <details>
                <summary className="cursor-pointer panel-header">
                  Exact prediction rows ({preds.length})
                </summary>
                <div className="mt-2 max-h-64 space-y-1.5 overflow-y-auto">
                  {preds.map((p) => (
                    <button
                      key={p.id}
                      onClick={() => setSelectedPrediction(p)}
                      className={`flex w-full items-center gap-3 rounded-md border px-3 py-1.5 text-left text-xs ${
                        selectedPrediction?.id === p.id
                          ? "border-pulse-400/50 bg-pulse-500/10"
                          : "border-ink-700 bg-ink-800/50 hover:border-ink-600"
                      }`}
                    >
                      <span className="w-40 font-mono text-zinc-500">{fmt(p.window_end)}</span>
                      <div className="h-1.5 flex-1 overflow-hidden rounded bg-ink-700">
                        <div
                          className={p.alert ? "h-full bg-risk-high" : "h-full bg-pulse-400/70"}
                          style={{ width: `${Math.min(100, p.risk * 100)}%` }}
                        />
                      </div>
                      <span className="w-14 text-right font-mono text-zinc-300">
                        {p.risk.toFixed(3)}
                      </span>
                      <span className="chip w-16 justify-center border border-zinc-500/30 bg-zinc-500/10 text-zinc-400">
                        {p.operating_mode || "—"}
                      </span>
                      {p.alert && (
                        <span className="chip border border-risk-high/30 bg-risk-high/10 text-risk-high">
                          threshold crossed
                        </span>
                      )}
                    </button>
                  ))}
                </div>
                <p className="pt-1 text-[10px] text-zinc-600">
                  threshold {preds[0].threshold.toFixed(3)} · policy {preds[0].policy_mode} (
                  {preds[0].policy_version}) · deviations shown are descriptive, not causal
                </p>
              </details>
            )}
          </div>
        </Panel>
      )}

      {/* Trigger state machine — how a score becomes (only ever) a shadow alert */}
      {run && (
        <Panel title="Trigger state machine — score → persistence → shadow alert → human decision">
          <TriggerStateMachine predictions={ascendingPreds} alerts={alerts} policy={policy} />
        </Panel>
      )}

      {/* Shadow alerts + promotion */}
      <Panel
        title={`Shadow alerts (${pendingAlerts.length} pending · ${alerts.length} total) — human review required`}
      >
        {alerts.length === 0 ? (
          <EmptyState text="No shadow alerts. Replay an anomalous period to see would-trigger decisions." />
        ) : (
          <div className="space-y-2.5">
            {alerts.map((a) => (
              <div key={a.id} className="rounded-lg border border-ink-700 bg-ink-800/60 p-3.5">
                <div className="flex items-center justify-between gap-2">
                  <div className="text-xs font-semibold text-zinc-200">
                    Shadow alert #{a.id} · {a.model_source} · {a.consecutive_windows} consecutive
                    windows
                  </div>
                  <StatusPill
                    status={
                      a.status === "observed"
                        ? "proposed"
                        : a.status === "promoted"
                          ? "approved"
                          : "rejected"
                    }
                  />
                </div>
                <p className="mt-1.5 text-xs text-zinc-400">{a.trigger_reason}</p>
                <div className="mt-2 flex flex-wrap items-center gap-2">
                  {a.status === "observed" && (
                    <>
                      <button
                        onClick={() => act(() => api.promoteAndInvestigate(a.id, "operator"))}
                        disabled={busy}
                        className="rounded-lg bg-risk-low/15 border border-risk-low/40 px-3 py-1.5 text-xs font-semibold text-risk-low disabled:opacity-50"
                        title="Creates ONE synthetic/demo incident and ONE investigation, advanced through read-only Observe/Diagnose to the human-approval gate. Idempotent — retries never duplicate. Approval stays a separate decision."
                      >
                        Promote &amp; Investigate → synthetic/demo incident
                      </button>
                      <button
                        onClick={() => act(() => api.dismissShadowAlert(a.id, "operator"))}
                        disabled={busy}
                        className="rounded-lg bg-risk-high/10 border border-risk-high/40 px-3 py-1.5 text-xs font-semibold text-risk-high disabled:opacity-50"
                      >
                        Dismiss
                      </button>
                    </>
                  )}
                  {a.status === "promoted" && a.incident_id && apu && (
                    <span className="text-xs text-zinc-400">
                      Promoted by <b className="text-zinc-200">{a.decided_by}</b> at{" "}
                      {fmt(a.decided_at)} → synthetic incident #{a.incident_id} ·{" "}
                      {runsByIncident[a.incident_id] ? (
                        <Link
                          href={`/agent?run=${runsByIncident[a.incident_id]}`}
                          className="text-pulse-400 hover:underline font-semibold"
                        >
                          investigation run #{runsByIncident[a.incident_id]} →
                        </Link>
                      ) : (
                        <Link href="/agent" className="text-pulse-400 hover:underline">
                          control room
                        </Link>
                      )}{" "}
                      ·{" "}
                      <Link href={`/assets/${apu.id}`} className="text-pulse-400 hover:underline">
                        open asset
                      </Link>
                    </span>
                  )}
                  {a.status === "dismissed" && (
                    <span className="text-xs text-zinc-500">
                      Dismissed by {a.decided_by} at {fmt(a.decided_at)} — nothing was created.
                    </span>
                  )}
                </div>
              </div>
            ))}
          </div>
        )}
      </Panel>
    </div>
  );
}
