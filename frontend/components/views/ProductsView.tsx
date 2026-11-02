"use client";

import { useState } from "react";
import { Card, Empty, ErrorState, SkeletonList } from "@/components/ui";
import { api } from "@/lib/api";
import { money } from "@/lib/format";
import { useApi } from "@/lib/hooks";
import { reviewDay } from "@/app/workspace/components/artifacts/ReviewsCard";
import { ProductChips, type Product } from "@/app/workspace/components/artifacts/ShelfCard";
import { useViewNav } from "./ViewNav";
import { ViewHeader } from "./ViewHeader";

interface Panel {
  symbol: string;
  status: string;
  rationale: string;
  document: string;
  next_review_on: string;
  minimum_class: string;
}

/** The firm's product shelf. In the work pane a decision is the conversation's tool behind its Confirm
 * card; on a page the form acts directly. */
export function ProductsView() {
  const nav = useViewNav();
  const { data, loading, error, refetch } = useApi<any>("/api/shelf");
  const [panel, setPanel] = useState<Panel | null>(null);
  const [busy, setBusy] = useState(false);
  const [msg, setMsg] = useState<string | null>(null);

  if (error) return <div className="view-container"><Card><ErrorState what="the product shelf" message={error} onRetry={refetch} /></Card></div>;
  if (loading || !data) return <div className="view-container"><SkeletonList count={4} /></div>;
  const s = data.summary;
  const rows: Product[] = data.products;
  const ready = !!panel && panel.rationale.trim().length >= 5 && panel.document.trim().length >= 5;

  async function act(say: string, path: string, body: any) {
    if (nav.say) {
      nav.say(say);
      setPanel(null);
      return;
    }
    setBusy(true);
    setMsg(null);
    try {
      await api<any>(path, { method: "POST", body });
      setPanel(null);
      await refetch();
    } catch (e: any) {
      setMsg(e?.message ?? "That did not work.");
    } finally {
      setBusy(false);
    }
  }

  const decide = () => panel && act(
    `Record ${panel.symbol} as ${panel.status}: ${panel.rationale.trim()}. From "${panel.document.trim()}".${panel.minimum_class ? ` Only for ${panel.minimum_class.replace("_", " ")} investors.` : ""}${panel.next_review_on ? ` Next review ${panel.next_review_on}.` : ""}`,
    "/api/shelf/decisions",
    { symbol: panel.symbol, status: panel.status, rationale: panel.rationale, document: panel.document, next_review_on: panel.next_review_on || null, minimum_class: panel.minimum_class || null },
  );
  const toggle = () => act(`Turn approved products only ${data.approved_products_only ? "off" : "on"}.`, "/api/shelf/enforcement", { on: !data.approved_products_only });

  return (
    <div className="view-container">
      <ViewHeader title="Product shelf" actions={data.can_switch ? (
        <button className="btn-outline text-xs" data-testid="shelf-enforce" disabled={busy} onClick={toggle}>
          Approved only {data.approved_products_only ? "on" : "off"}
        </button>
      ) : undefined} />
      {msg && <div className="mb-4 font-mono text-xs text-ink-muted" role="status" data-testid="shelf-status">{msg}</div>}

      <div className="mb-4 grid grid-cols-2 gap-3 cq-md:grid-cols-4" data-testid="shelf-summary">
        {[["Approved", s.approved, "text-ok"], ["Under watch", s.watch, s.watch ? "text-warn" : "text-ink-muted"], ["Closed, still held", s.held_while_closed, s.held_while_closed ? "text-crit" : "text-ink-muted"],
          ["No decision", s.not_reviewed, "text-ink-muted"]].map(([label, v, tone]: any) => (
          <Card key={label}>
            <div className="tile-label">{label}</div>
            <div className={`font-mono text-2xl tabular-nums ${tone}`}>{v}</div>
          </Card>
        ))}
      </div>

      <Card className="mb-4">
        <div className="flex flex-wrap gap-1.5">
          <span className="chip text-ink-muted">approved products only {data.approved_products_only ? "on" : "off"}</span>
          <span className="chip text-ink-muted">closed stops every purchase</span>
          <span className="chip text-ink-muted">no decision does not mean never reviewed</span>
          {s.reviews_overdue > 0 && <span className="chip text-warn">{s.reviews_overdue} reviews overdue</span>}
          {s.dearer_class > 0 && <span className="chip text-warn">{s.dearer_class} held in a dearer class</span>}
        </div>
      </Card>

      {rows.length === 0 ? <Empty>No products.</Empty> : (
        <Card className="overflow-hidden p-0">
          <div className="overflow-x-auto">
            <table className="w-full text-sm" data-testid="shelf-rows">
              <thead>
                <tr className="text-left text-[10px] uppercase tracking-wide text-ink-muted">
                  <th className="px-4 py-2 font-medium">Product</th>
                  <th className="px-4 py-2 text-right font-medium">Held</th>
                  <th className="px-4 py-2 font-medium">Status</th>
                  <th className="px-4 py-2 font-medium">Next review</th>
                  <th className="px-4 py-2 text-right font-medium">Act</th>
                </tr>
              </thead>
              <tbody className="divide-y divide-border-soft">
                {rows.map((p) => {
                  const open = panel?.symbol === p.symbol ? panel : null;
                  return (
                    <tr key={p.symbol} title={p.document ?? undefined}>
                      <td className="px-4 py-2">
                        <span className="font-mono text-xs text-ink">{p.symbol}</span>
                        <span className="ml-2 text-xs text-ink-muted">{p.name}</span>
                        {p.share_class && <span className="ml-1 text-xs text-ink-muted">· {p.share_class}</span>}
                        {p.expense_ratio != null && <span className="ml-1 font-mono text-xs text-ink-muted">{(p.expense_ratio * 100).toFixed(2)}%</span>}
                      </td>
                      <td className="px-4 py-2 text-right font-mono text-xs tabular-nums text-ink-muted">{money(p.value_held)}</td>
                      <td className="px-4 py-2">
                        <ProductChips product={p} />
                        {open && (
                          <div className="mt-2 flex flex-wrap items-center gap-2" data-testid="shelf-form">
                            <select className="input text-xs" aria-label="Status" value={open.status} onChange={(e) => setPanel({ ...open, status: e.target.value })}>
                              {["approved", "watch", "closed"].map((v) => <option key={v} value={v}>{v}</option>)}
                            </select>
                            <select className="input text-xs" aria-label="Investors" value={open.minimum_class} onChange={(e) => setPanel({ ...open, minimum_class: e.target.value })}>
                              <option value="">Any investor</option>
                              <option value="accredited">Accredited</option>
                              <option value="qualified_purchaser">Qualified purchaser</option>
                            </select>
                            <input type="date" className="input text-xs" aria-label="Next review" value={open.next_review_on} min={data.as_of} onChange={(e) => setPanel({ ...open, next_review_on: e.target.value })} />
                            <input className="input min-w-44 flex-1 text-xs" aria-label="Reason" placeholder="Reason" value={open.rationale} onChange={(e) => setPanel({ ...open, rationale: e.target.value })} />
                            <input className="input min-w-44 flex-1 text-xs" aria-label="Document" placeholder="Due-diligence paper" value={open.document} onChange={(e) => setPanel({ ...open, document: e.target.value })} />
                            <button className="btn-primary text-xs" data-testid="shelf-confirm" disabled={busy || !ready} onClick={decide}>{nav.say ? "Ask Astra" : "Confirm"}</button>
                            <button className="btn-outline text-xs" disabled={busy} onClick={() => setPanel(null)}>Not now</button>
                          </div>
                        )}
                      </td>
                      <td className="px-4 py-2 font-mono text-xs text-ink-muted">{p.next_review_on ? reviewDay(p.next_review_on) : "—"}</td>
                      <td className="px-4 py-2 text-right">
                        {data.can_decide && !open && (
                          <button className="btn-outline text-xs" data-testid="shelf-decide" onClick={() => setPanel({
                            symbol: p.symbol, status: p.status === "not_reviewed" ? "approved" : p.status, rationale: "", document: "", next_review_on: "", minimum_class: p.minimum_class ?? "",
                          })}>Decide</button>
                        )}
                      </td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
          </div>
        </Card>
      )}
    </div>
  );
}
