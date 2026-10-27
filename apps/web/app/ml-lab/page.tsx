"use client";

import { useEffect, useState } from "react";
import { api, CaseMemoryOut, FlagshipEvaluationOut } from "@/lib/api";
import { EmptyState, Panel } from "@/components/ui";
import { fmtTs, parseReportTs } from "@/components/InvestigationTimeline";

/** Honest chronological view of the WHOLE holdout interval: every documented
 * failure event (detected or missed, with first-detection markers and
 * post-onset delay), every false-alarm episode outside event intervals, and
 * the frozen threshold + provenance. */
function HoldoutChronology({ evaluation }: { evaluation: FlagshipEvaluationOut }) {
  const W = 860;
  const H = 210;
  const PAD_L = 12;
  const PAD_R = 12;
  const AXIS_Y = 140;
  const BAND_TOP = 50; // event rectangles start here; labels sit above
  const t0 = parseReportTs(evaluation.evaluated_start_exclusive ?? "");
  const t1 = parseReportTs(evaluation.evaluated_end ?? "");
  if (!isFinite(t0) || !isFinite(t1) || t1 <= t0) return null;
  const x = (t: number) => PAD_L + ((t - t0) / (t1 - t0)) * (W - PAD_L - PAD_R);

  const months: number[] = [];
  const cursor = new Date(t0);
  cursor.setUTCDate(1);
  cursor.setUTCHours(0, 0, 0, 0);
  cursor.setUTCMonth(cursor.getUTCMonth() + 1);
  while (cursor.getTime() < t1) {
    months.push(cursor.getTime());
    cursor.setUTCMonth(cursor.getUTCMonth() + 1);
  }
  // Events close together in time would print their labels on top of each
  // other, so each label takes the first row where it fits.
  const CHAR_W = 5.6; // monospace, font size 9
  const rowEnds: number[] = [];
  const eventRows = evaluation.primary_policy.per_event.map((e) => {
    const from = x(parseReportTs(e.start));
    const label = `${e.detected ? "✓" : "✗ missed"} ${e.kind} ${e.start.slice(5, 10)}`;
    const alertLabel =
      e.detected && e.first_alert
        ? `first alert ${Math.abs(e.lead_time_hours ?? 0).toFixed(1)}h after onset`
        : "";
    const width = Math.max(label.length, alertLabel.length) * CHAR_W + 10;
    let row = rowEnds.findIndex((end) => end < from);
    if (row === -1) row = rowEnds.length;
    rowEnds[row] = from + width;
    return row;
  });

  const maxEpisodeWindows = Math.max(
    1,
    ...evaluation.false_alarm_episode_intervals.map((e) => e.n_alert_windows),
  );

  return (
    <div className="rounded-lg border border-ink-700 bg-ink-800/50 p-4">
      <div className="panel-header mb-2">
        Chronological holdout evaluation — all {evaluation.events_total} documented events and{" "}
        {evaluation.primary_policy.false_alarm_episodes} false-alarm episodes
      </div>
      <div className="overflow-x-auto">
        <svg
          viewBox={`0 0 ${W} ${H}`}
          className="w-full min-w-[640px]"
          role="img"
          aria-label="Chronological holdout evaluation timeline"
        >
          {months.map((m) => {
            const nearEdge = x(m) > W - PAD_R - 50;
            return (
              <g key={m}>
                <line x1={x(m)} x2={x(m)} y1={16} y2={AXIS_Y + 34} stroke="#27272a" />
                <text
                  x={nearEdge ? x(m) - 3 : x(m) + 3}
                  y={AXIS_Y + 44}
                  textAnchor={nearEdge ? "end" : "start"}
                  fill="#52525b"
                  fontSize={9}
                  fontFamily="monospace"
                >
                  {new Date(m).toISOString().slice(0, 7)}
                </text>
              </g>
            );
          })}
          <line x1={PAD_L} x2={W - PAD_R} y1={AXIS_Y} y2={AXIS_Y} stroke="#3f3f46" />

          {/* documented failure events */}
          {evaluation.primary_policy.per_event.map((e, i) => {
            const from = x(parseReportTs(e.start));
            const to = Math.max(from + 3, x(parseReportTs(e.end)));
            const labelY = 24 + eventRows[i] * 11;
            const alertY = BAND_TOP + 14 + eventRows[i] * 11;
            return (
              <g key={e.start}>
                <rect
                  x={from}
                  y={BAND_TOP}
                  width={to - from}
                  height={AXIS_Y - BAND_TOP}
                  fill={e.detected ? "#2dd4bf" : "#fb7185"}
                  opacity={0.16}
                  stroke={e.detected ? "#2dd4bf" : "#fb7185"}
                  strokeWidth={1}
                />
                <text
                  x={from}
                  y={labelY}
                  fill={e.detected ? "#2dd4bf" : "#fb7185"}
                  fontSize={9}
                  fontFamily="monospace"
                >
                  {e.detected ? "✓" : "✗ missed"} {e.kind} {e.start.slice(5, 10)}
                </text>
                {e.detected && e.first_alert && (
                  <g>
                    <line
                      x1={x(parseReportTs(e.first_alert))}
                      x2={x(parseReportTs(e.first_alert))}
                      y1={BAND_TOP}
                      y2={AXIS_Y}
                      stroke="#e4e4e7"
                      strokeWidth={1.5}
                      strokeDasharray="3 2"
                    />
                    <path
                      d={`M ${x(parseReportTs(e.first_alert))} ${BAND_TOP} l -4 -7 l 8 0 Z`}
                      fill="#e4e4e7"
                    />
                    <text
                      x={x(parseReportTs(e.first_alert)) + 3}
                      y={alertY}
                      fill="#a1a1aa"
                      fontSize={9}
                      fontFamily="monospace"
                    >
                      first alert {Math.abs(e.lead_time_hours ?? 0).toFixed(1)}h after onset
                    </text>
                  </g>
                )}
              </g>
            );
          })}

          {/* false-alarm episodes outside event intervals */}
          {evaluation.false_alarm_episode_intervals.map((ep, i) => {
            const mid = x((parseReportTs(ep.start) + parseReportTs(ep.end)) / 2);
            const h = 6 + (ep.n_alert_windows / maxEpisodeWindows) * 22;
            return (
              <line
                key={i}
                x1={mid}
                x2={mid}
                y1={AXIS_Y}
                y2={AXIS_Y + h}
                stroke="#f59e0b"
                strokeWidth={2}
              >
                <title>{`false-alarm episode ${fmtTs(parseReportTs(ep.start))} → ${fmtTs(parseReportTs(ep.end))} · ${ep.n_alert_windows} alert window(s) · loaded fraction ${ep.loaded_fraction ?? "?"}`}</title>
              </line>
            );
          })}
          <text x={PAD_L} y={AXIS_Y + 18} fill="#f59e0b" fontSize={9} fontFamily="monospace">
            ↓ false-alarm episodes outside events (tick height = alert windows)
          </text>
          <text x={PAD_L} y={12} fill="#71717a" fontSize={10} fontFamily="monospace">
            holdout {fmtTs(t0)} → {fmtTs(t1)} · frozen {evaluation.primary_policy.threshold}
          </text>
        </svg>
      </div>
      <p className="mt-2 text-[10px] leading-relaxed text-zinc-600">
        Detected events show the first alert AFTER documented onset — this model detects, it has not
        demonstrated advance warning. Threshold frozen at training time (
        {evaluation.primary_policy.threshold_selection}). Source: {evaluation.model_source} holdout
        report generated {evaluation.generated_at}.
      </p>
    </div>
  );
}

/** Month × operating-mode drift heatmap: WHERE the false alerting lives. */
function DriftHeatmap({ evaluation }: { evaluation: FlagshipEvaluationOut }) {
  const months = Object.keys(evaluation.monthly_mode_alert_rate);
  if (months.length === 0) return null;
  const modes = ["loaded", "idle"];
  const maxRate = Math.max(
    0.01,
    ...months.flatMap((m) =>
      modes.map((mode) => evaluation.monthly_mode_alert_rate[m]?.[mode]?.alert_rate ?? 0),
    ),
  );
  return (
    <div className="rounded-lg border border-ink-700 bg-ink-800/50 p-4">
      <div className="panel-header mb-3">
        Alerting by month × operating mode — share of holdout windows over the threshold
      </div>
      <div className="overflow-x-auto">
        <table className="w-full min-w-[560px] border-separate border-spacing-1 text-center font-mono text-[11px]">
          <thead>
            <tr>
              <th className="text-left font-normal text-zinc-500">mode</th>
              {months.map((m) => (
                <th key={m} className="font-normal text-zinc-500">
                  {m}
                </th>
              ))}
            </tr>
          </thead>
          <tbody>
            {modes.map((mode) => (
              <tr key={mode}>
                <td className="text-left text-zinc-400">{mode}</td>
                {months.map((m) => {
                  const cell = evaluation.monthly_mode_alert_rate[m]?.[mode];
                  const rate = cell?.alert_rate ?? null;
                  const intensity = rate == null ? 0 : rate / maxRate;
                  return (
                    <td
                      key={m}
                      className="rounded px-2 py-2.5 text-zinc-100"
                      style={{
                        backgroundColor:
                          rate == null
                            ? "rgba(63,63,70,0.3)"
                            : `rgba(251,113,133,${0.08 + intensity * 0.55})`,
                      }}
                      title={cell ? `${cell.windows} windows` : "no windows"}
                    >
                      {rate == null ? "—" : `${(rate * 100).toFixed(1)}%`}
                      <div className="text-[9px] text-zinc-400">
                        {cell ? `${cell.windows}w` : ""}
                      </div>
                    </td>
                  );
                })}
              </tr>
            ))}
          </tbody>
        </table>
      </div>
      <p className="mt-2 text-[10px] leading-relaxed text-zinc-600">
        Includes event windows. Loaded windows alert often from May onward while idle windows almost
        never do. Heavy load was rare in the Feb–Apr healthy training data, so the most likely
        explanation is that the model treats working hard as unusual. That hasn&apos;t been
        confirmed experimentally. Either way it&apos;s one reason the model stays in shadow mode.
      </p>
    </div>
  );
}

export default function MlLabPage() {
  const [cases, setCases] = useState<CaseMemoryOut[]>([]);
  const [evaluation, setEvaluation] = useState<FlagshipEvaluationOut | null>(null);

  useEffect(() => {
    api.caseMemory().then(setCases).catch(console.error);
    api.flagshipEvaluation().then(setEvaluation).catch(console.error);
  }, []);

  return (
    <div className="space-y-5">
      <header>
        <h1 className="text-xl font-bold tracking-tight text-zinc-100">ML Model Lab</h1>
        <p className="text-sm text-zinc-500">
          Model registry and case memory — real MetroPT-3 detector (shadow-only) beside the labeled
          mock adapter for synthetic assets.
        </p>
      </header>

      <div className="grid lg:grid-cols-2 gap-4">
        <Panel title="Model registry">
          <div className="space-y-3 text-xs">
            <div className="flex items-center justify-between rounded-lg border border-pulse-400/30 bg-pulse-500/5 p-3">
              <div>
                <div className="font-mono font-semibold text-zinc-100">metropt3_iforest_v1</div>
                <div className="text-zinc-400 mt-0.5">
                  Real anomaly detector trained on UCI MetroPT-3 compressor telemetry · scores
                  APU-01 during replay · outputs an anomaly risk score, not a calibrated failure
                  probability
                </div>
              </div>
              <span className="chip border bg-risk-mid/15 text-risk-mid border-risk-mid/40 uppercase shrink-0">
                Real · shadow-only
              </span>
            </div>
            <div className="flex items-center justify-between rounded-lg border border-risk-mid/30 bg-risk-mid/5 p-3">
              <div>
                <div className="font-mono font-semibold text-zinc-100">mock_demo_v0</div>
                <div className="text-zinc-400 mt-0.5">
                  Deterministic heuristic over sensor statistics · powers the synthetic HVAC assets
                  (chillers/pumps) in the Command Center
                </div>
              </div>
              <span className="chip border bg-risk-mid/15 text-risk-mid border-risk-mid/40 uppercase shrink-0">
                Mock / demo
              </span>
            </div>
            <p className="text-zinc-500 leading-relaxed">
              The flagship model is trained exclusively on the real public MetroPT-3 dataset — never
              on the synthetic facility stream — and its trigger policy is{" "}
              <b className="text-zinc-300">shadow mode</b>: would-trigger alerts are recorded,
              incidents are never opened automatically. Holdout evaluation and limitations:{" "}
              <span className="font-mono">docs/model-card.md</span>.
            </p>
          </div>
        </Panel>

        <Panel title="Data boundaries">
          <ul className="space-y-2 text-xs text-zinc-400">
            <li className="flex gap-2">
              <span className="text-pulse-400">●</span>
              APU-01 replay telemetry: <b className="text-zinc-300">real public history</b> (UCI
              MetroPT-3, CC BY 4.0), replayed on a simulation clock.
            </li>
            <li className="flex gap-2">
              <span className="text-risk-low">●</span>
              Facility topology, HVAC sensors, incidents, manuals, postmortems:{" "}
              <b className="text-zinc-300">synthetic</b>, generated by the seed script.
            </li>
            <li className="flex gap-2">
              <span className="text-risk-mid">●</span>
              HVAC risk scores: <b className="text-zinc-300">mock adapter</b>, labeled mock_demo_v0
              in every API response.
            </li>
            <li className="flex gap-2">
              <span className="text-zinc-500">●</span>
              NASA C-MAPSS is <b className="text-zinc-300">simulated</b> data and is not used. No
              real company or customer data anywhere.
            </li>
          </ul>
        </Panel>
      </div>

      <Panel
        title="Held-out evaluation — generated report, not a demo claim"
        right={
          evaluation && (
            <span className="chip border border-risk-mid/40 bg-risk-mid/10 uppercase text-risk-mid">
              {evaluation.trigger_mode} only
            </span>
          )
        }
      >
        {!evaluation ? (
          <EmptyState text="Evaluation report unavailable — regenerate ml/reports/metropt3_eval.json." />
        ) : (
          <div className="space-y-4">
            <div className="grid gap-3 sm:grid-cols-2 lg:grid-cols-5">
              {[
                [
                  "Event detection",
                  `${evaluation.primary_policy.events_detected}/${evaluation.events_total}`,
                  "independent air-leak events",
                ],
                [
                  "False alarms",
                  evaluation.primary_policy.false_alarms_per_operating_day.toFixed(3),
                  evaluation.primary_policy.false_alarms_per_observed_day != null
                    ? `episodes / eligible day (${evaluation.primary_policy.false_alarms_per_observed_day.toFixed(3)} per observed day)`
                    : "episodes / eligible day",
                ],
                [
                  "Alert windows",
                  `${(evaluation.primary_policy.alert_window_rate * 100).toFixed(2)}%`,
                  "of holdout windows",
                ],
                [
                  "Loaded alerting",
                  `${(evaluation.primary_policy.outside_event_loaded_alert_rate * 100).toFixed(1)}%`,
                  "outside-event loaded alerting",
                ],
                [
                  "Holdout",
                  evaluation.holdout_windows.toLocaleString(),
                  `${evaluation.eligible_days} eligible days`,
                ],
              ].map(([label, value, note]) => (
                <div key={label} className="rounded-lg border border-ink-700 bg-ink-800/60 p-3">
                  <div className="panel-header">{label}</div>
                  <div className="mt-1 font-mono text-2xl font-bold text-zinc-100">{value}</div>
                  <div className="mt-1 text-[10px] text-zinc-500">{note}</div>
                </div>
              ))}
            </div>

            {evaluation.persistent_policy && (
              <p className="rounded-lg border border-ink-700 bg-ink-800/50 px-4 py-3 text-xs text-zinc-400">
                These figures count every single window over the threshold. The app only raises a
                shadow alert after three consecutive windows, stays alerted until the score falls
                below 80% of the threshold, then waits 60 minutes. Under that rule the same model
                detected{" "}
                <b className="text-zinc-200">
                  {evaluation.persistent_policy.events_detected}/{evaluation.events_total}
                </b>{" "}
                events with{" "}
                <b className="text-zinc-200">{evaluation.persistent_policy.false_alarm_episodes}</b>{" "}
                false-alarm episodes (
                {evaluation.persistent_policy.false_alarms_per_observed_day?.toFixed(3) ??
                  evaluation.persistent_policy.false_alarms_per_operating_day.toFixed(3)}{" "}
                per observed day).
              </p>
            )}

            <HoldoutChronology evaluation={evaluation} />

            <DriftHeatmap evaluation={evaluation} />

            <div className="rounded-lg border border-risk-mid/30 bg-risk-mid/5 p-4">
              <div className="text-xs font-semibold text-risk-mid">
                Why autonomous triggering is disabled
              </div>
              <ul className="mt-2 space-y-1 text-xs text-zinc-400">
                {evaluation.readiness_blockers.map((blocker) => (
                  <li key={blocker}>• {blocker}</li>
                ))}
              </ul>
              <p className="mt-2 text-[10px] leading-relaxed text-zinc-600">
                Source: ml/reports/metropt3_eval.json · generated {evaluation.generated_at} ·
                threshold selected from healthy data without failure-event leakage.
              </p>
            </div>
          </div>
        )}
      </Panel>

      <Panel title={`Case memory — institutional knowledge (${cases.length})`}>
        {cases.length === 0 ? (
          <EmptyState text="Case memory empty" />
        ) : (
          <div className="grid md:grid-cols-2 gap-3">
            {cases.map((c) => (
              <div key={c.id} className="rounded-lg border border-ink-700 bg-ink-800/60 p-3.5">
                <div className="flex items-center justify-between gap-2">
                  <span className="text-xs font-semibold text-zinc-200">{c.title}</span>
                  <div className="flex gap-1.5 shrink-0">
                    <span
                      className={`chip border ${
                        c.validation_status === "validated_success"
                          ? "bg-risk-low/10 text-risk-low border-risk-low/30"
                          : c.validation_status === "validated_failure"
                            ? "bg-risk-high/10 text-risk-high border-risk-high/30"
                            : "bg-zinc-500/10 text-zinc-400 border-zinc-500/30"
                      }`}
                      title={`validated by: ${c.validated_by || "n/a"}`}
                    >
                      {c.validation_status.replace(/_/g, " ")}
                    </span>
                    {c.source_run_id ? (
                      <span className="chip border bg-pulse-500/10 text-pulse-400 border-pulse-400/30">
                        AGENT RUN #{c.source_run_id}
                      </span>
                    ) : (
                      <span className="chip border bg-zinc-500/10 text-zinc-400 border-zinc-500/30">
                        seeded
                      </span>
                    )}
                  </div>
                </div>
                <div className="mt-2 text-[11px] space-y-1 text-zinc-400">
                  <div>
                    <span className="text-zinc-600">root cause:</span>{" "}
                    <span className="text-zinc-300">{c.root_cause}</span>
                  </div>
                  <div>
                    <span className="text-zinc-600">symptoms:</span> {c.symptoms.join(", ")}
                  </div>
                  <div>
                    <span className="text-zinc-600">outcome:</span> {c.outcome}
                  </div>
                </div>
              </div>
            ))}
          </div>
        )}
      </Panel>
    </div>
  );
}
