"use client";

// Small shared primitives for the command-center look.

export function RiskBadge({ risk }: { risk: number }) {
  const cls =
    risk >= 0.7
      ? "bg-risk-high/10 text-risk-high border-risk-high/30"
      : risk >= 0.4
        ? "bg-risk-mid/10 text-risk-mid border-risk-mid/30"
        : "bg-risk-low/10 text-risk-low border-risk-low/30";
  const label = risk >= 0.7 ? "CRITICAL" : risk >= 0.4 ? "ELEVATED" : "NORMAL";
  return (
    <span className={`chip border font-mono ${cls}`}>
      {(risk * 100).toFixed(0)}% · {label}
    </span>
  );
}

const STATUS_STYLES: Record<string, string> = {
  online: "bg-risk-low/10 text-risk-low border-risk-low/30",
  degraded: "bg-risk-mid/10 text-risk-mid border-risk-mid/30",
  offline: "bg-risk-high/10 text-risk-high border-risk-high/30",
  maintenance: "bg-sky-400/10 text-sky-400 border-sky-400/30",
  // agent runs
  running: "bg-sky-400/10 text-sky-400 border-sky-400/30",
  awaiting_evidence: "bg-sky-400/10 text-sky-300 border-sky-400/40",
  awaiting_approval: "bg-risk-mid/10 text-risk-mid border-risk-mid/30",
  completed: "bg-risk-low/10 text-risk-low border-risk-low/30",
  failed: "bg-risk-high/10 text-risk-high border-risk-high/30",
  paused: "bg-zinc-400/10 text-zinc-400 border-zinc-400/30",
  escalated: "bg-fuchsia-400/10 text-fuchsia-400 border-fuchsia-400/30",
  // actions / work orders
  proposed: "bg-risk-mid/10 text-risk-mid border-risk-mid/30",
  approved: "bg-sky-400/10 text-sky-400 border-sky-400/30",
  rejected: "bg-risk-high/10 text-risk-high border-risk-high/30",
  executed: "bg-risk-low/10 text-risk-low border-risk-low/30",
  open: "bg-sky-400/10 text-sky-400 border-sky-400/30",
  in_progress: "bg-risk-mid/10 text-risk-mid border-risk-mid/30",
  // incidents
  active: "bg-risk-high/10 text-risk-high border-risk-high/30",
  investigating: "bg-risk-mid/10 text-risk-mid border-risk-mid/30",
  mitigated: "bg-risk-low/10 text-risk-low border-risk-low/30",
  closed: "bg-zinc-400/10 text-zinc-400 border-zinc-400/30",
};

export function StatusPill({ status }: { status: string }) {
  const cls = STATUS_STYLES[status] ?? "bg-zinc-400/10 text-zinc-400 border-zinc-400/30";
  return (
    <span className={`chip border uppercase tracking-wide ${cls}`}>
      {status.replace(/_/g, " ")}
    </span>
  );
}

export function PriorityPill({ priority }: { priority: string }) {
  const cls =
    priority === "urgent"
      ? "bg-risk-high/15 text-risk-high border-risk-high/40"
      : priority === "high"
        ? "bg-risk-mid/10 text-risk-mid border-risk-mid/30"
        : "bg-zinc-400/10 text-zinc-400 border-zinc-400/30";
  return <span className={`chip border uppercase ${cls}`}>{priority}</span>;
}

export function Panel({
  title,
  right,
  children,
  className = "",
}: {
  title?: string;
  right?: React.ReactNode;
  children: React.ReactNode;
  className?: string;
}) {
  return (
    <section className={`panel p-4 ${className}`}>
      {(title || right) && (
        <div className="flex items-center justify-between mb-3">
          {title && <h2 className="panel-header">{title}</h2>}
          {right}
        </div>
      )}
      {children}
    </section>
  );
}

export function Spinner() {
  return (
    <span className="inline-block h-3.5 w-3.5 animate-spin rounded-full border-2 border-zinc-600 border-t-pulse-400 align-middle" />
  );
}

export function EmptyState({ text }: { text: string }) {
  return <div className="py-8 text-center text-sm text-zinc-500">{text}</div>;
}
