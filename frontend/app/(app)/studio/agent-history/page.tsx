"use client";
import { useEffect, useState } from "react";
import { History, CheckCircle2, XCircle, Clock, RefreshCw } from "lucide-react";
import { api } from "@/lib/api";
import { Spinner } from "@/components/ui";

const STATUS_ICON: Record<string, JSX.Element> = {
  completed: <CheckCircle2 size={14} className="text-positive" />,
  failed: <XCircle size={14} className="text-critical" />,
  running: <RefreshCw size={14} className="text-navy-400 animate-spin" />,
  pending: <Clock size={14} className="text-caution" />,
};

const STATUS_CHIP: Record<string, string> = {
  completed: "bg-positive/10 text-positive",
  failed: "bg-critical/10 text-critical",
  running: "bg-navy-100 text-navy-600",
  pending: "bg-caution/10 text-caution",
};

const AGENTS = [
  "all", "drift_rebalancing", "next_best_action", "conduct_surveillance",
  "client_care", "research_reporting", "meeting_prep", "onboarding_kyc",
  "book_integration", "paraplanner",
];

export default function AgentHistoryPage() {
  const [runs, setRuns] = useState<any[]>([]);
  const [loading, setLoading] = useState(true);
  const [agentFilter, setAgentFilter] = useState("all");
  const [statusFilter, setStatusFilter] = useState("all");
  const [dateFrom, setDateFrom] = useState("");
  const [dateTo, setDateTo] = useState("");

  async function load() {
    setLoading(true);
    try {
      const qs = new URLSearchParams();
      if (agentFilter !== "all") qs.set("agent_key", agentFilter);
      if (statusFilter !== "all") qs.set("status", statusFilter);
      if (dateFrom) qs.set("date_from", dateFrom);
      if (dateTo) qs.set("date_to", dateTo);
      qs.set("limit", "100");
      const data = await api<any[]>(`/api/studio/agents/history?${qs}`);
      setRuns(data);
    } catch {}
    setLoading(false);
  }

  useEffect(() => { load(); }, [agentFilter, statusFilter]);

  const grouped = runs.reduce((acc: Record<string, any[]>, r) => {
    const date = r.created_at ? r.created_at.slice(0, 10) : "Unknown";
    if (!acc[date]) acc[date] = [];
    acc[date].push(r);
    return acc;
  }, {});

  return (
    <div className="max-w-4xl mx-auto">
      <div className="flex items-center justify-between mb-6">
        <div>
          <h1 className="font-serif text-2xl text-ink flex items-center gap-2"><History size={22} /> Agent Run History</h1>
          <p className="text-sm text-ink-muted mt-1">Audit trail of all agent executions across your firm.</p>
        </div>
        <button onClick={load} className="btn-outline text-sm"><RefreshCw size={14} /> Refresh</button>
      </div>

      {/* Filters */}
      <div className="card p-4 mb-5 flex flex-wrap gap-3 items-end">
        <div>
          <label className="label text-xs mb-1">Agent</label>
          <select className="input text-sm" value={agentFilter} onChange={(e) => setAgentFilter(e.target.value)}>
            {AGENTS.map(a => <option key={a} value={a}>{a === "all" ? "All agents" : a.replace(/_/g, " ")}</option>)}
          </select>
        </div>
        <div>
          <label className="label text-xs mb-1">Status</label>
          <select className="input text-sm" value={statusFilter} onChange={(e) => setStatusFilter(e.target.value)}>
            {["all", "completed", "failed", "running", "pending"].map(s => (
              <option key={s} value={s}>{s === "all" ? "All statuses" : s}</option>
            ))}
          </select>
        </div>
        <div>
          <label className="label text-xs mb-1">From</label>
          <input type="date" className="input text-sm" value={dateFrom} onChange={(e) => setDateFrom(e.target.value)} />
        </div>
        <div>
          <label className="label text-xs mb-1">To</label>
          <input type="date" className="input text-sm" value={dateTo} onChange={(e) => setDateTo(e.target.value)} />
        </div>
        <button className="btn-primary text-sm" onClick={load}>Apply</button>
      </div>

      {loading ? <Spinner label="Loading agent history…" /> : (
        <div className="space-y-5">
          {Object.keys(grouped).length === 0 ? (
            <div className="card p-8 text-center text-ink-muted text-sm">No runs match your filters.</div>
          ) : (
            Object.entries(grouped)
              .sort(([a], [b]) => b.localeCompare(a))
              .map(([date, dayRuns]) => (
                <div key={date}>
                  <div className="text-xs font-medium text-ink-muted mb-2 uppercase tracking-wide">{date}</div>
                  <div className="card overflow-hidden">
                    <table className="w-full text-sm">
                      <thead className="bg-navy-50 border-b border-navy-100">
                        <tr>
                          <th className="text-left px-4 py-2.5 font-medium text-ink-muted text-xs">Agent</th>
                          <th className="text-left px-4 py-2.5 font-medium text-ink-muted text-xs">Trigger</th>
                          <th className="text-left px-4 py-2.5 font-medium text-ink-muted text-xs">Tier</th>
                          <th className="text-left px-4 py-2.5 font-medium text-ink-muted text-xs">Status</th>
                          <th className="text-left px-4 py-2.5 font-medium text-ink-muted text-xs">Time</th>
                        </tr>
                      </thead>
                      <tbody className="divide-y divide-navy-50">
                        {dayRuns.map((r: any) => (
                          <tr key={r.id} className="hover:bg-navy-50/50">
                            <td className="px-4 py-3 font-medium text-ink">{r.agent_key?.replace(/_/g, " ")}</td>
                            <td className="px-4 py-3 text-ink-muted">{r.trigger || "—"}</td>
                            <td className="px-4 py-3 text-ink-muted">{r.tier || "—"}</td>
                            <td className="px-4 py-3">
                              <span className={`chip text-xs flex items-center gap-1 w-fit ${STATUS_CHIP[r.status] || "bg-navy-100 text-navy-600"}`}>
                                {STATUS_ICON[r.status] || null}
                                {r.status}
                              </span>
                            </td>
                            <td className="px-4 py-3 text-ink-muted text-xs">
                              {r.created_at ? new Date(r.created_at).toLocaleTimeString() : "—"}
                              {r.duration_ms ? <span className="ml-1 text-ink-muted">({(r.duration_ms/1000).toFixed(1)}s)</span> : null}
                            </td>
                          </tr>
                        ))}
                      </tbody>
                    </table>
                  </div>
                </div>
              ))
          )}
        </div>
      )}
    </div>
  );
}
