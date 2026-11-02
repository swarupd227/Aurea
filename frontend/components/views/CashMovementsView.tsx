"use client";

import { Card, Empty, ErrorState, SkeletonList } from "@/components/ui";
import { money } from "@/lib/format";
import { useApi } from "@/lib/hooks";
import { reviewDay } from "@/app/workspace/components/artifacts/ReviewsCard";
import { AllClients, ViewHeader } from "./ViewHeader";

const th = "px-4 py-2 font-medium";
const num = "px-4 py-2 text-right font-mono text-xs tabular-nums text-ink-muted";
const TONE: Record<string, string> = { requested: "text-warn", paid: "text-ok", cancelled: "text-ink-muted" };

/** Payments out, standing instructions, required distributions and phased money. Each is done in the conversation,
 * because a request names a person and a confirmation names a document. */
export function CashMovementsView({ household = null }: { household?: string | null }) {
  const { data, loading, error, refetch } = useApi<any>(household ? `/api/cash-movements?household=${household}` : "/api/cash-movements", [household]);
  if (error) return <div className="view-container"><Card><ErrorState what="cash movements" message={error} onRetry={refetch} /></Card></div>;
  if (loading || !data || (data.household_id ?? null) !== household) return <div className="view-container"><SkeletonList count={3} /></div>;
  const s = data.summary;
  const nothing = !data.requests.length && !data.standing.length && !data.rmds.length && !data.phasing.length;
  return (
    <div className="view-container">
      <ViewHeader title="Cash movements" actions={household ? <AllClients href="/studio/cash" /> : undefined} />
      <div className="mb-4 grid grid-cols-2 gap-3 cq-md:grid-cols-4" data-testid="cash-summary">
        {[["Open", s.open, "text-ink"], ["To pay", money(s.value_open), "text-ink-muted"], ["Short of cash", s.short, s.short ? "text-crit" : "text-ink-muted"], ["RMD left this year", money(s.rmd_left), s.rmd_left ? "text-warn" : "text-ink-muted"]].map(([label, v, tone]: any) => (
          <Card key={label}><div className="tile-label">{label}</div><div className={`font-mono text-lg tabular-nums ${tone}`}>{v}</div></Card>
        ))}
      </div>
      <Card className="mb-4"><div className="flex flex-wrap gap-1.5">
        <span className="chip text-ink-muted">one person asks, another records</span>
        <span className="chip text-ink-muted">third party needs a callback</span>
        <span className="chip text-ink-muted">cash for an open request is held back</span>
      </div></Card>
      {nothing ? <Empty>None on file.</Empty> : (
        <div className="space-y-4">
          {data.requests.length > 0 && (
            <Card className="overflow-hidden p-0"><div className="overflow-x-auto"><table className="w-full text-sm" data-testid="cash-requests">
              <thead><tr className="text-left text-[10px] uppercase tracking-wide text-ink-muted"><th className={th}>Client</th><th className={th}>To</th><th className={`${th} text-right`}>Amount</th><th className={th}>Status</th></tr></thead>
              <tbody className="divide-y divide-border-soft">
                {data.requests.map((r: any) => (
                  <tr key={r.id} title={r.reason}>
                    <td className="px-4 py-2 text-ink">{r.client}<span className="ml-1 text-xs text-ink-muted">{r.account}</span></td>
                    <td className="px-4 py-2 text-xs text-ink-muted">{r.payee}{r.third_party && <span className="chip ml-1.5 text-warn">third party</span>}</td>
                    <td className={num}>{money(r.amount)}</td>
                    <td className="px-4 py-2"><div className="flex flex-wrap gap-1.5"><span className={`chip ${TONE[r.status]}`}>{r.status}</span>{r.short_by > 0 && <span className="chip text-crit">short {money(r.short_by)}</span>}</div></td>
                  </tr>
                ))}
              </tbody>
            </table></div></Card>
          )}
          {data.rmds.length > 0 && (
            <Card className="overflow-hidden p-0"><div className="overflow-x-auto"><table className="w-full text-sm" data-testid="cash-rmds">
              <thead><tr className="text-left text-[10px] uppercase tracking-wide text-ink-muted"><th className={th}>Required distribution</th><th className={`${th} text-right`}>Required</th><th className={`${th} text-right`}>Taken</th><th className={`${th} text-right`}>Left</th><th className={th}>By</th></tr></thead>
              <tbody className="divide-y divide-border-soft">
                {data.rmds.map((r: any) => <tr key={r.account_id}><td className="px-4 py-2 text-ink">{r.account}</td><td className={num}>{money(r.required)}</td><td className={num}>{money(r.taken)}</td><td className={num}>{money(r.left)}</td><td className="px-4 py-2 font-mono text-xs text-ink-muted">{reviewDay(r.by)}</td></tr>)}
              </tbody>
            </table></div></Card>
          )}
          {data.standing.length > 0 && (
            <Card className="overflow-hidden p-0"><div className="overflow-x-auto"><table className="w-full text-sm" data-testid="cash-standing">
              <thead><tr className="text-left text-[10px] uppercase tracking-wide text-ink-muted"><th className={th}>Standing</th><th className={th}>To</th><th className={`${th} text-right`}>Amount</th><th className={th}>Next</th></tr></thead>
              <tbody className="divide-y divide-border-soft">
                {data.standing.map((x: any) => <tr key={x.id} title={x.document}><td className="px-4 py-2 text-ink">{x.client}<span className="ml-1 text-xs text-ink-muted">{x.frequency}</span></td><td className="px-4 py-2 text-xs text-ink-muted">{x.payee}</td><td className={num}>{money(x.amount)}</td><td className="px-4 py-2 font-mono text-xs text-ink-muted">{reviewDay(x.next_due)}</td></tr>)}
              </tbody>
            </table></div></Card>
          )}
          {data.phasing.length > 0 && (
            <Card className="overflow-hidden p-0"><div className="overflow-x-auto"><table className="w-full text-sm" data-testid="cash-phasing">
              <thead><tr className="text-left text-[10px] uppercase tracking-wide text-ink-muted"><th className={th}>Phasing in</th><th className={`${th} text-right`}>Amount</th><th className={`${th} text-right`}>Stages left</th><th className={`${th} text-right`}>Held back</th></tr></thead>
              <tbody className="divide-y divide-border-soft">
                {data.phasing.map((p: any) => <tr key={p.id} title={p.reason}><td className="px-4 py-2 text-ink">{p.client}</td><td className={num}>{money(p.amount)}</td><td className={num}>{p.left} of {p.tranches}</td><td className={num}>{money(p.held_back)}</td></tr>)}
              </tbody>
            </table></div></Card>
          )}
        </div>
      )}
    </div>
  );
}
