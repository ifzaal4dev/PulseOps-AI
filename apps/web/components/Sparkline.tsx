"use client";

import type { ReadingPoint } from "@/lib/api";

// Dependency-free SVG time-series chart with baseline reference.
export function Sparkline({
  points,
  baseline,
  unit,
  height = 120,
  danger = false,
}: {
  points: ReadingPoint[];
  baseline?: number;
  unit?: string;
  height?: number;
  danger?: boolean;
}) {
  if (!points.length) {
    return <div className="text-xs text-zinc-600 py-6 text-center">no data</div>;
  }
  const w = 600;
  const h = height;
  const pad = 6;
  const values = points.map((p) => p.value);
  const min = Math.min(...values, baseline ?? Infinity);
  const max = Math.max(...values, baseline ?? -Infinity);
  const span = max - min || 1;
  const x = (i: number) => pad + (i / (points.length - 1)) * (w - 2 * pad);
  const y = (v: number) => h - pad - ((v - min) / span) * (h - 2 * pad);

  const path = values
    .map((v, i) => `${i === 0 ? "M" : "L"}${x(i).toFixed(1)},${y(v).toFixed(1)}`)
    .join(" ");
  const area = `${path} L${x(values.length - 1).toFixed(1)},${h - pad} L${x(0).toFixed(1)},${h - pad} Z`;
  const stroke = danger ? "#fb7185" : "#38e1c6";
  const last = values[values.length - 1];

  return (
    <div className="relative">
      <svg viewBox={`0 0 ${w} ${h}`} className="w-full" preserveAspectRatio="none">
        <defs>
          <linearGradient id={`g-${danger}`} x1="0" y1="0" x2="0" y2="1">
            <stop offset="0%" stopColor={stroke} stopOpacity="0.25" />
            <stop offset="100%" stopColor={stroke} stopOpacity="0" />
          </linearGradient>
        </defs>
        {baseline !== undefined && (
          <line
            x1={pad}
            x2={w - pad}
            y1={y(baseline)}
            y2={y(baseline)}
            stroke="#52607a"
            strokeDasharray="4 4"
            strokeWidth="1"
          />
        )}
        <path d={area} fill={`url(#g-${danger})`} />
        <path d={path} fill="none" stroke={stroke} strokeWidth="1.8" />
        <circle cx={x(values.length - 1)} cy={y(last)} r="3" fill={stroke} />
      </svg>
      <div className="absolute top-1 right-2 font-mono text-xs text-zinc-300">
        {last.toFixed(2)}
        {unit && <span className="text-zinc-500 ml-1">{unit}</span>}
      </div>
    </div>
  );
}
