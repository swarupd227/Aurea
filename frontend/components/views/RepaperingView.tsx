"use client";

import { Card, Empty, ErrorState, SkeletonList } from "@/components/ui";
import { useApi } from "@/lib/hooks";
import { reviewDay } from "@/app/workspace/components/artifacts/ReviewsCard";
import { ViewHeader } from "./ViewHeader";

const th = "px-4 py-2 font-medium";
export const repaperTone: Record<string, string> = { consented: "text-ok", deemed_consent: "text-ok", objected: "text-critical", notice_bounced: "text-critical",
  in_notice_period: "text-warn", awaiting_signature: "text-warn", not_sent: "text-ink-muted" };

/** Bulk repapering: each campaign's consent rule, its dates, and where every household stands. */
export function RepaperingView() {
  const { data, loading, error, refetch } = useApi<any>("/api/repapering", []);
  if (error) return <div className="view-container"><Card><ErrorState what="repapering" message={error} onRetry={refetch} /></Card></div>;
  if (loading || !data) return <div className="view-container"><SkeletonList count={3} /></div>;
  return (
    <div className="view-container">
      <ViewHeader title="Repapering" />
      <Card className="mb-4"><div className="flex flex-wrap gap-1.5" data-testid="repapering-policy">
        {Object.entries(data.policy as Record<string, any>).map(([k, r]) => (
          <span key={k} className="chip">{r.label} · {r.consent === "negative" ? `silence after ${r.notice_days}d` : "signature"}{r.set_by_firm ? " · firm" : ""}</span>
        ))}
      </div></Card>
      {data.campaigns.length === 0 ? <Empty>No campaigns.</Empty> : data.campaigns.map((c: any) => (
        <Card key={c.id} className="mb-4 overflow-hidden p-0">
          <div className="flex flex-wrap items-center justify-between gap-2 border-b border-border-soft px-4 py-2">
            <div className="text-sm font-medium text-ink">{c.name}</div>
            <div className="flex flex-wrap gap-1.5">
              <span className="chip">{c.label}</span>
              <span className="chip">{c.consent}</span>
              {c.sent_on ? <span className="chip">sent {reviewDay(c.sent_on)}</span> : <span className="chip text-warn">not sent</span>}
              {c.effective_on && <span className="chip">effective {reviewDay(c.effective_on)}</span>}
              <span className="chip text-ok">{c.ready} of {c.total} ready</span>
            </div>
          </div>
          <div className="overflow-x-auto"><table className="w-full text-sm" data-testid="repapering-households">
            <thead><tr className="text-left text-[10px] uppercase tracking-wide text-ink-muted"><th className={th}>Household</th><th className={th}>Stands</th><th className={th}>Last answer</th></tr></thead>
            <tbody className="divide-y divide-border-soft">
              {c.households.map((h: any) => {
                const last = h.steps[h.steps.length - 1];
                return (
                  <tr key={h.household_id}>
                    <td className="px-4 py-2 text-ink">{h.household}</td>
                    <td className="px-4 py-2"><span className={`chip ${repaperTone[h.state] ?? ""}`}>{h.state.replace(/_/g, " ")}</span></td>
                    <td className="px-4 py-2 text-xs text-ink-muted">{last ? `${reviewDay(last.on)} · ${last.evidence}` : "—"}</td>
                  </tr>
                );
              })}
            </tbody>
          </table></div>
        </Card>
      ))}
    </div>
  );
}
