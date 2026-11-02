"use client";

import { useState } from "react";
import { Play, Check, X, GitMerge, AlertTriangle, FileText, CheckCircle2 } from "lucide-react";
import { Card, Spinner, StatTile, StatusBadge } from "@/components/ui";
import { useApi } from "@/lib/hooks";
import { api } from "@/lib/api";
import { titleCase } from "@/lib/format";
import { withRef } from "@/lib/refs";
import { useViewNav, ViewLink } from "./ViewNav";
import { ViewHeader, chip } from "./ViewHeader";

/** One acquired book: its mappings, conflicts and the operations decision. In the work pane running
 * the reconciliation and deciding its proposal are the conversation's tools behind their Confirm
 * card; on a page the buttons act directly. */
export function BookBatchView({ id }: { id: string }) {
  const nav = useViewNav();
  const { data: b, loading, refetch } = useApi<any>(`/api/onboarding/book-batches/${id}`, [id]);
  const [busy, setBusy] = useState<string | null>(null);
  const [err, setErr] = useState<string | null>(null);

  if (loading || !b) return <div className="view-container"><Spinner /></div>;
  const m = b.mappings || {};
  const stats = b.stats || {};
  const canDecide = b.recommendation_id && b.status === "reconciled";

  async function run() {
    if (nav.say) return nav.say(withRef("Run book integration on this batch", "book_batch", id));
    setBusy("run"); setErr(null);
    try { await api(`/api/onboarding/book-batches/${id}/run`, { body: {} }); await refetch(); }
    catch (e: any) { setErr(e.message); } finally { setBusy(null); }
  }
  async function decide(action: "approve" | "dismiss") {
    if (nav.say) {
      return nav.say(withRef(action === "approve" ? "Approve this proposal" : "Dismiss this proposal",
        "recommendation", b.recommendation_id));
    }
    setBusy(action); setErr(null);
    try {
      await api(`/api/studio/recommendations/${b.recommendation_id}/decide`, { body: { action } });
      await refetch(); nav.changed();
    }
    catch (e: any) { setErr(e.message); } finally { setBusy(null); }
  }

  return (
    <div className="view-container" data-testid="book-batch-view">
      <ViewHeader
        title={b.source_firm}
        meta={
          <>
            <StatusBadge status={b.status} />
            <span className={`${chip} tabular-nums`}>{b.feed?.clients?.length || 0} clients</span>
            <span className={`${chip} tabular-nums`}>{b.feed?.holdings?.length || 0} holdings</span>
            <ViewLink href="/studio/book-integration" className="chip bg-border-soft text-ink-muted hover:text-ink">All batches</ViewLink>
          </>
        }
        actions={
          b.status === "received" ? (
            <button className="btn-primary" disabled={busy === "run"} onClick={run} data-testid="book-batch-run">
              <Play size={16} /> {busy === "run" ? "Reconciling…" : nav.say ? "Ask Astra to reconcile" : "Run reconciliation"}
            </button>
          ) : null
        }
      />
      {err && <div className="card mb-4 border-crit p-3 text-sm text-crit">{err}</div>}

      {b.status === "committed" && (
        <div className="card mb-5 flex flex-wrap items-center gap-2 p-4" data-testid="book-batch-committed">
          <CheckCircle2 className="text-ok" size={18} />
          <span className="chip bg-ok-bg text-ok">Committed</span>
          <span className={`${chip} tabular-nums`}>{b.committed?.new_clients ?? "—"} new clients</span>
          <span className={`${chip} tabular-nums`}>{b.committed?.created_instruments ?? "—"} instruments</span>
          <span className={`${chip} tabular-nums`}>{b.committed?.holdings_written ?? "—"} holdings</span>
        </div>
      )}

      {b.status !== "received" && (
        <>
          <div className="mb-5 grid grid-cols-2 gap-3 cq-md:grid-cols-5">
            <StatTile label="Clients" value={stats.clients ?? "—"} />
            <StatTile label="Merges" value={stats.merges ?? "—"} />
            <StatTile label="New clients" value={stats.new_clients ?? "—"} accent="warn" />
            <StatTile label="Unmapped securities" value={stats.unmapped_securities ?? "—"} />
            <StatTile label="Conflicts" value={stats.conflicts ?? "—"} accent={stats.conflicts ? "crit" : undefined} />
          </div>

          <div className="grid gap-5 cq-lg:grid-cols-2">
            <Card className="min-w-0">
              <div className="mb-3 flex items-center gap-2 font-semibold text-ink"><GitMerge size={16} /> Client mapping</div>
              <Table head={["Inbound", "Action", "Match", "Score"]}>
                {(m.client_mappings || []).map((c: any, i: number) => (
                  <tr key={i} className="border-t border-border-soft">
                    <td className="py-1.5 pr-2 font-medium text-ink">{c.inbound}</td>
                    <td className="py-1.5 pr-2"><span className={`chip ${c.action === "merge" ? "bg-warn-bg text-warn" : "bg-ok-bg text-ok"}`}>{titleCase(c.action)}</span></td>
                    <td className="py-1.5 pr-2 text-right text-xs text-ink-muted">{c.target_name || "—"}</td>
                    <td className="py-1.5 text-right font-mono text-xs tabular-nums">{Math.round(c.score * 100)}%</td>
                  </tr>
                ))}
              </Table>
            </Card>

            <Card className="min-w-0">
              <div className="mb-3 font-semibold text-ink">Security mapping</div>
              <Table head={["Inbound", "Action", "Target"]}>
                {(m.security_mappings || []).map((s: any, i: number) => (
                  <tr key={i} className="border-t border-border-soft">
                    <td className="py-1.5 pr-2 font-medium text-ink">{s.inbound}</td>
                    <td className="py-1.5 pr-2"><span className={`chip ${s.action === "map" ? "bg-ok-bg text-ok" : "bg-warn-bg text-warn"}`}>{titleCase(s.action)}</span></td>
                    <td className="py-1.5 text-right text-xs text-ink-muted">{s.name}</td>
                  </tr>
                ))}
              </Table>
            </Card>

            {m.holding_conflicts?.length > 0 && (
              <Card className="min-w-0 border-crit" data-testid="book-batch-conflicts">
                <div className="mb-3 flex items-center gap-2 font-semibold text-ink"><AlertTriangle size={16} className="text-crit" /> Holding conflicts</div>
                <Table head={["Client", "Security", "Inbound", "On file", "Δ"]}>
                  {m.holding_conflicts.map((c: any, i: number) => (
                    <tr key={i} className="border-t border-border-soft">
                      <td className="py-1.5 pr-2 text-ink">{c.client}</td>
                      <td className="py-1.5 pr-2 font-medium">{c.symbol}</td>
                      <td className="py-1.5 pr-2 text-right font-mono tabular-nums">{c.inbound_quantity}</td>
                      <td className="py-1.5 pr-2 text-right font-mono tabular-nums">{c.existing_quantity}</td>
                      <td className={`py-1.5 text-right font-mono tabular-nums ${c.delta >= 0 ? "text-ok" : "text-crit"}`}>{c.delta}</td>
                    </tr>
                  ))}
                </Table>
              </Card>
            )}

            {m.capital_calls?.length > 0 && (
              <Card className="min-w-0">
                <div className="mb-3 flex flex-wrap items-center gap-2 font-semibold text-ink">
                  <FileText size={16} /> Capital-call notices <span className={chip}>Extracted</span>
                </div>
                <div className="space-y-2">
                  {m.capital_calls.map((c: any, i: number) => (
                    <div key={i} className="rounded-lg border border-border-soft p-3 text-sm">
                      <div className="grid gap-x-4 gap-y-1 text-xs cq-sm:grid-cols-2">
                        {Object.entries(c.fields || {}).map(([k, v]: any) => (
                          <div key={k} className="flex justify-between gap-2"><span className="text-ink-muted">{titleCase(k)}</span><span className="break-words text-right text-ink-soft">{String(v)}</span></div>
                        ))}
                      </div>
                    </div>
                  ))}
                </div>
              </Card>
            )}
          </div>

          {canDecide && (
            <Card className="mt-5" data-testid="book-batch-decision">
              <div className="flex flex-wrap items-center justify-between gap-3">
                <div className="flex flex-wrap items-center gap-2">
                  <span className="font-semibold text-ink">Operations validation</span>
                  <span className="chip bg-warn-bg text-warn">Awaiting decision</span>
                </div>
                <div className="flex flex-wrap gap-2">
                  <button className="btn-outline" disabled={!!busy} onClick={() => decide("dismiss")} data-testid="book-batch-dismiss">
                    <X size={15} /> {nav.say ? "Ask Astra to dismiss" : "Dismiss"}
                  </button>
                  <button className="btn-primary" disabled={!!busy} onClick={() => decide("approve")} data-testid="book-batch-commit">
                    <Check size={15} /> {nav.say ? "Ask Astra to commit" : "Commit mappings"}
                  </button>
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
        <thead className="text-xs uppercase tracking-wide text-ink-muted">
          <tr>{head.map((h, i) => <th key={i} className={`py-1.5 ${i >= 2 ? "text-right" : "text-left"}`}>{h}</th>)}</tr>
        </thead>
        <tbody>{children}</tbody>
      </table>
    </div>
  );
}
