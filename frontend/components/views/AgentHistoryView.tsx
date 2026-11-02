"use client";

import { useEffect, useState } from "react";
import type { ReactNode } from "react";
import { CheckCircle2, XCircle, Clock, RefreshCw } from "lucide-react";
import { api } from "@/lib/api";
import { Card, Empty, ErrorState, SkeletonList } from "@/components/ui";
import { ViewHeader, chip } from "./ViewHeader";

/** One row of `/api/studio/agents/history` (backend `atlas/history.run_history`). */
interface AgentRun {
  id: string;
  agent_key: string | null;
  status: string;
  tier: string | null;
  trigger: string | null;
  duration_ms: number | null;
  created_at: string | null;
  updated_at: string | null;
}

// A running agent is the one place yellow belongs: agent working.
const STATUS_ICON: Record<string, ReactNode> = {
  completed: <CheckCircle2 size={14} className="text-ok" />,
  failed: <XCircle size={14} className="text-crit" />,
  running: <RefreshCw size={14} className="animate-spin text-accent" />,
  pending: <Clock size={14} className="text-warn" />,
};

const STATUS_CHIP: Record<string, string> = {
  completed: "bg-ok-bg text-ok",
  failed: "bg-crit-bg text-crit",
  running: "bg-border-soft text-ink-soft",
  pending: "bg-warn-bg text-warn",
};

const AGENTS = [
  "all", "drift_rebalancing", "next_best_action", "conduct_surveillance",
  "client_care", "research_reporting", "meeting_prep", "onboarding_kyc",
  "book_integration", "paraplanner",
];

const th = "px-4 py-2 font-medium";

/** Every agent run across the firm, by day, newest first. Read only. */
export function AgentHistoryView() {
  const [runs, setRuns] = useState<AgentRun[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [agentFilter, setAgentFilter] = useState("all");
  const [statusFilter, setStatusFilter] = useState("all");
  const [dateFrom, setDateFrom] = useState("");
  const [dateTo, setDateTo] = useState("");

  async function load() {
    setLoading(true);
    setError(null);
    try {
      const qs = new URLSearchParams();
      if (agentFilter !== "all") qs.set("agent_key", agentFilter);
      if (statusFilter !== "all") qs.set("status", statusFilter);
      if (dateFrom) qs.set("date_from", dateFrom);
      if (dateTo) qs.set("date_to", dateTo);
      qs.set("limit", "100");
      setRuns(await api<AgentRun[]>(`/api/studio/agents/history?${qs}`));
    } catch (e: any) {
      setError(e?.message || "Failed to load");
    }
    setLoading(false);
  }

  // eslint-disable-next-line react-hooks/exhaustive-deps
  useEffect(() => { load(); }, [agentFilter, statusFilter]);

  const grouped = runs.reduce<Record<string, AgentRun[]>>((acc, r) => {
    const date = r.created_at ? r.created_at.slice(0, 10) : "Unknown";
    (acc[date] ||= []).push(r);
    return acc;
  }, {});
  const days = Object.entries(grouped).sort(([a], [b]) => b.localeCompare(a));

  return (
    <div className="view-container">
      <ViewHeader
        title="Agent run history"
        meta={!loading && !error ? <span className={chip} data-testid="agent-history-count">{runs.length} runs</span> : null}
        actions={<button onClick={load} className="btn-outline text-sm" data-testid="agent-history-refresh"><RefreshCw size={14} /> Refresh</button>}
      />

      <div className="card mb-5 flex flex-wrap items-end gap-3 p-4" data-testid="agent-history-filters">
        <div className="min-w-[9rem] flex-1">
          <label className="label mb-1 text-xs">Agent</label>
          <select className="input text-sm" value={agentFilter} onChange={(e) => setAgentFilter(e.target.value)}>
            {AGENTS.map((a) => <option key={a} value={a}>{a === "all" ? "All agents" : a.replace(/_/g, " ")}</option>)}
          </select>
        </div>
        <div className="min-w-[9rem] flex-1">
          <label className="label mb-1 text-xs">Status</label>
          <select className="input text-sm" value={statusFilter} onChange={(e) => setStatusFilter(e.target.value)}>
            {["all", "completed", "failed", "running", "pending"].map((s) => (
              <option key={s} value={s}>{s === "all" ? "All statuses" : s}</option>
            ))}
          </select>
        </div>
        <div className="min-w-[9rem] flex-1">
          <label className="label mb-1 text-xs">From</label>
          <input type="date" className="input text-sm" value={dateFrom} onChange={(e) => setDateFrom(e.target.value)} />
        </div>
        <div className="min-w-[9rem] flex-1">
          <label className="label mb-1 text-xs">To</label>
          <input type="date" className="input text-sm" value={dateTo} onChange={(e) => setDateTo(e.target.value)} />
        </div>
        <button className="btn-primary text-sm" onClick={load}>Apply</button>
      </div>

      {loading ? <SkeletonList count={3} /> : error ? (
        <Card><ErrorState what="agent history" message={error} onRetry={load} /></Card>
      ) : days.length === 0 ? (
        <Card><Empty>No runs match your filters.</Empty></Card>
      ) : (
        <div className="space-y-5" data-testid="agent-history-rows">
          {days.map(([date, dayRuns]) => (
            <div key={date}>
              <div className="mb-2 text-xs font-medium uppercase tracking-wide text-ink-muted">{date}</div>
              <Card className="overflow-hidden p-0">
                <div className="overflow-x-auto">
                  <table className="w-full text-sm">
                    <thead>
                      <tr className="border-b border-border-soft text-left text-[10px] uppercase tracking-wide text-ink-muted">
                        <th className={th}>Agent</th>
                        <th className={th}>Trigger</th>
                        <th className={th}>Tier</th>
                        <th className={th}>Status</th>
                        <th className={th}>Time</th>
                      </tr>
                    </thead>
                    <tbody className="divide-y divide-border-soft">
                      {dayRuns.map((r) => (
                        <tr key={r.id} className="hover:bg-border-soft/40">
                          <td className="px-4 py-3 font-medium text-ink">{r.agent_key?.replace(/_/g, " ")}</td>
                          <td className="px-4 py-3 text-ink-muted">{r.trigger || "—"}</td>
                          <td className="px-4 py-3 text-ink-muted">{r.tier || "—"}</td>
                          <td className="px-4 py-3">
                            <span className={`chip w-fit text-xs ${STATUS_CHIP[r.status] || "bg-border-soft text-ink-soft"}`}>
                              {STATUS_ICON[r.status] || null}
                              {r.status}
                            </span>
                          </td>
                          <td className="whitespace-nowrap px-4 py-3 font-mono text-xs tabular-nums text-ink-muted">
                            {r.created_at ? new Date(r.created_at).toLocaleTimeString() : "—"}
                            {r.duration_ms ? <span className="ml-1">({(r.duration_ms / 1000).toFixed(1)}s)</span> : null}
                          </td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </div>
              </Card>
            </div>
          ))}
        </div>
      )}
    </div>
  );
}
