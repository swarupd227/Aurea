"use client";

import { useEffect, useState } from "react";
import { Receipt } from "lucide-react";
import { Card, Empty, ErrorState, SkeletonList } from "@/components/ui";
import { InvoiceCard } from "@/app/workspace/components/artifacts/InvoiceCard";
import { api, getUser } from "@/lib/api";
import { firmCurrency, firmLocale } from "@/lib/format";
import { useApi } from "@/lib/hooks";
import { withRef } from "@/lib/refs";
import { useViewNav } from "./ViewNav";
import { ViewHeader, chip } from "./ViewHeader";

const APPROVERS = ["operations", "branch_leader"];
const PROPOSERS = ["operations", "branch_leader", "compliance", "admin"];
const VETO = ["operations", "branch_leader", "compliance"];

function money(n: number, cur = firmCurrency()) {
  return `${cur} ${n.toLocaleString(firmLocale(), { minimumFractionDigits: 2, maximumFractionDigits: 2 })}`;
}
function label(s: string) {
  return s.replace(/_/g, " ");
}
function totals(by: Record<string, number> | undefined) {
  const e = Object.entries(by ?? {});
  return e.length ? e.map(([c, a]) => money(a, c)).join(" + ") : "—";
}

function Checks({ checks }: { checks: any[] }) {
  return (
    <div className="flex flex-wrap gap-1">
      {checks.filter((c) => c.severity !== "info").map((c) => (
        <span key={c.code} className={`chip ${c.severity === "block" ? "text-crit" : "text-warn"}`} title={c.detail}>{label(c.code)}</span>
      ))}
    </div>
  );
}

function InvoiceTable({ rows, onOpen, openId, testId }: { rows: any[]; onOpen: (id: string) => void; openId: string | null; testId?: string }) {
  if (!rows.length) return <Empty>Nothing here.</Empty>;
  return (
    <div className="overflow-x-auto" data-testid={testId}>
      <table className="w-full text-sm">
        <thead>
          <tr className="border-b border-border-soft text-left text-[10px] uppercase tracking-wide text-ink-muted">
            <th className="py-1.5 pr-3 font-medium">Bill</th>
            <th className="py-1.5 pr-3 font-medium">Period</th>
            <th className="py-1.5 pr-3 text-right font-medium">Amount</th>
            <th className="py-1.5 pr-3 font-medium">Status</th>
            <th className="py-1.5 font-medium">Checks</th>
          </tr>
        </thead>
        <tbody className="divide-y divide-border-soft">
          {rows.map((i) => (
            <tr key={i.id} aria-selected={openId === i.id}
              className={`cursor-pointer hover:bg-border-soft/40 ${openId === i.id ? "bg-border-soft/60" : ""}`}
              onClick={() => onOpen(i.id)}>
              <td className="py-1.5 pr-3">
                <div className="text-ink">{i.number ?? "Draft"}{i.kind === "credit_note" ? " · credit note" : ""}</div>
                <div className="text-xs text-ink-muted">{i.household_name ?? i.mandate_name}</div>
              </td>
              <td className="py-1.5 pr-3 font-mono text-xs text-ink-muted whitespace-nowrap">{i.period_start} → {i.period_end}</td>
              <td className={`py-1.5 pr-3 text-right font-mono tabular-nums whitespace-nowrap ${i.amount < 0 ? "text-ok" : "text-ink"}`}>{money(i.amount, i.currency)}</td>
              <td className="py-1.5 pr-3 whitespace-nowrap">
                <span className={`font-mono text-xs ${i.status === "held" ? "text-crit" : i.status === "debited" ? "text-ok" : "text-ink-muted"}`}>{i.status}</span>
                {i.debit_status === "insufficient_cash" && <div className="font-mono text-[10px] text-warn">unpaid · short {i.shortfall?.toLocaleString()}</div>}
              </td>
              <td className="py-1.5"><Checks checks={i.checks ?? []} /></td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

/** Fees due, the run waiting on a decision, and what is wrong with any bill. In the work pane,
 * preparing a run and crediting an invoice are the conversation's run_billing and credit_invoice,
 * behind its Confirm card; deciding the run is a decision, as on any proposal card. */
export function BillingView({ focusInvoice = null }: { focusInvoice?: string | null }) {
  const nav = useViewNav();
  const { data, loading, error, refetch } = useApi<any>("/api/billing/overview");
  const role = getUser()?.role as string | undefined;
  const [busy, setBusy] = useState(false);
  const [msg, setMsg] = useState<string | null>(null);
  const [openId, setOpenId] = useState<string | null>(focusInvoice);
  const [acceptStale, setAcceptStale] = useState(false);
  const [reason, setReason] = useState("");
  const { data: detail, refetch: refetchDetail } = useApi<any>(openId ? `/api/billing/invoices/${openId}` : null, [openId]);

  useEffect(() => setOpenId(focusInvoice), [focusInvoice]);

  async function act(fn: () => Promise<any>, done: (r: any) => string) {
    setBusy(true);
    setMsg(null);
    try {
      const r = await fn();
      setMsg(done(r));
      await refetch();
      if (openId) await refetchDetail();
      nav.changed();
    } catch (e: any) {
      setMsg(e?.message ?? "That did not work.");
    } finally {
      setBusy(false);
    }
  }

  if (loading) return <SkeletonList count={5} />;
  if (error || !data) {
    return (
      <div className="rounded-lg border border-border bg-surface p-8">
        <ErrorState what="billing" message={error ?? "No data"} onRetry={refetch} />
      </div>
    );
  }
  const run = data.open_run;
  const ex = data.exceptions;
  const staleHeld = run ? run.invoices.filter((i: any) => i.checks.some((c: any) => c.severity === "block") &&
    i.checks.filter((c: any) => c.severity === "block").every((c: any) => c.code === "stale_valuation")).length : 0;
  const canApprove = !!role && APPROVERS.includes(role);
  const canPropose = !!role && PROPOSERS.includes(role);

  const prepare = () =>
    nav.say
      ? nav.say("Prepare a billing run for the current period.")
      : act(() => api("/api/billing/runs/propose", { method: "POST", body: {} }),
          (r) => (r.proposed ? "Run prepared · awaiting approval" : r.reason));

  const credit = () => {
    if (!detail) return;
    const who = detail.household_name ?? detail.mandate_name;
    if (nav.say) {
      nav.say(withRef(`Credit invoice ${detail.number} for ${who}. Reason: ${reason.trim()}`, "invoice", detail.id));
      setReason("");
      return;
    }
    act(() => api(`/api/billing/invoices/${detail.id}/credit`, { method: "POST", body: { reason } }),
      (r) => `Credited · ${r.number}`);
  };

  return (
    <div className="view-container">
      <ViewHeader
        title="Billing"
        meta={
          <>
            {run && <span className="chip bg-warn-bg text-warn" data-testid="billing-run-waiting">Run awaiting approval · {run.as_of}</span>}
            {ex.held.length > 0 && <span className="chip bg-crit-bg text-crit">{ex.held.length} held</span>}
            {ex.unpaid.length > 0 && <span className="chip bg-warn-bg text-warn">{ex.unpaid.length} unpaid</span>}
            {ex.no_agreement.length > 0 && <span className={chip}>{ex.no_agreement.length} without agreement</span>}
          </>
        }
        actions={
          <button className="btn-primary" data-testid="billing-prepare-run"
            disabled={busy || !canPropose || !!run}
            title={run ? "A run is already awaiting approval" : undefined}
            onClick={prepare}>
            <Receipt size={15} /> Prepare run
          </button>
        }
      />
      {msg && <div className="mb-4 font-mono text-xs text-ink-muted" role="status">{msg}</div>}

      <div className="mb-6 grid grid-cols-2 gap-3 cq-md:grid-cols-5">
        {[
          ["Billed this year", totals(data.billed_this_year), ""],
          ["Awaiting approval", run ? totals(run.summary?.amount_by_currency) : "—", run ? "text-warn" : ""],
          ["No agreement", String(ex.no_agreement.length), ex.no_agreement.length ? "text-warn" : ""],
          ["Held", String(ex.held.length), ex.held.length ? "text-crit" : ""],
          ["Unpaid", String(ex.unpaid.length), ex.unpaid.length ? "text-warn" : ""],
        ].map(([k, v, tone]) => (
          <div key={k} className="card p-4">
            <div className="tile-label mb-1">{k}</div>
            <div className={`font-mono text-lg tabular-nums ${tone || "text-ink"}`}>{v}</div>
          </div>
        ))}
      </div>

      <div className="space-y-5">
        {run && (
          <Card data-testid="billing-run">
            <div className="mb-3 flex flex-wrap items-center justify-between gap-3">
              <div className="font-semibold text-ink">Run awaiting approval · {run.as_of}</div>
              {run.awaiting_decision && run.recommendation_id && (
                <div className="flex flex-wrap items-center gap-2">
                  {!canApprove && (
                    <span className="chip text-warn" data-testid="billing-approval-rights">
                      Approval: operations or branch leader · you are {label(role ?? "unknown")}
                    </span>
                  )}
                  {staleHeld > 0 && (
                    <label className="flex items-center gap-1.5 text-xs text-ink-muted">
                      <input type="checkbox" checked={acceptStale} onChange={(e) => setAcceptStale(e.target.checked)} />
                      Accept stale valuation on {staleHeld} held bill(s)
                    </label>
                  )}
                  <button className="btn-primary" data-testid="billing-approve-run" disabled={busy || !canApprove}
                    title={canApprove ? undefined : "Only operations or a branch leader may approve a billing run"}
                    onClick={() => act(
                      async () => {
                        await api(`/api/studio/recommendations/${run.recommendation_id}/decide`, {
                          method: "POST",
                          body: acceptStale
                            ? { action: "modify", modified_payload: { acknowledge: ["stale_valuation"] } }
                            : { action: "approve" },
                        });
                        return api(`/api/billing/runs/${run.id}`);
                      },
                      (r) => {
                        const x = r.summary?.result;
                        return x ? `Issued ${x.issued} · paid from cash ${x.debited} · unpaid ${x.insufficient_cash} · held ${x.held}` : "Decision recorded";
                      })}>
                    {acceptStale ? "Approve and accept stale" : "Approve run"}
                  </button>
                  <button className="btn-outline" data-testid="billing-dismiss-run" disabled={busy || !role || !VETO.includes(role)}
                    onClick={() => act(
                      () => api(`/api/studio/recommendations/${run.recommendation_id}/decide`, { method: "POST", body: { action: "dismiss" } }),
                      () => "Run dismissed")}>
                    Dismiss
                  </button>
                </div>
              )}
            </div>
            <InvoiceTable rows={run.invoices} onOpen={setOpenId} openId={openId} testId="billing-run-table" />
          </Card>
        )}

        {detail && (
          <div className="space-y-2" data-testid="billing-invoice-detail">
            <InvoiceCard result={detail} />
            {(detail.status === "issued" || detail.status === "debited") && detail.kind === "invoice" && (
              <div className="flex flex-wrap items-center gap-2">
                <input className="input w-72 max-w-full text-xs" placeholder="Reason for crediting" aria-label="Reason for crediting"
                  value={reason} onChange={(e) => setReason(e.target.value)} />
                <button className="btn-outline" data-testid="billing-credit-invoice" disabled={busy || !reason.trim() || !canApprove}
                  title={canApprove ? undefined : "Only operations or a branch leader may credit an invoice"}
                  onClick={credit}>
                  Credit invoice
                </button>
              </div>
            )}
          </div>
        )}

        <Card data-testid="billing-exceptions">
          <div className="mb-3 font-semibold text-ink">Exceptions</div>
          <div className="grid gap-5 cq-lg:grid-cols-3">
            <div>
              <div className="tile-label mb-2">No agreement on file</div>
              {ex.no_agreement.length === 0 ? <div className="font-mono text-xs text-ink-muted">None</div> : (
                <div className="space-y-1">
                  {ex.no_agreement.map((u: any) => (
                    <div key={u.mandate_id} className="flex justify-between gap-3 text-sm">
                      <span className="text-ink">{u.household_name ?? u.mandate_name}{u.pending_confirmation ? " · awaiting confirmation" : ""}</span>
                      <span className="font-mono tabular-nums text-ink-muted">{u.value.toLocaleString(undefined, { maximumFractionDigits: 0 })}</span>
                    </div>
                  ))}
                </div>
              )}
            </div>
            <div>
              <div className="tile-label mb-2">Held by a block</div>
              {ex.held.length === 0 ? <div className="font-mono text-xs text-ink-muted">None</div> : (
                <InvoiceTable rows={ex.held} onOpen={setOpenId} openId={openId} />
              )}
            </div>
            <div>
              <div className="tile-label mb-2">Issued, unpaid for lack of cash</div>
              {ex.unpaid.length === 0 ? <div className="font-mono text-xs text-ink-muted">None</div> : (
                <InvoiceTable rows={ex.unpaid} onOpen={setOpenId} openId={openId} />
              )}
            </div>
          </div>
        </Card>

        <Card>
          <div className="mb-3 font-semibold text-ink">Recent runs</div>
          {data.recent_runs.length === 0 ? <Empty>No runs yet.</Empty> : (
            <div className="overflow-x-auto">
              <table className="w-full text-sm">
                <thead>
                  <tr className="border-b border-border-soft text-left text-[10px] uppercase tracking-wide text-ink-muted">
                    <th className="py-1.5 pr-3 font-medium">As of</th>
                    <th className="py-1.5 pr-3 font-medium">Status</th>
                    <th className="py-1.5 pr-3 text-right font-medium">Bills</th>
                    <th className="py-1.5 pr-3 font-medium">Issued</th>
                    <th className="py-1.5 font-medium">Approved by</th>
                  </tr>
                </thead>
                <tbody className="divide-y divide-border-soft">
                  {data.recent_runs.map((r: any) => (
                    <tr key={r.id}>
                      <td className="py-1.5 pr-3 font-mono text-xs text-ink">{r.as_of}</td>
                      <td className="py-1.5 pr-3 font-mono text-xs text-ink-muted">{r.status}</td>
                      <td className="py-1.5 pr-3 text-right font-mono tabular-nums text-ink-muted">{r.summary?.invoices ?? "—"}</td>
                      <td className="py-1.5 pr-3 font-mono text-xs tabular-nums text-ink">{totals(r.summary?.result?.issued_by_currency)}</td>
                      <td className="py-1.5 text-xs text-ink-muted">{r.approved_by ?? "—"}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}
        </Card>
      </div>
    </div>
  );
}
