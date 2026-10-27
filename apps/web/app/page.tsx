"use client";

import Link from "next/link";
import { useEffect, useState } from "react";
import { api, DashboardOut, riskBg } from "@/lib/api";
import { EmptyState, Panel, RiskBadge, Spinner, StatusPill } from "@/components/ui";

export default function Dashboard() {
  const [data, setData] = useState<DashboardOut | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [resetting, setResetting] = useState(false);

  async function resetDemo() {
    if (
      !window.confirm(
        "Reset the demo? This deterministically wipes ALL demo state (replays, incidents, " +
          "investigations, work orders) and reseeds the synthetic facility. Nothing here is real data.",
      )
    )
      return;
    setResetting(true);
    try {
      await api.resetDemo("operator");
      window.location.reload();
    } catch (e) {
      setError(String(e));
    } finally {
      setResetting(false);
    }
  }

  useEffect(() => {
    let alive = true;
    const load = () =>
      api
        .dashboard()
        .then((d) => alive && (setData(d), setError(null)))
        .catch((e) => alive && setError(String(e)));
    load();
    const t = setInterval(load, 5000);
    return () => {
      alive = false;
      clearInterval(t);
    };
  }, []);

  // Only a failure with nothing to show yet replaces the page; later poll
  // failures keep the last good data and show a banner instead.
  if (error && !data)
    return (
      <div className="panel p-6 text-sm text-risk-high">
        API unreachable: {error}
        <div className="text-zinc-400 mt-2">
          Start the backend:{" "}
          <code className="font-mono text-zinc-300">
            cd apps/api && python -m uvicorn pulseops_api.main:app
          </code>
        </div>
      </div>
    );
  if (!data)
    return (
      <div className="flex items-center gap-3 text-zinc-400 text-sm py-20 justify-center">
        <Spinner /> Loading command center…
      </div>
    );

  const flagship = data.flagship_model;

  return (
    <div className="space-y-5">
      {error && (
        <p
          role="alert"
          className="rounded-md border border-risk-high/30 bg-risk-high/5 px-3 py-2 text-xs text-risk-high"
        >
          Lost contact with the API ({error}). Showing the last data received.
        </p>
      )}
      <header className="flex items-end justify-between gap-3">
        <div>
          <h1 className="text-xl font-bold tracking-tight text-zinc-100">Command Center</h1>
          <p className="text-sm text-zinc-500">
            Two clearly separated layers: a <b className="text-zinc-300">real ML demonstration</b>{" "}
            (UCI MetroPT-3 · <span className="font-mono">metropt3_iforest_v1</span>, shadow-only)
            and a <b className="text-zinc-300">synthetic facility operations demo</b> (
            <span className="font-mono">{data.risk_source}</span> heuristic).
          </p>
        </div>
        <button
          onClick={resetDemo}
          disabled={resetting}
          className="shrink-0 rounded-lg border border-risk-high/40 bg-risk-high/10 px-3 py-2 text-xs font-semibold text-risk-high hover:bg-risk-high/20 disabled:opacity-50"
          title="Deterministic destructive reset: drop, re-migrate, reseed the synthetic demo database"
        >
          {resetting ? "Resetting…" : "⟲ Reset demo"}
        </button>
      </header>

      {/* Guided walkthrough — a new viewer should know exactly what to click */}
      <div className="rounded-xl border border-pulse-400/25 bg-pulse-500/5 px-4 py-3">
        <div className="flex flex-wrap items-center gap-x-2 gap-y-1.5 text-[11px] text-zinc-400">
          <span className="font-semibold uppercase tracking-wider text-pulse-400">Guided demo</span>
          {[
            ["1", "Replay Lab → “June 5 air-leak” preset → Start + Advance", "/replay"],
            [
              "2",
              "Watch the real model cross its threshold 3× → one shadow alert, zero auto-incidents",
              "/replay",
            ],
            ["3", "Promote & Investigate (human decision)", "/replay"],
            ["4", "Control Room → review evidence + LLM/fallback status → Approve", "/agent"],
            [
              "5",
              "Verification decides the outcome; ML Lab shows why the model stays shadow-only",
              "/ml-lab",
            ],
          ].map(([n, label, href], i) => (
            <span key={n} className="flex items-center gap-2">
              {i > 0 && <span className="text-zinc-600">→</span>}
              <Link href={href} className="hover:text-pulse-400">
                <span className="mr-1 font-mono text-pulse-400">{n}.</span>
                {label}
              </Link>
            </span>
          ))}
        </div>
      </div>

      {/* KPI row */}
      <div className="grid grid-cols-2 lg:grid-cols-4 gap-4">
        <Panel title="Assets monitored">
          <div className="stat-value">{data.asset_count}</div>
        </Panel>
        <Panel title="Online / degraded">
          <div className="stat-value">
            <span className="text-risk-low">{data.online_count}</span>
            <span className="text-zinc-600 mx-1.5">/</span>
            <span className="text-risk-mid">{data.degraded_count}</span>
          </div>
        </Panel>
        <Panel title="Active incidents">
          <div className={`stat-value ${data.active_incidents.length ? "text-risk-high" : ""}`}>
            {data.active_incidents.length}
          </div>
        </Panel>
        <Panel title="Open work orders">
          <div className="stat-value">{data.open_work_orders}</div>
        </Panel>
      </div>

      {flagship && (
        <Panel
          title="Real ML Demonstration — UCI MetroPT-3 · metropt3_iforest_v1"
          right={
            <span className="chip border bg-risk-mid/10 text-risk-mid border-risk-mid/40 uppercase">
              {flagship.policy_mode} mode
            </span>
          }
        >
          <div className="grid gap-4 lg:grid-cols-[1.4fr_1fr_1fr] items-stretch">
            <div className="rounded-lg border border-ink-700 bg-ink-800/60 p-4">
              <div className="flex items-start justify-between gap-3">
                <div>
                  <div className="text-sm font-semibold text-zinc-100">
                    {flagship.asset_tag} · {flagship.asset_name}
                  </div>
                  <div className="mt-1 font-mono text-[11px] text-zinc-500">
                    {flagship.model_source} · {flagship.feature_version}
                  </div>
                </div>
                <span
                  className={`chip border ${
                    flagship.status === "review_required"
                      ? "bg-risk-high/10 text-risk-high border-risk-high/40"
                      : flagship.status === "building_persistence"
                        ? "bg-risk-mid/10 text-risk-mid border-risk-mid/40"
                        : "bg-pulse-500/10 text-pulse-400 border-pulse-400/30"
                  }`}
                >
                  {flagship.status.replaceAll("_", " ")}
                </span>
              </div>
              <p className="mt-3 text-xs leading-relaxed text-zinc-400">
                {flagship.risk == null || flagship.window_end == null
                  ? "No replay prediction yet. Run the June air-leak preset to exercise the real model."
                  : `Latest window ${new Date(flagship.window_end).toISOString().replace("T", " ").slice(0, 19)}Z · ${flagship.operating_mode || "unknown"} mode.`}
              </p>
              <p className="mt-2 text-[10px] text-zinc-600">{flagship.disclaimer}</p>
            </div>

            <div className="rounded-lg border border-ink-700 bg-ink-800/60 p-4">
              <div className="panel-header">Anomaly score</div>
              <div className="mt-2 flex items-end gap-2">
                <span
                  className={`font-mono text-3xl font-bold ${
                    flagship.threshold_crossed ? "text-risk-high" : "text-pulse-400"
                  }`}
                >
                  {flagship.risk == null ? "—" : flagship.risk.toFixed(3)}
                </span>
                <span className="pb-1 text-xs text-zinc-500">
                  threshold {flagship.threshold == null ? "—" : flagship.threshold.toFixed(3)}
                </span>
              </div>
              <div className="mt-3 h-2 overflow-hidden rounded-full bg-ink-700">
                <div
                  className={
                    flagship.threshold_crossed ? "h-full bg-risk-high" : "h-full bg-pulse-400"
                  }
                  style={{ width: `${Math.min(100, (flagship.risk ?? 0) * 100)}%` }}
                />
              </div>
            </div>

            <div className="rounded-lg border border-ink-700 bg-ink-800/60 p-4">
              <div className="panel-header">Trigger persistence</div>
              <div className="mt-3 flex gap-2">
                {Array.from({ length: flagship.persistence_required }).map((_, i) => (
                  <div
                    key={i}
                    className={`h-3 flex-1 rounded-full ${
                      i < flagship.persistence_windows ? "bg-risk-high" : "bg-ink-700"
                    }`}
                  />
                ))}
              </div>
              <div className="mt-2 text-xs text-zinc-400">
                {Math.min(flagship.persistence_windows, flagship.persistence_required)}/
                {flagship.persistence_required} consecutive threshold crossings
              </div>
              <Link
                href="/replay"
                className="mt-3 inline-block text-xs font-semibold text-pulse-400 hover:underline"
              >
                {flagship.pending_shadow_alerts > 0
                  ? `Review ${flagship.pending_shadow_alerts} pending shadow alert${flagship.pending_shadow_alerts === 1 ? "" : "s"} →`
                  : "Open Replay Lab →"}
              </Link>
            </div>
          </div>
        </Panel>
      )}

      <div className="grid lg:grid-cols-5 gap-4">
        {/* Risk feed */}
        <Panel
          title="Synthetic Facility Operations Demo — mock_demo_v0"
          className="lg:col-span-3"
          right={
            <span className="chip border border-zinc-500/30 bg-zinc-500/10 text-[10px] uppercase text-zinc-400">
              synthetic assets · heuristic scores
            </span>
          }
        >
          <div className="space-y-2">
            {data.top_risk_assets.map((a) => (
              <Link
                key={a.id}
                href={`/assets/${a.id}`}
                className="flex items-center gap-4 rounded-lg border border-ink-700 bg-ink-800/60 px-4 py-3 hover:border-pulse-400/40 transition-colors"
              >
                <div className="font-mono text-xs text-zinc-500 w-14">{a.tag}</div>
                <div className="flex-1 min-w-0">
                  <div className="text-sm font-medium text-zinc-200">{a.name}</div>
                  <div className="text-xs text-zinc-500 truncate">{a.location}</div>
                </div>
                <div className="w-32">
                  <div className="h-1.5 rounded-full bg-ink-700 overflow-hidden">
                    <div
                      className={`h-full ${riskBg(a.risk)}`}
                      style={{ width: `${Math.max(4, a.risk * 100)}%` }}
                    />
                  </div>
                </div>
                <RiskBadge risk={a.risk} />
                <StatusPill status={a.status} />
              </Link>
            ))}
            <p className="pt-1 text-[10px] text-zinc-600">
              These are <span className="font-mono">mock_demo_v0</span> heuristic facility-risk
              scores for synthetic HVAC assets — they are never output of the MetroPT model above.
              The real model scores only APU-01 during replay, in shadow mode.
            </p>
          </div>
        </Panel>

        {/* Incidents + runs */}
        <div className="lg:col-span-2 space-y-4">
          <Panel title="Active incidents">
            {data.active_incidents.length === 0 ? (
              <EmptyState text="No active incidents" />
            ) : (
              <div className="space-y-2">
                {data.active_incidents.map((i) => (
                  <Link
                    key={i.id}
                    href={`/assets/${i.asset_id}`}
                    className="block rounded-lg border border-risk-high/25 bg-risk-high/5 px-4 py-3 hover:border-risk-high/50 transition-colors"
                  >
                    <div className="flex items-center justify-between gap-2">
                      <span className="text-sm font-medium text-zinc-100">{i.title}</span>
                      <StatusPill status={i.status} />
                    </div>
                    <p className="mt-1 text-xs text-zinc-400 line-clamp-2">{i.summary}</p>
                  </Link>
                ))}
              </div>
            )}
          </Panel>

          <Panel
            title="Recent agent runs"
            right={
              <Link href="/agent" className="text-xs text-pulse-400 hover:underline">
                Control room →
              </Link>
            }
          >
            {data.recent_runs.length === 0 ? (
              <EmptyState text="No agent runs yet — open an asset and start an investigation" />
            ) : (
              <div className="space-y-2">
                {data.recent_runs.map((r) => (
                  <Link
                    key={r.id}
                    href={`/agent?run=${r.id}`}
                    className="flex items-center justify-between rounded-lg border border-ink-700 bg-ink-800/60 px-4 py-2.5 hover:border-pulse-400/40 transition-colors"
                  >
                    <div className="text-xs">
                      <span className="font-mono text-zinc-500">run #{r.id}</span>
                      <span className="ml-2 text-zinc-300">
                        phase: <span className="font-mono">{r.current_phase}</span>
                      </span>
                    </div>
                    <StatusPill status={r.status} />
                  </Link>
                ))}
              </div>
            )}
          </Panel>
        </div>
      </div>
    </div>
  );
}
