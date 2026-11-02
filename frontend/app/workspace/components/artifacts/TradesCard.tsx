import { money } from "@/lib/format";
import { reviewDay } from "./ReviewsCard";

export function TradesCard({ result }: { result: any }) {
  const s = result.summary;
  return (
    <div className="overflow-hidden rounded-lg border border-border bg-surface" data-testid="trades-card">
      <div className="flex flex-wrap items-center justify-between gap-2 border-b border-border-soft px-3 py-2">
        <div className="text-sm font-medium text-ink">Trades, last {result.days} days</div>
        <div className="flex flex-wrap gap-1.5">
          <span className="chip text-ink-muted">{s.filled} filled</span>
          {s.unsettled > 0 && <span className="chip text-warn">{s.unsettled} unsettled</span>}
          <span className="chip text-ink-muted">{s.direct} direct</span>
          {result.paper_venue && <span className="chip text-ink-muted">paper venue</span>}
        </div>
      </div>
      <div className="overflow-x-auto">
        <table className="w-full text-sm">
          <tbody className="divide-y divide-border-soft">
            {result.trades.slice(0, 6).map((t: any) => (
              <tr key={t.id}>
                <td className="px-3 py-1.5 font-mono text-xs text-ink">{t.side.toUpperCase()} {t.quantity} {t.symbol}</td>
                <td className="px-3 py-1.5 text-xs text-ink-muted">{t.client}</td>
                <td className="px-3 py-1.5 text-right font-mono text-xs tabular-nums text-ink-muted">{t.gross ? money(t.gross) : "—"}</td>
                <td className="px-3 py-1.5 font-mono text-xs text-ink-muted">{t.settles_on ? reviewDay(t.settles_on) : t.status}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </div>
  );
}

export function TaxDocumentsCard({ result }: { result: any }) {
  const rows: any[] = Array.isArray(result?.households) ? result.households : [result];
  const expected = rows.flatMap((r) => (r.expected ?? []).map((e: any) => ({ ...e, client: r.household })));
  return (
    <div className="overflow-hidden rounded-lg border border-border bg-surface" data-testid="tax-documents-card">
      <div className="flex flex-wrap items-center justify-between gap-2 border-b border-border-soft px-3 py-2">
        <div className="text-sm font-medium text-ink">Tax documents {result.tax_year_label}</div>
        <span className="chip text-ink-muted">not the official form</span>
      </div>
      {expected.length === 0 ? <div className="px-3 py-2"><span className="chip text-ink-muted">none expected</span></div> : (
        <div className="divide-y divide-border-soft">
          {expected.slice(0, 8).map((e, i) => (
            <div key={i} className="flex flex-wrap items-center gap-1.5 px-3 py-1.5">
              <span className="text-sm text-ink">{e.client}</span>
              <span className="text-xs text-ink-muted">{e.label} · {e.issuer}</span>
              <span className={`chip ${e.status === "received" ? "text-ok" : e.status === "overdue" ? "text-crit" : "text-ink-muted"}`}>{e.status}</span>
            </div>
          ))}
        </div>
      )}
    </div>
  );
}
