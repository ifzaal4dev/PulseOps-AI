"use client";

import Link from "next/link";
import { useParams, useRouter } from "next/navigation";
import { useEffect, useState } from "react";
import {
  api,
  AssetDetail,
  IncidentOut,
  InspectionOut,
  SensorSeries,
  WorkOrderOut,
} from "@/lib/api";
import { EmptyState, Panel, PriorityPill, RiskBadge, Spinner, StatusPill } from "@/components/ui";
import { Sparkline } from "@/components/Sparkline";

const SENSOR_LABELS: Record<string, string> = {
  vibration_rms: "Vibration RMS",
  discharge_pressure: "Discharge Pressure",
  supply_temp: "Supply Temperature",
  power_kw: "Power Draw",
  flow_rate: "Flow Rate",
  return_temp: "Return Temperature",
  humidity: "Humidity",
  fan_speed: "Fan Speed",
  steam_pressure: "Steam Pressure",
  stack_temp: "Stack Temperature",
};

export default function AssetPage() {
  const { id } = useParams<{ id: string }>();
  const assetId = Number(id);
  const router = useRouter();
  const [asset, setAsset] = useState<AssetDetail | null>(null);
  const [series, setSeries] = useState<SensorSeries[]>([]);
  const [incidents, setIncidents] = useState<IncidentOut[]>([]);
  const [inspections, setInspections] = useState<InspectionOut[]>([]);
  const [orders, setOrders] = useState<WorkOrderOut[]>([]);
  const [starting, setStarting] = useState(false);

  useEffect(() => {
    if (!assetId) return;
    api.asset(assetId).then(setAsset).catch(console.error);
    api.readings(assetId, 72).then(setSeries).catch(console.error);
    api.incidents(assetId).then(setIncidents).catch(console.error);
    api.inspections(assetId).then(setInspections).catch(console.error);
    api.workOrders(assetId).then(setOrders).catch(console.error);
  }, [assetId]);

  async function startInvestigation() {
    setStarting(true);
    try {
      const run = await api.startRun(assetId, `ui: investigate ${asset?.name ?? assetId}`);
      await api.advanceRun(run.id, "until_blocked");
      router.push(`/agent?run=${run.id}`);
    } finally {
      setStarting(false);
    }
  }

  if (!asset)
    return (
      <div className="flex items-center gap-3 text-zinc-400 text-sm py-20 justify-center">
        <Spinner /> Loading asset…
      </div>
    );

  const anomalous = (kind: string) => {
    const s = series.find((x) => x.sensor.kind === kind);
    if (!s || !s.points.length || !s.sensor.baseline_value) return false;
    const last = s.points[s.points.length - 1].value;
    return Math.abs(last / s.sensor.baseline_value - 1) > 0.15;
  };

  return (
    <div className="space-y-5">
      <div className="text-xs text-zinc-500">
        <Link href="/" className="hover:text-pulse-400">
          Command Center
        </Link>
        <span className="mx-2">/</span>
        <span className="text-zinc-300">{asset.name}</span>
      </div>

      <header className="flex flex-wrap items-center justify-between gap-4">
        <div className="flex items-center gap-4">
          <div>
            <h1 className="text-xl font-bold tracking-tight text-zinc-100 flex items-center gap-3">
              {asset.name}
              <span className="font-mono text-sm text-zinc-500">{asset.tag}</span>
            </h1>
            <p className="text-sm text-zinc-500">
              {asset.manufacturer} {asset.model} · installed {asset.install_date} · {asset.location}
            </p>
          </div>
          <StatusPill status={asset.status} />
          <RiskBadge risk={asset.risk} />
        </div>
        <button
          onClick={startInvestigation}
          disabled={starting}
          className="rounded-lg bg-pulse-500/15 border border-pulse-400/40 px-5 py-2.5 text-sm font-semibold text-pulse-400 hover:bg-pulse-500/25 transition-colors disabled:opacity-50"
        >
          {starting ? (
            <>
              Starting… <Spinner />
            </>
          ) : (
            "▶ Start Agent Investigation"
          )}
        </button>
      </header>

      {incidents
        .filter((i) => i.status !== "closed")
        .map((i) => (
          <div
            key={i.id}
            className={`rounded-xl border px-5 py-4 ${
              i.status === "mitigated"
                ? "border-risk-low/30 bg-risk-low/5"
                : "border-risk-high/30 bg-risk-high/5"
            }`}
          >
            <div className="flex items-center justify-between">
              <div className="text-sm font-semibold text-zinc-100">
                {i.severity === "critical" && i.status !== "mitigated" ? "🔴" : "🟢"} {i.title}
              </div>
              <StatusPill status={i.status} />
            </div>
            <p className="mt-1 text-xs text-zinc-400">{i.summary}</p>
          </div>
        ))}

      {/* Sensor grid */}
      <div className="grid md:grid-cols-2 gap-4">
        {series.map((s) => (
          <Panel
            key={s.sensor.id}
            title={`${SENSOR_LABELS[s.sensor.kind] ?? s.sensor.kind} (${s.sensor.unit})`}
            right={
              anomalous(s.sensor.kind) ? (
                <span className="chip border bg-risk-high/10 text-risk-high border-risk-high/30">
                  ANOMALY
                </span>
              ) : (
                <span className="chip border bg-risk-low/10 text-risk-low border-risk-low/30">
                  nominal
                </span>
              )
            }
          >
            <Sparkline
              points={s.points}
              baseline={s.sensor.baseline_value}
              unit={s.sensor.unit}
              danger={anomalous(s.sensor.kind)}
            />
            <div className="mt-1 text-[11px] text-zinc-500 font-mono">
              baseline {s.sensor.baseline_value} {s.sensor.unit} · last 72h
            </div>
          </Panel>
        ))}
      </div>

      <div className="grid lg:grid-cols-2 gap-4">
        <Panel title="Recent inspections">
          {inspections.length === 0 ? (
            <EmptyState text="No inspections" />
          ) : (
            <div className="space-y-3">
              {inspections.map((i) => (
                <div key={i.id} className="rounded-lg border border-ink-700 bg-ink-800/60 p-3">
                  <div className="flex justify-between text-xs">
                    <span className="font-medium text-zinc-200">{i.summary}</span>
                    <span className="font-mono text-zinc-500">
                      {new Date(i.ts).toLocaleDateString()}
                    </span>
                  </div>
                  <p className="mt-1 text-xs text-zinc-400">{i.notes}</p>
                </div>
              ))}
            </div>
          )}
        </Panel>

        <Panel title="Work orders">
          {orders.length === 0 ? (
            <EmptyState text="No work orders" />
          ) : (
            <div className="space-y-2">
              {orders.map((w) => (
                <div key={w.id} className="rounded-lg border border-ink-700 bg-ink-800/60 p-3">
                  <div className="flex items-center justify-between gap-2">
                    <span className="text-xs font-medium text-zinc-200">{w.title}</span>
                    <div className="flex gap-1.5">
                      {w.source === "agent" && (
                        <span className="chip border bg-pulse-500/10 text-pulse-400 border-pulse-400/30">
                          AGENT
                        </span>
                      )}
                      <PriorityPill priority={w.priority} />
                      <StatusPill status={w.status} />
                    </div>
                  </div>
                  {w.notes && <p className="mt-1 text-xs text-zinc-500">{w.notes}</p>}
                </div>
              ))}
            </div>
          )}
        </Panel>
      </div>
    </div>
  );
}
