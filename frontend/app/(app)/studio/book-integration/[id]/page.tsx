"use client";
import { useState } from "react";
import { useParams } from "next/navigation";
import { Play, Check, X, GitMerge, AlertTriangle, FileText, CheckCircle2 } from "lucide-react";
import PageHeader from "@/components/PageHeader";
import { Card, Spinner, StatTile, StatusBadge } from "@/components/ui";
import { useApi } from "@/lib/hooks";
import { api } from "@/lib/api";
import { titleCase } from "@/lib/format";

export default function BatchDetail() {
  const { id } = useParams<{ id: string }>();
  const { data: b, loading, refetch } = useApi<any>(`/api/onboarding/book-batches/${id}`, [id]);
  const [busy, setBusy] = useState<string | null>(null);
  const [err, setErr] = useState<string | null>(null);

  if (loading || !b) return <Spinner />;
  const m = b.mappings || {};
  const stats = b.stats || {};
  const canDecide = b.recommendation_id && b.status === "reconciled";

  async function run() {
    setBusy("run"); setErr(null);
    try { await api(`/api/onboarding/book-batches/${id}/run`, { body: {} }); await refetch(); }
    catch (e: any) { setErr(e.message); } finally { setBusy(null); }
  }
  async function decide(action: string) {
    setBusy(action); setErr(null);
    try { await api(`/api/studio/recommendations/${b.recommendation_id}/decide`, { body: { action } }); await refetch(); }
    catch (e: any) { setErr(e.message); } finally { setBusy(null); }
  }

  return (
    <div>
      <PageHeader
        title={b.source_firm}
        sub={`Acquired book · status ${titleCase(b.status)}`}
        actions={
          b.status === "received" ? (
            <button className="btn-primary" disabled={busy === "run"} onClick={run}><Play size={16} /> {busy === "run" ? "Reconciling…" : "Run reconciliation"}</button>
          ) : null
        }
      />
      {err && <div className="card p-3 mb-4 text-sm text-critical border-critical/30">{err}</div>}

      {b.status === "committed" && (
        <div className="card p-4 mb-5 border-positive/30 flex items-center gap-3">
          <CheckCircle2 className="text-positive" />
          <div className="text-sm">
            <span className="font-semibold text-ink">Committed as golden records.</span>
            <span className="text-ink-muted"> {b.committed?.new_clients} new clients, {b.committed?.created_instruments} instruments, {b.committed?.holdings_written} holdings written into the brain.</span>
          </div>
        </div>
      )}

      {b.status !== "received" && (
        <>
          <div className="grid grid-cols-2 lg:grid-cols-5 gap-3 mb-5">
            <StatTile label="Clients" value={stats.clients ?? "—"} />
            <StatTile label="Merges" value={stats.merges ?? "—"} />
            <StatTile label="New clients" value={stats.new_clients ?? "—"} accent="gold" />
            <StatTile label="Unmapped securities" value={stats.unmapped_securities ?? "—"} />
            <StatTile label="Conflicts" value={stats.conflicts ?? "—"} accent={stats.conflicts ? "critical" : undefined} />
          </div>

          <div className="grid lg:grid-cols-2 gap-5">
            <Card>
              <div className="font-semibold text-ink mb-3 flex items-center gap-2"><GitMerge size={16} /> Client mapping</div>
              <Table head={["Inbound", "Action", "Match", "Score"]}>
                {(m.client_mappings || []).map((c: any, i: number) => (
                  <tr key={i} className="border-t border-navy-50">
                    <td className="py-1.5 text-ink font-medium">{c.inbound}</td>
                    <td className="py-1.5"><span className={`chip ${c.action === "merge" ? "bg-gold-soft/40 text-gold-dark" : "bg-positive/10 text-positive"}`}>{titleCase(c.action)}</span></td>
                    <td className="py-1.5 text-ink-muted text-xs">{c.target_name || "—"}</td>
                    <td className="py-1.5 text-right text-xs tabular-nums">{Math.round(c.score * 100)}%</td>
                  </tr>
                ))}
              </Table>
            </Card>

            <Card>
              <div className="font-semibold text-ink mb-3">Security mapping</div>
              <Table head={["Inbound", "Action", "Target"]}>
                {(m.security_mappings || []).map((s: any, i: number) => (
                  <tr key={i} className="border-t border-navy-50">
                    <td className="py-1.5 text-ink font-medium">{s.inbound}</td>
                    <td className="py-1.5"><span className={`chip ${s.action === "map" ? "bg-positive/10 text-positive" : "bg-gold-soft/40 text-gold-dark"}`}>{titleCase(s.action)}</span></td>
                    <td className="py-1.5 text-ink-muted text-xs">{s.name}</td>
                  </tr>
                ))}
              </Table>
            </Card>

            {m.holding_conflicts?.length > 0 && (
              <Card className="border-critical/30">
                <div className="font-semibold text-ink mb-3 flex items-center gap-2"><AlertTriangle size={16} className="text-critical" /> Holding conflicts</div>
                <Table head={["Client", "Security", "Inbound", "In brain", "Δ"]}>
                  {m.holding_conflicts.map((c: any, i: number) => (
                    <tr key={i} className="border-t border-navy-50">
                      <td className="py-1.5 text-ink">{c.client}</td>
                      <td className="py-1.5 font-medium">{c.symbol}</td>
                      <td className="py-1.5 text-right tabular-nums">{c.inbound_quantity}</td>
                      <td className="py-1.5 text-right tabular-nums">{c.existing_quantity}</td>
                      <td className={`py-1.5 text-right tabular-nums ${c.delta >= 0 ? "text-positive" : "text-critical"}`}>{c.delta}</td>
                    </tr>
                  ))}
                </Table>
              </Card>
            )}

            {m.capital_calls?.length > 0 && (
              <Card>
                <div className="font-semibold text-ink mb-3 flex items-center gap-2"><FileText size={16} /> Capital-call notices · extracted</div>
                {m.capital_calls.map((c: any, i: number) => (
                  <div key={i} className="rounded-lg border border-navy-100 p-3 text-sm">
                    <div className="grid grid-cols-2 gap-x-4 gap-y-1 text-xs">
                      {Object.entries(c.fields || {}).map(([k, v]: any) => (
                        <div key={k} className="flex justify-between"><span className="text-ink-muted">{titleCase(k)}</span><span className="text-ink-soft">{String(v)}</span></div>
                      ))}
                    </div>
                  </div>
                ))}
              </Card>
            )}
          </div>

          {canDecide && (
            <Card className="mt-5">
              <div className="flex items-center justify-between">
                <div>
                  <div className="font-semibold text-ink">Operations validation</div>
                  <p className="text-xs text-ink-muted">Tier 2 — commit the mappings as golden records, or dismiss.</p>
                </div>
                <div className="flex gap-2">
                  <button className="btn-outline" disabled={!!busy} onClick={() => decide("dismiss")}><X size={15} /> Dismiss</button>
                  <button className="btn-gold" disabled={!!busy} onClick={() => decide("approve")}><Check size={15} /> Commit golden records</button>
                </div>
              </div>
            </Card>
          )}
        </>
      )}
    </div>
  );
}

function Table({ head, children }: { head: string[]; children: any }) {
  return (
    <div className="overflow-x-auto">
      <table className="w-full text-sm">
        <thead className="text-ink-muted text-xs uppercase tracking-wide">
          <tr>{head.map((h, i) => <th key={i} className={`py-1.5 ${i >= 2 ? "text-right" : "text-left"}`}>{h}</th>)}</tr>
        </thead>
        <tbody>{children}</tbody>
      </table>
    </div>
  );
}
