"use client";

import { useState } from "react";
import { Card, Empty, ErrorState, SkeletonList } from "@/components/ui";
import { useApi } from "@/lib/hooks";
import { withRef } from "@/lib/refs";
import { reviewDay } from "@/app/workspace/components/artifacts/ReviewsCard";
import { useViewNav } from "./ViewNav";
import { ViewHeader } from "./ViewHeader";

const th = "px-4 py-2 font-medium";
const STATE: Record<string, [string, string]> = {
  open: ["open", "text-warn"], needs_info: ["needs info", "text-warn"], awaiting_confirmation: ["awaiting second reviewer", "text-crit"],
  true_match: ["true match", "text-crit"], false_positive: ["false positive", "text-ok"],
};
const EDD: Record<string, [string, string]> = {
  edd_pending: ["awaiting sign-off", "text-warn"], edd_declined: ["declined", "text-crit"], edd_complete: ["signed off", "text-ok"],
};

/** Screening hits and EDD files waiting on a person. Beside the conversation, a decision is a sentence to Astra and
 * its Confirm card; nothing here decides on its own. */
export function AmlReviewsView({ caseId = null }: { caseId?: string | null }) {
  const nav = useViewNav();
  const { data, loading, error, refetch } = useApi<any>(caseId ? `/api/aml?case=${caseId}` : "/api/aml", [caseId]);
  const [open, setOpen] = useState<{ party: string; hit: string; name: string; decision: string; reason: string } | null>(null);
  if (error) return <div className="view-container"><Card><ErrorState what="AML reviews" message={error} onRetry={refetch} /></Card></div>;
  if (loading || !data || (data.case_id ?? null) !== caseId) return <div className="view-container"><SkeletonList count={3} /></div>;
  const s = data.summary;

  function decide(h: any, kind: string, decision: string, reason: string) {
    if (!nav.say) return;
    const verb = kind === "disposition" ? `Record ${decision.replace("_", " ")}` : kind === "confirmation" ? `Record the confirmation (${decision})` : `Record QA ${decision}`;
    nav.say(withRef(`${verb} on ${h.matched_name} for ${h.party}. Reason: ${reason.trim()}`, "party", h.party_id));
    setOpen(null);
  }

  return (
    <div className="view-container">
      <ViewHeader title={caseId ? `AML reviews · ${data.case}` : "AML reviews"} />
      <div className="mb-4 grid grid-cols-2 gap-3 cq-md:grid-cols-4" data-testid="aml-summary">
        {[["Hits open", s.open, s.open ? "text-warn" : "text-ink-muted"], ["Second review", s.awaiting_confirmation, s.awaiting_confirmation ? "text-crit" : "text-ink-muted"],
          ["QA due", s.qa_due, s.qa_due ? "text-warn" : "text-ink-muted"], ["EDD to sign off", s.edd_pending, s.edd_pending ? "text-warn" : "text-ink-muted"]].map(([label, v, tone]: any) => (
          <Card key={label}><div className="tile-label">{label}</div><div className={`font-mono text-2xl tabular-nums ${tone}`}>{v}</div></Card>
        ))}
      </div>
      <Card className="mb-4"><div className="flex flex-wrap gap-1.5">
        <span className="chip text-ink-muted">screening provider is a mock</span>
        <span className="chip text-ink-muted">false positives {s.false_positive_pct == null ? "—" : `${s.false_positive_pct}%`}</span>
        <span className="chip text-ink-muted">QA overturned {s.qa_overturn_pct == null ? "—" : `${s.qa_overturn_pct}%`}</span>
        {!nav.say && <span className="chip text-ink-muted">decide beside the conversation</span>}
      </div></Card>

      {data.hits.length === 0 ? <Empty>No screening hits.</Empty> : (
        <Card className="mb-4 overflow-hidden p-0"><div className="overflow-x-auto"><table className="w-full text-sm" data-testid="aml-hits">
          <thead><tr className="text-left text-[10px] uppercase tracking-wide text-ink-muted"><th className={th}>Party</th><th className={th}>Hit</th><th className={th}>Birth date</th><th className={th}>State</th><th className={`${th} text-right`}>Decide</th></tr></thead>
          <tbody className="divide-y divide-border-soft">
            {data.hits.map((h: any) => {
              const [label, tone] = STATE[h.state] ?? [h.state, "text-ink-muted"];
              const mine = open && open.party === h.party_id && open.hit === h.hit;
              const kind = h.state === "awaiting_confirmation" ? "confirmation" : h.qa_due ? "qa" : "disposition";
              const choices = kind === "confirmation" ? ["confirmed", "rejected"] : kind === "qa" ? ["agree", "overturn"] : ["false_positive", "true_match", "needs_info"];
              const decidable = ["open", "needs_info", "awaiting_confirmation"].includes(h.state) || h.qa_due;
              return (
                <tr key={`${h.party_id}-${h.hit}`} title={h.decision?.rationale || h.note || undefined}>
                  <td className="px-4 py-2"><div className="text-ink">{h.party}</div><div className="text-xs text-ink-muted">{h.case} · {h.role.replace(/_/g, " ")}</div></td>
                  <td className="px-4 py-2"><span className="chip text-ink-muted">{h.category.replace(/_/g, " ")}</span> <span className="text-xs text-ink">{h.matched_name}</span> <span className="font-mono text-xs text-ink-muted">{h.score}</span></td>
                  <td className="px-4 py-2 font-mono text-xs text-ink-muted">{h.discriminators?.dob ?? "unknown"}</td>
                  <td className="px-4 py-2"><span className={`chip ${tone}`}>{label}</span>{h.qa_due && <span className="chip ml-1 text-warn">QA due</span>}</td>
                  <td className="px-4 py-2 text-right">
                    {decidable && !mine && nav.say && <button className="btn-outline text-xs" data-testid="aml-decide" onClick={() => setOpen({ party: h.party_id, hit: h.hit, name: h.matched_name, decision: choices[0], reason: "" })}>Decide</button>}
                    {mine && open && (
                      <div className="flex flex-wrap items-center justify-end gap-1.5" data-testid="aml-form">
                        <select className="input text-xs" aria-label="Decision" value={open.decision} onChange={(e) => setOpen({ ...open, decision: e.target.value })}>
                          {choices.map((c) => <option key={c} value={c}>{c.replace(/_/g, " ")}</option>)}
                        </select>
                        <input className="input text-xs" aria-label="Reason" placeholder="Reason" value={open.reason} onChange={(e) => setOpen({ ...open, reason: e.target.value })} />
                        <button className="btn-primary text-xs" disabled={open.reason.trim().length < 5} onClick={() => decide(h, kind, open.decision, open.reason)}>Ask Astra</button>
                        <button className="btn-outline text-xs" onClick={() => setOpen(null)}>Not now</button>
                      </div>
                    )}
                  </td>
                </tr>
              );
            })}
          </tbody>
        </table></div></Card>
      )}

      {data.edd.length > 0 && (
        <Card className="overflow-hidden p-0"><div className="overflow-x-auto"><table className="w-full text-sm" data-testid="aml-edd">
          <thead><tr className="text-left text-[10px] uppercase tracking-wide text-ink-muted"><th className={th}>Case</th><th className={th}>Risk</th><th className={th}>Wealth</th><th className={th}>Status</th><th className={th}>Last review</th></tr></thead>
          <tbody className="divide-y divide-border-soft">
            {data.edd.map((e: any) => {
              const [label, tone] = EDD[e.edd_status] ?? [e.edd_status, "text-ink-muted"];
              return (
                <tr key={e.case_id} title={e.last_review?.rationale || undefined}>
                  <td className="px-4 py-2 text-ink">{e.case}</td>
                  <td className="px-4 py-2"><span className={`chip ${e.risk_tier === "high" ? "text-crit" : "text-warn"}`}>{e.risk_tier ?? "not rated"}</span></td>
                  <td className="px-4 py-2 text-xs text-ink-muted">{e.last_review?.wealth_type?.replace(/_/g, " ") ?? e.source_of_wealth ?? "—"}</td>
                  <td className="px-4 py-2"><span className={`chip ${tone}`}>{label}</span>{e.memo_gaps.length > 0 && <span className="chip ml-1 text-ink-muted">{e.memo_gaps.length} gaps in memo</span>}</td>
                  <td className="px-4 py-2 font-mono text-xs text-ink-muted">{e.last_review ? `${e.last_review.decision} · ${e.last_review.by} · ${reviewDay(e.last_review.at.slice(0, 10))}` : "—"}</td>
                </tr>
              );
            })}
          </tbody>
        </table></div></Card>
      )}
    </div>
  );
}
