"use client";

/**
 * Synchronized Replay Investigation Timeline.
 *
 * Aligned small multiples of real replayed compressor telemetry over a shared
 * source-time axis, with the anomaly-score track, frozen threshold, distinct
 * threshold-crossing markers, persistence progress, shadow-alert intervals,
 * documented failure intervals, and promotion markers. Rendering downsamples
 * for presentation only (min/max per bucket, so extremes survive); hover and
 * selection always resolve against the exact stored rows.
 */

import { useMemo, useState } from "react";
import {
  ModelPredictionOut,
  ReplayRunOut,
  SensorSeries,
  ShadowAlertOut,
  TriggerPolicyOut,
} from "@/lib/api";

export const fmtTs = (t: number | string | null | undefined) =>
  t == null ? "—" : new Date(t).toISOString().replace("T", " ").slice(0, 19) + "Z";

/** Trim float noise for display without losing real precision. */
const fmtNum = (v: number) => String(Number(v.toPrecision(6)));

/** ML report timestamps are naive UTC strings like "2020-06-05 10:00:00". */
export const parseReportTs = (s: string) =>
  new Date(s.includes("T") ? s : s.replace(" ", "T") + (s.endsWith("Z") ? "" : "Z")).getTime();

const W = 860;
const PAD_L = 50;
const PAD_R = 14;
const PLOT_W = W - PAD_L - PAD_R;
const SENSOR_H = 66;
const SCORE_H = 140;
const PERSIST_H = 20;

export interface FailureEvent {
  start: string;
  end: string;
  kind: string;
}

interface Interval {
  from: number;
  to: number;
  label?: string;
}

function clamp(v: number, lo: number, hi: number) {
  return Math.max(lo, Math.min(hi, v));
}

/** min/max-preserving downsample: at most 2 points per bucket, in time order. */
function downsample(points: { t: number; v: number }[], buckets = 240) {
  if (points.length <= buckets * 2) return points;
  const t0 = points[0].t;
  const span = points[points.length - 1].t - t0 || 1;
  const out: { t: number; v: number }[] = [];
  let bucket = -1;
  let lo: { t: number; v: number } | null = null;
  let hi: { t: number; v: number } | null = null;
  const flush = () => {
    if (!lo || !hi) return;
    if (lo === hi) out.push(lo);
    else out.push(...(lo.t <= hi.t ? [lo, hi] : [hi, lo]));
  };
  for (const p of points) {
    const b = Math.floor(((p.t - t0) / span) * buckets);
    if (b !== bucket) {
      flush();
      bucket = b;
      lo = hi = p;
    } else {
      if (p.v < lo!.v) lo = p;
      if (p.v > hi!.v) hi = p;
    }
  }
  flush();
  return out;
}

function nearest(points: { t: number; v: number }[], t: number) {
  if (points.length === 0) return null;
  let a = 0;
  let b = points.length - 1;
  while (b - a > 1) {
    const m = (a + b) >> 1;
    if (points[m].t <= t) a = m;
    else b = m;
  }
  return Math.abs(points[a].t - t) <= Math.abs(points[b].t - t) ? points[a] : points[b];
}

/** Merge consecutive predictions with the same operating mode into bands. */
function modeBands(preds: ModelPredictionOut[]): (Interval & { mode: string })[] {
  const bands: (Interval & { mode: string })[] = [];
  for (const p of preds) {
    const to = new Date(p.window_end).getTime();
    const from = p.window_start ? new Date(p.window_start).getTime() : to - 10 * 60 * 1000;
    const last = bands[bands.length - 1];
    if (last && last.mode === p.operating_mode && from <= last.to + 1) last.to = to;
    else bands.push({ from, to, mode: p.operating_mode });
  }
  return bands;
}

export function InvestigationTimeline({
  series,
  predictions,
  alerts,
  policy,
  failureEvents,
  run,
  selectedId,
  onSelect,
}: {
  series: SensorSeries[];
  predictions: ModelPredictionOut[]; // ascending by window_end
  alerts: ShadowAlertOut[];
  policy: TriggerPolicyOut | null;
  failureEvents: FailureEvent[];
  run: ReplayRunOut;
  selectedId: number | null;
  onSelect: (p: ModelPredictionOut | null) => void;
}) {
  const [hoverT, setHoverT] = useState<number | null>(null);

  const sensorData = useMemo(
    () =>
      series
        .filter((s) => s.points.length > 1)
        .map((s) => ({
          sensor: s.sensor,
          raw: s.points.map((p) => ({ t: new Date(p.ts).getTime(), v: p.value })),
        })),
    [series],
  );

  const predPoints = useMemo(
    () => predictions.map((p) => ({ t: new Date(p.window_end).getTime(), v: p.risk, p })),
    [predictions],
  );

  const domain = useMemo(() => {
    const ts: number[] = [];
    for (const s of sensorData) {
      ts.push(s.raw[0].t, s.raw[s.raw.length - 1].t);
    }
    if (predPoints.length) ts.push(predPoints[0].t, predPoints[predPoints.length - 1].t);
    if (ts.length === 0) return null;
    const lo = Math.min(...ts);
    const hi = Math.max(...ts);
    return { lo, hi: hi > lo ? hi : lo + 1 };
  }, [sensorData, predPoints]);

  const required = policy?.persistence_windows ?? 3;

  const streaks = useMemo(() => {
    // consecutive threshold crossings ending at each prediction
    const out: number[] = [];
    let n = 0;
    for (const { p } of predPoints) {
      n = p.alert ? n + 1 : 0;
      out.push(n);
    }
    return out;
  }, [predPoints]);

  if (!domain || (sensorData.length === 0 && predPoints.length === 0)) {
    return (
      <div className="rounded-lg border border-ink-700 bg-ink-900/60 p-6 text-center text-xs text-zinc-500">
        No replayed telemetry or predictions for this run yet — start and advance the replay.
      </div>
    );
  }

  const x = (t: number) => PAD_L + ((t - domain.lo) / (domain.hi - domain.lo)) * PLOT_W;
  const bands = modeBands(predictions);

  const alertIntervals: (Interval & { alert: ShadowAlertOut })[] = alerts.flatMap((a) => {
    const first = predictions.find((p) => p.id === a.first_prediction_id);
    const last = predictions.find((p) => p.id === a.latest_prediction_id);
    if (!first || !last) return [];
    return [
      {
        from: new Date(first.window_end).getTime(),
        to: new Date(last.window_end).getTime(),
        alert: a,
      },
    ];
  });

  const failureIntervals: (Interval & { kind: string })[] = failureEvents
    .map((e) => ({
      from: parseReportTs(e.start),
      to: parseReportTs(e.end),
      kind: e.kind,
    }))
    .filter((e) => e.to >= domain.lo && e.from <= domain.hi)
    .map((e) => ({ ...e, from: Math.max(e.from, domain.lo), to: Math.min(e.to, domain.hi) }));

  function pointerTime(evt: React.MouseEvent<SVGSVGElement>): number | null {
    const rect = (evt.target as SVGElement).closest("svg")?.getBoundingClientRect();
    if (!rect) return null;
    const px = ((evt.clientX - rect.left) / rect.width) * W;
    if (px < PAD_L || px > W - PAD_R) return null;
    return domain!.lo + ((px - PAD_L) / PLOT_W) * (domain!.hi - domain!.lo);
  }

  const handleMove = (evt: React.MouseEvent<SVGSVGElement>) => setHoverT(pointerTime(evt));
  const handleLeave = () => setHoverT(null);
  const handleClick = (evt: React.MouseEvent<SVGSVGElement>) => {
    const t = pointerTime(evt);
    if (t == null || predPoints.length === 0) return;
    const hit = nearest(
      predPoints.map(({ t: tt, v }) => ({ t: tt, v })),
      t,
    );
    if (!hit) return;
    const match = predPoints.find((pp) => pp.t === hit.t);
    onSelect(match && match.p.id === selectedId ? null : match ? match.p : null);
  };

  const hoverX = hoverT != null ? x(hoverT) : null;
  const hoverPred =
    hoverT != null && predPoints.length
      ? predPoints[
          predPoints.reduce(
            (best, pp, i) =>
              Math.abs(pp.t - hoverT) < Math.abs(predPoints[best].t - hoverT) ? i : best,
            0,
          )
        ]
      : null;

  const ModeBands = ({ h }: { h: number }) => (
    <>
      {bands.map((b, i) =>
        b.mode === "loaded" ? (
          <rect
            key={i}
            x={x(b.from)}
            y={0}
            width={Math.max(1, x(b.to) - x(b.from))}
            height={h}
            fill="#60a5fa"
            opacity={0.07}
          />
        ) : null,
      )}
    </>
  );

  const HoverLine = ({ h }: { h: number }) =>
    hoverX != null ? (
      <line
        x1={hoverX}
        x2={hoverX}
        y1={0}
        y2={h}
        stroke="#a1a1aa"
        strokeWidth={1}
        strokeDasharray="2 3"
      />
    ) : null;

  const shared = {
    onMouseMove: handleMove,
    onMouseLeave: handleLeave,
    onClick: handleClick,
  };

  return (
    <div className="space-y-1">
      {/* shared SVG pattern definitions (referenced by every row below) */}
      <svg width="0" height="0" className="absolute" aria-hidden="true">
        <defs>
          <pattern
            id="hatch-alert"
            width="6"
            height="6"
            patternUnits="userSpaceOnUse"
            patternTransform="rotate(45)"
          >
            <rect width="6" height="6" fill="#f59e0b" opacity="0.10" />
            <line x1="0" y1="0" x2="0" y2="6" stroke="#f59e0b" strokeWidth="1.5" opacity="0.35" />
          </pattern>
          <pattern
            id="hatch-failure"
            width="6"
            height="6"
            patternUnits="userSpaceOnUse"
            patternTransform="rotate(135)"
          >
            <rect width="6" height="6" fill="#fb7185" opacity="0.07" />
            <line x1="0" y1="0" x2="0" y2="6" stroke="#fb7185" strokeWidth="1.5" opacity="0.30" />
          </pattern>
        </defs>
      </svg>
      {/* legend — shapes and line styles, not color alone */}
      <div className="flex flex-wrap gap-x-4 gap-y-1 px-1 text-[10px] text-zinc-500">
        <span>
          <span className="mr-1 inline-block h-2 w-3 bg-[#60a5fa]/20 align-middle" />
          loaded mode (idle = plain)
        </span>
        <span>
          <span className="mr-1 align-middle font-mono text-[#f59e0b]">– –</span>frozen threshold
        </span>
        <span>
          <span className="mr-1 align-middle font-mono text-[#f59e0b]">· ·</span>hysteresis clear (
          {policy ? `${policy.hysteresis_low_factor}×` : "0.8×"})
        </span>
        <span>
          <span className="mr-1 align-middle text-[#fb7185]">▲</span>threshold crossing
        </span>
        <span>
          <span className="mr-1 inline-block h-2 w-3 border border-[#f59e0b]/60 bg-[repeating-linear-gradient(45deg,transparent,transparent_2px,#f59e0b40_2px,#f59e0b40_4px)] align-middle" />
          shadow-alert episode
        </span>
        <span>
          <span className="mr-1 inline-block h-2 w-3 border border-[#fb7185]/60 bg-[repeating-linear-gradient(135deg,transparent,transparent_2px,#fb718540_2px,#fb718540_4px)] align-middle" />
          documented failure interval
        </span>
        <span>
          <span className="mr-1 align-middle text-[#c084fc]">◆</span>promoted → investigation
        </span>
      </div>

      {/* sensor small multiples */}
      {sensorData.map(({ sensor, raw }) => {
        const pts = downsample(raw);
        const vmin = Math.min(...raw.map((p) => p.v));
        const vmax = Math.max(...raw.map((p) => p.v));
        const span = vmax - vmin || 1;
        // The top 16px are reserved for the labels so the line never covers them.
        const y = (v: number) => 18 + (1 - (v - vmin) / span) * (SENSOR_H - 24);
        const hov = hoverT != null ? nearest(raw, hoverT) : null;
        return (
          <div key={sensor.id} className="relative">
            <svg
              viewBox={`0 0 ${W} ${SENSOR_H}`}
              className="w-full cursor-crosshair"
              role="img"
              aria-label={`${sensor.kind} telemetry (${sensor.unit})`}
              {...shared}
            >
              <ModeBands h={SENSOR_H} />
              {failureIntervals.map((f, i) => (
                <rect
                  key={`f${i}`}
                  x={x(f.from)}
                  y={0}
                  width={Math.max(1, x(f.to) - x(f.from))}
                  height={SENSOR_H}
                  fill="url(#hatch-failure)"
                  opacity={0.25}
                />
              ))}
              <polyline
                points={pts.map((p) => `${x(p.t)},${y(p.v)}`).join(" ")}
                fill="none"
                stroke="#7dd3fc"
                strokeWidth={1.4}
              />
              {hov && <circle cx={x(hov.t)} cy={y(hov.v)} r={3} fill="#7dd3fc" stroke="#0c0d10" />}
              <HoverLine h={SENSOR_H} />
              <text x={4} y={12} fill="#71717a" fontSize={10} fontFamily="monospace">
                {sensor.kind} ({sensor.unit || "–"})
              </text>
              <text
                x={W - PAD_R}
                y={12}
                textAnchor="end"
                fill="#52525b"
                fontSize={9}
                fontFamily="monospace"
              >
                {hov
                  ? `${fmtNum(hov.v)} ${sensor.unit} @ ${fmtTs(hov.t)}`
                  : `${fmtNum(vmin)} … ${fmtNum(vmax)} ${sensor.unit}`}
              </text>
            </svg>
          </div>
        );
      })}

      {/* anomaly-score track */}
      <svg
        viewBox={`0 0 ${W} ${SCORE_H}`}
        className="w-full cursor-crosshair"
        role="img"
        aria-label="Anomaly score with frozen threshold, shadow-alert and failure intervals"
        {...shared}
      >
        {(() => {
          const y = (v: number) => 14 + (1 - clamp(v, 0, 1)) * (SCORE_H - 34);
          const thr = predictions[0]?.threshold;
          const low = thr != null && policy ? thr * policy.hysteresis_low_factor : null;
          return (
            <>
              <ModeBands h={SCORE_H} />
              {failureIntervals.map((f, i) => (
                <g key={`f${i}`}>
                  <rect
                    x={x(f.from)}
                    y={0}
                    width={Math.max(1, x(f.to) - x(f.from))}
                    height={SCORE_H}
                    fill="url(#hatch-failure)"
                  />
                  <text
                    x={x(f.from) + 3}
                    y={SCORE_H - 6}
                    fill="#fb7185"
                    fontSize={9}
                    fontFamily="monospace"
                  >
                    documented {f.kind} (failure report)
                  </text>
                </g>
              ))}
              {alertIntervals.map((a, i) => (
                <g key={`a${i}`}>
                  <rect
                    x={x(a.from)}
                    y={0}
                    width={Math.max(2, x(a.to) - x(a.from))}
                    height={SCORE_H}
                    fill="url(#hatch-alert)"
                  />
                  <text x={x(a.from) + 3} y={12} fill="#f59e0b" fontSize={9} fontFamily="monospace">
                    shadow alert #{a.alert.id} ({a.alert.status})
                  </text>
                  {a.alert.status === "promoted" && (
                    <g>
                      <line
                        x1={x(a.to)}
                        x2={x(a.to)}
                        y1={0}
                        y2={SCORE_H}
                        stroke="#c084fc"
                        strokeWidth={1.5}
                      />
                      <text
                        x={x(a.to) > W * 0.75 ? x(a.to) - 4 : x(a.to) + 4}
                        y={26}
                        textAnchor={x(a.to) > W * 0.75 ? "end" : "start"}
                        fill="#c084fc"
                        fontSize={9}
                        fontFamily="monospace"
                      >
                        ◆ promoted → incident #{a.alert.incident_id ?? "?"}
                      </text>
                    </g>
                  )}
                </g>
              ))}
              {[0.25, 0.5, 0.75].map((v) => (
                <g key={v}>
                  <line x1={PAD_L} x2={W - PAD_R} y1={y(v)} y2={y(v)} stroke="#27272a" />
                  <text
                    x={PAD_L - 6}
                    y={y(v) + 3}
                    textAnchor="end"
                    fill="#52525b"
                    fontSize={9}
                    fontFamily="monospace"
                  >
                    {v.toFixed(2)}
                  </text>
                </g>
              ))}
              {thr != null && (
                <>
                  <line
                    x1={PAD_L}
                    x2={W - PAD_R}
                    y1={y(thr)}
                    y2={y(thr)}
                    stroke="#f59e0b"
                    strokeWidth={1.5}
                    strokeDasharray="7 5"
                  />
                  <text
                    x={W - PAD_R}
                    y={y(thr) - 4}
                    textAnchor="end"
                    fill="#f59e0b"
                    fontSize={10}
                    fontFamily="monospace"
                  >
                    frozen threshold {thr.toFixed(3)}
                  </text>
                </>
              )}
              {low != null && (
                <>
                  <line
                    x1={PAD_L}
                    x2={W - PAD_R}
                    y1={y(low)}
                    y2={y(low)}
                    stroke="#f59e0b"
                    strokeWidth={1}
                    strokeDasharray="2 4"
                    opacity={0.7}
                  />
                  <text
                    x={W - PAD_R}
                    y={y(low) + 11}
                    textAnchor="end"
                    fill="#f59e0b"
                    fontSize={9}
                    opacity={0.8}
                    fontFamily="monospace"
                  >
                    hysteresis clear {low.toFixed(3)}
                  </text>
                </>
              )}
              <polyline
                points={predPoints.map((pp) => `${x(pp.t)},${y(pp.v)}`).join(" ")}
                fill="none"
                stroke="#2dd4bf"
                strokeWidth={2}
              />
              {predPoints.map((pp) =>
                pp.p.alert ? (
                  <path
                    key={pp.p.id}
                    d={`M ${x(pp.t)} ${y(pp.v) - 5} L ${x(pp.t) - 4.5} ${y(pp.v) + 3.5} L ${x(pp.t) + 4.5} ${y(pp.v) + 3.5} Z`}
                    fill="#fb7185"
                    stroke="#0c0d10"
                    strokeWidth={0.5}
                  />
                ) : (
                  <circle key={pp.p.id} cx={x(pp.t)} cy={y(pp.v)} r={2} fill="#2dd4bf" />
                ),
              )}
              {selectedId != null &&
                (() => {
                  const sel = predPoints.find((pp) => pp.p.id === selectedId);
                  return sel ? (
                    <circle
                      cx={x(sel.t)}
                      cy={y(sel.v)}
                      r={7}
                      fill="none"
                      stroke="#e4e4e7"
                      strokeWidth={1.5}
                      strokeDasharray="3 2"
                    />
                  ) : null;
                })()}
              <HoverLine h={SCORE_H} />
              <text x={4} y={12} fill="#71717a" fontSize={10} fontFamily="monospace">
                anomaly score (0–1, NOT a failure probability)
              </text>
            </>
          );
        })()}
      </svg>

      {/* persistence progress strip */}
      <svg
        viewBox={`0 0 ${W} ${PERSIST_H}`}
        className="w-full"
        role="img"
        aria-label={`Persistence progress toward ${required} consecutive crossings`}
      >
        {predPoints.map((pp, i) => {
          const streak = Math.min(streaks[i], required);
          return (
            <rect
              key={pp.p.id}
              x={x(pp.t) - 2}
              y={PERSIST_H - 4 - streak * 4}
              width={4}
              height={Math.max(2, streak * 4)}
              fill={streaks[i] >= required ? "#fb7185" : streak > 0 ? "#f59e0b" : "#3f3f46"}
            />
          );
        })}
        <text x={4} y={12} fill="#71717a" fontSize={9} fontFamily="monospace">
          persistence (bar height = consecutive crossings, {required} required)
        </text>
      </svg>

      {/* shared axis + hover readout */}
      <div className="flex items-center justify-between px-1 font-mono text-[10px] text-zinc-600">
        <span>{fmtTs(domain.lo)}</span>
        <span className="text-zinc-500">
          {hoverT != null && hoverPred
            ? `hover ${fmtTs(hoverT)} · nearest window ${fmtTs(hoverPred.t)} · score ${hoverPred.v.toFixed(3)} — click to inspect`
            : "source/event time (UTC) · hover for exact values · click a window to inspect"}
        </span>
        <span>{fmtTs(domain.hi)}</span>
      </div>
      <div className="px-1 font-mono text-[10px] text-zinc-600">
        replay #{run.id} · {predictions[0]?.model_source ?? run.model_source} ·{" "}
        {predictions[0]?.feature_version ?? run.feature_version} · policy{" "}
        {predictions[0]?.policy_mode ?? "shadow"} ({predictions[0]?.policy_version ?? "—"}) ·{" "}
        {predictions.length} windows · rendering is min/max-downsampled, values shown are exact
        stored rows
      </div>
    </div>
  );
}

export function PredictionDetailPanel({
  prediction,
  onClose,
}: {
  prediction: ModelPredictionOut;
  onClose: () => void;
}) {
  const top = prediction.explanation?.top ?? [];
  const maxZ = Math.max(1e-9, ...top.map((t) => t.abs_z));
  return (
    <div className="rounded-lg border border-pulse-400/30 bg-ink-900/70 p-4 text-xs">
      <div className="flex items-start justify-between gap-2">
        <div className="font-semibold text-zinc-200">
          Prediction #{prediction.id} — window ending {fmtTs(prediction.window_end)}
        </div>
        <button onClick={onClose} className="text-zinc-500 hover:text-zinc-300">
          ✕ close
        </button>
      </div>
      <div className="mt-2 grid gap-x-6 gap-y-1 sm:grid-cols-2 text-zinc-400">
        <span>
          score:{" "}
          <b className={`font-mono ${prediction.alert ? "text-risk-high" : "text-pulse-400"}`}>
            {prediction.risk.toFixed(4)}
          </b>{" "}
          vs threshold <b className="font-mono text-zinc-200">{prediction.threshold.toFixed(3)}</b>{" "}
          ({prediction.alert ? "crossed" : "not crossed"})
        </span>
        <span>
          operating mode: <b className="text-zinc-200">{prediction.operating_mode || "unknown"}</b>
        </span>
        <span>
          feature window:{" "}
          <b className="font-mono text-zinc-200">
            {fmtTs(prediction.window_start)} → {fmtTs(prediction.window_end)}
          </b>
        </span>
        <span>
          provenance:{" "}
          <b className="font-mono text-zinc-200">
            replay #{prediction.replay_run_id ?? "—"} · prediction #{prediction.id} ·{" "}
            {prediction.model_source} · {prediction.feature_version}
          </b>
        </span>
      </div>
      {top.length > 0 && (
        <div className="mt-3">
          <div className="panel-header mb-2">Top standardized feature deviations (|z|)</div>
          <div className="space-y-1.5">
            {top.map((t) => (
              <div key={t.feature} className="grid grid-cols-[9rem_1fr_auto] items-center gap-2">
                <span className="truncate font-mono text-[10px] text-zinc-400">{t.feature}</span>
                <div className="h-3 rounded bg-ink-700">
                  <div
                    className="h-full rounded bg-pulse-400/70"
                    style={{ width: `${(t.abs_z / maxZ) * 100}%` }}
                  />
                </div>
                <span className="font-mono text-[10px] text-zinc-300">
                  {t.abs_z.toFixed(2)}|z|
                  {t.observed != null && t.healthy_mean != null && (
                    <span className="text-zinc-500">
                      {" "}
                      · {t.observed} vs healthy {t.healthy_mean}
                      {t.healthy_std != null ? ` ± ${t.healthy_std}` : ""}
                    </span>
                  )}
                </span>
              </div>
            ))}
          </div>
        </div>
      )}
      <p className="mt-3 rounded-md border border-risk-mid/30 bg-risk-mid/5 px-2.5 py-1.5 text-[10px] text-risk-mid">
        ⚠ These are descriptive deviations from the healthy-training scaler — NOT causal feature
        importance. They say which inputs looked unusual, not what caused the anomaly.
      </p>
    </div>
  );
}

const MACHINE_STATES = [
  "normal",
  "threshold crossed",
  "persistence",
  "shadow alert",
  "human review",
  "incident / investigation",
] as const;

export function TriggerStateMachine({
  predictions,
  alerts,
  policy,
}: {
  predictions: ModelPredictionOut[]; // ascending
  alerts: ShadowAlertOut[];
  policy: TriggerPolicyOut | null;
}) {
  const required = policy?.persistence_windows ?? 3;
  let streak = 0;
  for (let i = predictions.length - 1; i >= 0; i--) {
    if (!predictions[i].alert) break;
    streak++;
  }
  const promoted = alerts.find((a) => a.status === "promoted");
  const pending = alerts.find((a) => a.status === "observed");
  const active = alerts.find((a) => a.hysteresis_state === "active");
  const latest = predictions[predictions.length - 1];

  const stateIndex = promoted
    ? 5
    : pending
      ? 4
      : active
        ? 3
        : streak >= required
          ? 3
          : streak > 0
            ? 2
            : latest?.alert
              ? 1
              : 0;

  const firstCrossing = (() => {
    const idx = predictions.length - streak;
    return streak > 0 ? predictions[idx]?.window_end : ((pending ?? promoted)?.episode_key ?? null);
  })();
  const anyAlert = pending ?? promoted ?? alerts[0];

  return (
    <div className="space-y-3">
      <div className="flex flex-wrap items-center gap-1.5">
        {MACHINE_STATES.map((label, i) => {
          const stateLabel =
            label === "persistence"
              ? `persistence ${Math.min(streak, required)}/${required}`
              : label === "human review"
                ? "human promote / dismiss"
                : label;
          return (
            <div key={label} className="flex items-center gap-1.5">
              <span
                className={`chip border px-2.5 py-1 text-[10px] ${
                  i === stateIndex
                    ? "border-pulse-400/60 bg-pulse-500/15 font-semibold text-pulse-400"
                    : i < stateIndex
                      ? "border-ink-600 bg-ink-800 text-zinc-400"
                      : "border-ink-700 bg-ink-900 text-zinc-600"
                }`}
              >
                {i < stateIndex ? "✓ " : ""}
                {stateLabel}
              </span>
              {i < MACHINE_STATES.length - 1 && <span className="text-zinc-600">→</span>}
            </div>
          );
        })}
      </div>
      <div className="grid gap-x-6 gap-y-1 text-[11px] text-zinc-400 sm:grid-cols-2 lg:grid-cols-3">
        <span>
          high threshold:{" "}
          <b className="font-mono text-zinc-200">{latest ? latest.threshold.toFixed(3) : "—"}</b>
        </span>
        <span>
          hysteresis clearing threshold:{" "}
          <b className="font-mono text-zinc-200">
            {latest && policy ? (latest.threshold * policy.hysteresis_low_factor).toFixed(3) : "—"}
            {policy ? ` (${policy.hysteresis_low_factor}× high)` : ""}
          </b>
        </span>
        <span>
          cooldown after clearing:{" "}
          <b className="font-mono text-zinc-200">
            {policy ? `${policy.cooldown_minutes} min source time` : "—"}
          </b>
        </span>
        <span>
          first crossing of current streak:{" "}
          <b className="font-mono text-zinc-200">{firstCrossing ? fmtTs(firstCrossing) : "—"}</b>
        </span>
        <span>
          latest window:{" "}
          <b className="font-mono text-zinc-200">{latest ? fmtTs(latest.window_end) : "—"}</b>
        </span>
        <span>
          episode:{" "}
          <b className="font-mono text-zinc-200">
            {anyAlert
              ? `#${anyAlert.id} ${anyAlert.hysteresis_state} (${anyAlert.status})`
              : "none"}
          </b>
          {anyAlert?.cooldown_until ? (
            <span className="text-zinc-500">
              {" "}
              · cooldown until {fmtTs(anyAlert.cooldown_until)}
            </span>
          ) : null}
        </span>
      </div>
      <p className="rounded-md border border-risk-mid/30 bg-risk-mid/5 px-3 py-2 text-[11px] leading-relaxed text-zinc-400">
        {policy?.explanation ??
          "Shadow means the model would have triggered an incident, but automatic triggering is disabled because its evaluation did not meet the readiness bar."}
      </p>
    </div>
  );
}
