"use client";

import { Card, Empty, ErrorState, SkeletonList } from "@/components/ui";
import { useApi } from "@/lib/hooks";
import { money } from "@/lib/format";
import { reviewDay } from "@/app/workspace/components/artifacts/ReviewsCard";
import { ViewHeader } from "./ViewHeader";

const th = "px-4 py-2 font-medium";

/** Money coming in: deposits and their holds, returns by code, bank links, and standing instructions due to be revalidated. */
export function MoneyInView() {
  const { data, loading, error, refetch } = useApi<any>("/api/money-in", []);
  if (error) return <div className="view-container"><Card><ErrorState what="money in" message={error} onRetry={refetch} /></Card></div>;
  if (loading || !data) return <div className="view-container"><SkeletonList count={4} /></div>;
  const s = data.summary;
  const p = data.policy;
  return (
    <div className="view-container">
      <ViewHeader title="Money in" />
      <div className="mb-4 grid grid-cols-2 gap-3 sm:grid-cols-4" data-testid="money-in-summary">
        {[["On hold", money(s.on_hold)], ["Returned", s.returned], ["Instructions overdue", s.instructions_overdue], ["Bank links verified", s.links_verified]].map(([label, n]) => (
          <Card key={label as string}><div className="text-[10px] uppercase tracking-wide text-ink-muted">{label}</div><div className="font-mono text-xl text-ink">{n as any}</div></Card>
        ))}
      </div>
      <Card className="mb-4"><div className="flex flex-wrap gap-1.5" data-testid="money-in-policy">
        <span className={`chip ${p.set_by_firm ? "" : "text-warn"}`}>{p.set_by_firm ? "Firm policy" : "Platform default policy"}</span>
        <span className="chip">Hold from {money(p.deposit_hold_threshold)}</span>
        {Object.entries(p.deposit_hold_days as Record<string, number>).map(([m, d]) => <span key={m} className="chip">{m.toUpperCase()} {d} business days</span>)}
        <span className="chip">Revalidate every {p.instruction_revalidate_months} months</span>
        {data.returns_by_code.map((r: any) => <span key={r.code} className="chip text-warn">{r.code} {r.reason} · {r.count}</span>)}
      </div></Card>
      <Card className="mb-4 overflow-hidden p-0">
        <div className="border-b border-border-soft px-4 py-2 text-sm font-medium text-ink">Deposits</div>
        {data.deposits.length === 0 ? <div className="px-4 py-3"><Empty>No deposits recorded.</Empty></div> : (
          <div className="overflow-x-auto"><table className="w-full text-sm" data-testid="money-in-deposits">
            <thead><tr className="text-left text-[10px] uppercase tracking-wide text-ink-muted"><th className={th}>Client</th><th className={th}>Account</th><th className={th}>By</th><th className={`${th} text-right`}>Amount</th><th className={th}>Received</th><th className={th}>State</th></tr></thead>
            <tbody className="divide-y divide-border-soft">
              {data.deposits.map((d: any) => (
                <tr key={d.id}>
                  <td className="px-4 py-2 text-ink">{d.client}</td>
                  <td className="px-4 py-2 text-xs text-ink-muted">{d.account}</td>
                  <td className="px-4 py-2 font-mono text-xs uppercase text-ink-muted">{d.method}</td>
                  <td className="px-4 py-2 text-right font-mono text-xs">{money(d.amount)}</td>
                  <td className="px-4 py-2 font-mono text-xs text-ink-muted">{reviewDay(d.received_on)}</td>
                  <td className="px-4 py-2"><div className="flex flex-wrap gap-1">
                    {d.returned_on ? <span className="chip text-critical">{d.return_code} {d.return_reason}</span>
                      : d.on_hold ? <span className="chip text-warn">held to {reviewDay(d.hold_until)}</span> : <span className="chip text-ok">available</span>}
                    {d.may_have_been_invested && <span className="chip text-critical">may have been invested</span>}
                  </div></td>
                </tr>
              ))}
            </tbody>
          </table></div>
        )}
      </Card>
      <Card className="overflow-hidden p-0">
        <div className="border-b border-border-soft px-4 py-2 text-sm font-medium text-ink">Standing instructions</div>
        {data.instructions.length === 0 ? <div className="px-4 py-3"><Empty>No standing instructions.</Empty></div> : (
          <div className="overflow-x-auto"><table className="w-full text-sm" data-testid="money-in-instructions">
            <thead><tr className="text-left text-[10px] uppercase tracking-wide text-ink-muted"><th className={th}>Client</th><th className={th}>Pays</th><th className={`${th} text-right`}>Amount</th><th className={th}>Last confirmed</th><th className={th}>Due</th></tr></thead>
            <tbody className="divide-y divide-border-soft">
              {data.instructions.map((r: any) => (
                <tr key={r.id}>
                  <td className="px-4 py-2 text-ink">{r.client}</td>
                  <td className="px-4 py-2 text-xs text-ink-muted">{r.payee}{r.third_party ? " · third party" : ""}</td>
                  <td className="px-4 py-2 text-right font-mono text-xs">{money(r.amount)} {r.frequency}</td>
                  <td className="px-4 py-2 font-mono text-xs text-ink-muted">{r.last_checked ? reviewDay(r.last_checked) : "never"}</td>
                  <td className="px-4 py-2"><span className={`chip ${r.overdue ? "text-critical" : ""}`}>{reviewDay(r.due)}</span></td>
                </tr>
              ))}
            </tbody>
          </table></div>
        )}
      </Card>
    </div>
  );
}
