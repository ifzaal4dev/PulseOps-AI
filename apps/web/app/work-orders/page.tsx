"use client";

import { useEffect, useState } from "react";
import { api, WorkOrderOut } from "@/lib/api";
import { EmptyState, Panel, PriorityPill, Spinner, StatusPill } from "@/components/ui";

export default function WorkOrdersPage() {
  const [orders, setOrders] = useState<WorkOrderOut[] | null>(null);

  useEffect(() => {
    api.workOrders().then(setOrders).catch(console.error);
    const t = setInterval(() => api.workOrders().then(setOrders).catch(console.error), 5000);
    return () => clearInterval(t);
  }, []);

  if (!orders)
    return (
      <div className="py-20 text-center">
        <Spinner />
      </div>
    );

  return (
    <div className="space-y-5">
      <header>
        <h1 className="text-xl font-bold tracking-tight text-zinc-100">Work Orders</h1>
        <p className="text-sm text-zinc-500">
          Human-created and agent-proposed maintenance. Agent orders exist only after human
          approval.
        </p>
      </header>

      <Panel>
        {orders.length === 0 ? (
          <EmptyState text="No work orders" />
        ) : (
          <table className="w-full text-left text-xs">
            <thead>
              <tr className="panel-header border-b border-ink-700">
                <th className="py-2 pr-4">#</th>
                <th className="py-2 pr-4">Title</th>
                <th className="py-2 pr-4">Source</th>
                <th className="py-2 pr-4">Priority</th>
                <th className="py-2 pr-4">Status</th>
                <th className="py-2">Created</th>
              </tr>
            </thead>
            <tbody>
              {orders.map((w) => (
                <tr key={w.id} className="border-b border-ink-800 hover:bg-ink-800/40">
                  <td className="py-2.5 pr-4 font-mono text-zinc-500">{w.id}</td>
                  <td className="py-2.5 pr-4">
                    <div className="font-medium text-zinc-200">{w.title}</div>
                    {w.notes && <div className="text-zinc-500 mt-0.5 line-clamp-1">{w.notes}</div>}
                  </td>
                  <td className="py-2.5 pr-4">
                    {w.source === "agent" ? (
                      <span className="chip border bg-pulse-500/10 text-pulse-400 border-pulse-400/30">
                        AGENT
                      </span>
                    ) : (
                      <span className="text-zinc-500">human</span>
                    )}
                  </td>
                  <td className="py-2.5 pr-4">
                    <PriorityPill priority={w.priority} />
                  </td>
                  <td className="py-2.5 pr-4">
                    <StatusPill status={w.status} />
                  </td>
                  <td className="py-2.5 font-mono text-zinc-500">
                    {new Date(w.created_at).toLocaleDateString()}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
      </Panel>
    </div>
  );
}
