"use client";

import { Card, Empty, ErrorState, SkeletonList } from "@/components/ui";
import { money } from "@/lib/format";
import { useApi } from "@/lib/hooks";
import { reviewDay } from "@/app/workspace/components/artifacts/ReviewsCard";
import { AllClients, ViewHeader } from "./ViewHeader";

const th = "px-4 py-2 font-medium";
const num = "px-4 py-2 text-right font-mono text-xs tabular-nums text-ink-muted";
const TONE: Record<string, string> = { received: "text-ok", overdue: "text-crit", awaited: "text-ink-muted" };

/** What was traded, on whose say, and when it settles, with blocks shown so a fair allocation can be seen. */
export function TradesView({ household = null }: { household?: string | null }) {
  const { data, loading, error, refetch } = useApi<any>(household ? `/api/trades?household=${household}` : "/api/trades", [household]);
  if (error) return <div className="view-container"><Card><ErrorState what="trades" message={error} onRetry={refetch} /></Card></div>;
  if (loading || !data || (data.household_id ?? null) !== household) return <div className="view-container"><SkeletonList count={3} /></div>;
  const s = data.summary;
  return (
    <div className="view-container">
      <ViewHeader title="Trades" actions={household ? <AllClients href="/studio/trades" /> : undefined} />
      <div className="mb-4 grid grid-cols-2 gap-3 cq-md:grid-cols-4" data-testid="trades-summary">
        {[["Filled", s.filled, "text-ink"], ["Unsettled", s.unsettled, s.unsettled ? "text-warn" : "text-ink-muted"], ["Direct orders", s.direct, "text-ink-muted"], ["Rejected", s.rejected, s.rejected ? "text-crit" : "text-ink-muted"]].map(([label, v, tone]: any) => (
          <Card key={label}><div className="tile-label">{label}</div><div className={`font-mono text-2xl tabular-nums ${tone}`}>{v}</div></Card>
        ))}
      </div>
      <Card className="mb-4"><div className="flex flex-wrap gap-1.5">
        <span className="chip text-ink-muted">last {data.days} days</span>
        {data.settle_days != null && <span className="chip text-ink-muted">settles T+{data.settle_days}, holidays not modelled</span>}
        {data.paper_venue && <span className="chip text-warn">paper venue: no market reached</span>}
      </div></Card>
      {data.blocks.length > 0 && (
        <Card className="mb-4 overflow-hidden p-0"><div className="overflow-x-auto"><table className="w-full text-sm" data-testid="trades-blocks">
          <thead><tr className="text-left text-[10px] uppercase tracking-wide text-ink-muted"><th className={th}>Block</th><th className={`${th} text-right`}>Accounts</th><th className={`${th} text-right`}>Units</th><th className={`${th} text-right`}>Average</th><th className={th}>Allocation</th></tr></thead>
          <tbody className="divide-y divide-border-soft">
            {data.blocks.map((b: any, i: number) => (
              <tr key={i}><td className="px-4 py-2 font-mono text-xs text-ink">{b.side.toUpperCase()} {b.symbol} {reviewDay(b.trade_date)}</td><td className={num}>{b.accounts}</td><td className={num}>{b.units.toLocaleString()}</td><td className={num}>{b.average_price}</td>
                <td className="px-4 py-2"><span className={`chip ${b.same_price ? "text-ok" : "text-warn"}`}>{b.same_price ? "same price" : "prices differ"}</span></td></tr>
            ))}
          </tbody>
        </table></div></Card>
      )}
      {data.trades.length === 0 ? <Empty>No trades.</Empty> : (
        <Card className="overflow-hidden p-0"><div className="overflow-x-auto"><table className="w-full text-sm" data-testid="trades-rows">
          <thead><tr className="text-left text-[10px] uppercase tracking-wide text-ink-muted"><th className={th}>Confirmation</th><th className={th}>Trade</th><th className={th}>Client</th><th className={`${th} text-right`}>Net</th><th className={th}>Settles</th><th className={th}>Source</th></tr></thead>
          <tbody className="divide-y divide-border-soft">
            {data.trades.map((t: any) => (
              <tr key={t.id} title={t.reason || t.rejected_reason || undefined}>
                <td className="px-4 py-2 font-mono text-xs text-ink-muted">{t.confirmation ?? t.status}</td>
                <td className="px-4 py-2 font-mono text-xs text-ink">{t.side.toUpperCase()} {t.quantity.toLocaleString()} {t.symbol}{t.price ? ` @ ${t.price}` : ""}</td>
                <td className="px-4 py-2 text-xs text-ink-muted">{t.client}</td>
                <td className={num}>{t.net ? money(t.net) : "—"}</td>
                <td className="px-4 py-2"><span className={`chip ${t.settled ? "text-ok" : "text-ink-muted"}`}>{t.settles_on ? reviewDay(t.settles_on) : "—"}</span></td>
                <td className="px-4 py-2"><span className="chip text-ink-muted">{t.source}</span></td>
              </tr>
            ))}
          </tbody>
        </table></div></Card>
      )}
    </div>
  );
}

/** What a tax year should produce, whether it has arrived, and the figures behind it. It is never the official form. */
export function TaxDocumentsView({ household = null }: { household?: string | null }) {
  const { data, loading, error, refetch } = useApi<any>(household ? `/api/tax-documents?household=${household}` : "/api/tax-documents", [household]);
  if (error) return <div className="view-container"><Card><ErrorState what="tax documents" message={error} onRetry={refetch} /></Card></div>;
  if (loading || !data || (data.household_id ?? null) !== household) return <div className="view-container"><SkeletonList count={3} /></div>;
  const rows: any[] = household ? [data] : data.households;
  return (
    <div className="view-container">
      <ViewHeader title={household ? `Tax documents ${data.tax_year_label} · ${data.household}` : `Tax documents ${data.tax_year_label}`} actions={household ? <AllClients href="/studio/tax-documents" /> : undefined} />
      <Card className="mb-4"><div className="flex flex-wrap gap-1.5"><span className="chip text-warn">a summary from the book, not the official form</span><span className="chip text-ink-muted">not on file does not mean not issued</span></div></Card>
      {rows.every((r) => !r.expected.length && !r.summaries.length) ? <Empty>Nothing expected this year.</Empty> : (
        <div className="space-y-4" data-testid="tax-documents-rows">
          {rows.map((r) => (
            <Card key={r.household_id} className="overflow-hidden p-0">
              <div className="flex flex-wrap items-center justify-between gap-2 border-b border-border-soft px-4 py-2.5">
                <div className="text-sm text-ink">{r.household}</div>
                <div className="flex flex-wrap gap-1.5">
                  <span className="chip text-ink-muted">held a year or less {money(r.realised.short)}</span>
                  <span className="chip text-ink-muted">longer {money(r.realised.long)}</span>
                </div>
              </div>
              <div className="divide-y divide-border-soft">
                {r.expected.map((e: any, i: number) => (
                  <div key={i} className="flex flex-wrap items-center gap-1.5 px-4 py-2" title={e.document ?? undefined}>
                    <span className="text-sm text-ink">{e.label}</span><span className="text-xs text-ink-muted">{e.issuer}</span>
                    <span className={`chip ${TONE[e.status]}`}>{e.status}</span><span className="font-mono text-xs text-ink-muted">due {reviewDay(e.due)}</span>
                  </div>
                ))}
              </div>
            </Card>
          ))}
        </div>
      )}
    </div>
  );
}
