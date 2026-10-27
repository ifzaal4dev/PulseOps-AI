"use client";

import Link from "next/link";
import { usePathname } from "next/navigation";

const NAV = [
  { href: "/", label: "Command Center", icon: "◉" },
  { href: "/replay", label: "Replay Lab", icon: "▶" },
  { href: "/agent", label: "Agent Control Room", icon: "⬡" },
  { href: "/work-orders", label: "Work Orders", icon: "▤" },
  { href: "/ml-lab", label: "ML Model Lab", icon: "∿" },
];

export function Sidebar() {
  const pathname = usePathname();
  return (
    <aside className="w-60 shrink-0 border-r border-ink-700 bg-ink-900/80 backdrop-blur flex flex-col sticky top-0 h-screen">
      <div className="px-5 py-6 border-b border-ink-700">
        <Link href="/" className="block">
          <div className="flex items-center gap-2.5">
            <div className="h-8 w-8 rounded-lg bg-pulse-500/15 border border-pulse-400/40 grid place-items-center">
              <span className="text-pulse-400 text-base leading-none">◈</span>
            </div>
            <div>
              <div className="font-bold tracking-tight text-zinc-100 leading-tight">
                PulseOps <span className="text-pulse-400">AI</span>
              </div>
              <div className="text-[10px] uppercase tracking-[0.2em] text-zinc-500">
                Reliability Ops
              </div>
            </div>
          </div>
        </Link>
      </div>

      <nav className="flex-1 px-3 py-4 space-y-1">
        {NAV.map((item) => {
          const active =
            item.href === "/"
              ? pathname === "/" || pathname.startsWith("/assets")
              : pathname.startsWith(item.href);
          return (
            <Link
              key={item.href}
              href={item.href}
              className={`flex items-center gap-3 rounded-lg px-3 py-2 text-[13px] font-medium transition-colors ${
                active
                  ? "bg-pulse-500/10 text-pulse-400 border border-pulse-400/20"
                  : "text-zinc-400 hover:text-zinc-200 hover:bg-ink-800 border border-transparent"
              }`}
            >
              <span className="w-4 text-center">{item.icon}</span>
              {item.label}
            </Link>
          );
        })}
      </nav>

      <div className="px-5 py-4 border-t border-ink-700 text-[11px] text-zinc-500 space-y-1">
        <div className="flex items-center gap-2">
          <span className="h-1.5 w-1.5 rounded-full bg-risk-low animate-pulse" />
          Synthetic facility — Tower A
        </div>
        <div className="font-mono text-[10px] text-zinc-600">
          MetroPT shadow monitor · hybrid agent
        </div>
      </div>
    </aside>
  );
}
