interface Row {
  symbol: string;
  name: string;
  currency: string;
  unit_value: number | null;
  as_of: string | null;
  basis: "declared" | "synthetic" | "market" | null;
  age_days: number | null;
  limit_days: number;
  overdue: boolean;
  holdings: number;
  value_held: number;
  source_document: string | null;
  recorded_by: string | null;
}

interface Recorded {
  symbol: string;
  currency: string;
  as_of: string;
  unit_value: number;
  source_document: string;
  previous: { value: number; as_of: string | null; basis: string | null } | null;
  change_pct: number | null;
  holdings_revalued: number;
  value_before: number;
  value_after: number;
  recorded_by: string;
}

type Payload = { instruments: Row[] } | Recorded;

function n(v: number, d = 2) {
  return v.toLocaleString(undefined, { minimumFractionDigits: d, maximumFractionDigits: d });
}

const BASIS_LABEL: Record<string, { text: string; tone: string }> = {
  declared: { text: "stated valuation", tone: "text-ink-muted" },
  synthetic: { text: "placeholder - not a valuation", tone: "text-warn" },
  market: { text: "market price", tone: "text-ink-muted" },
};

export function ValuationCard({ result }: { result: Payload }) {
  if ("instruments" in result) {
    return (
      <div className="overflow-hidden rounded-lg border border-border bg-surface" data-testid="valuation-card">
        <div className="border-b border-border-soft px-3 py-2 text-sm font-medium text-ink">Private valuations</div>
        <table className="w-full text-sm">
          <thead>
            <tr className="text-left text-[10px] uppercase tracking-wide text-ink-muted">
              <th className="px-3 py-1.5 font-medium">Holding</th>
              <th className="px-3 py-1.5 text-right font-medium">Unit value</th>
              <th className="px-3 py-1.5 font-medium">Basis</th>
              <th className="px-3 py-1.5 text-right font-medium">Age</th>
              <th className="px-3 py-1.5 text-right font-medium">Value held</th>
            </tr>
          </thead>
          <tbody className="divide-y divide-border-soft">
            {result.instruments.map((r) => {
              const b = r.basis ? BASIS_LABEL[r.basis] : null;
              return (
                <tr key={r.symbol}>
                  <td className="px-3 py-1.5">
                    <div className="text-ink">{r.symbol}</div>
                    <div className="text-xs text-ink-muted">{r.source_document ?? "no statement on file"}</div>
                  </td>
                  <td className="px-3 py-1.5 text-right font-mono tabular-nums text-ink">{r.unit_value != null ? n(r.unit_value, 4) : "—"}</td>
                  <td className="px-3 py-1.5">{b && <span className={`chip ${b.tone}`}>{b.text}</span>}</td>
                  <td className={`px-3 py-1.5 text-right font-mono tabular-nums ${r.overdue ? "text-crit" : "text-ink-muted"}`}>
                    {r.age_days != null ? `${r.age_days}d / ${r.limit_days}d` : "—"}
                  </td>
                  <td className="px-3 py-1.5 text-right font-mono tabular-nums text-ink-muted">{r.currency} {n(r.value_held, 0)} · {r.holdings}</td>
                </tr>
              );
            })}
          </tbody>
        </table>
      </div>
    );
  }

  const up = (result.change_pct ?? 0) >= 0;
  return (
    <div className="overflow-hidden rounded-lg border border-border bg-surface" data-testid="valuation-card">
      <div className="border-b border-border-soft px-3 py-2 text-sm font-medium text-ink">Valuation recorded · {result.symbol}</div>
      <div className="flex flex-wrap items-end gap-x-8 gap-y-2 px-3 py-3">
        <div>
          <div className="text-[10px] uppercase tracking-wide text-ink-muted">Unit value · {result.as_of}</div>
          <div className="font-mono text-xl tabular-nums text-ink">{result.currency} {n(result.unit_value, 4)}</div>
        </div>
        {result.previous && (
          <div>
            <div className="text-[10px] uppercase tracking-wide text-ink-muted">Was</div>
            <div className="font-mono text-base tabular-nums text-ink-muted">{n(result.previous.value, 4)}</div>
          </div>
        )}
        {result.change_pct != null && (
          <div className={`font-mono text-base tabular-nums ${up ? "text-ok" : "text-crit"}`}>{up ? "+" : ""}{(result.change_pct * 100).toFixed(2)}%</div>
        )}
      </div>
      <div className="flex flex-wrap gap-1.5 border-t border-border-soft px-3 py-2">
        <span className="chip text-ink-muted">{result.holdings_revalued} holding(s) revalued</span>
        <span className="chip text-ink-muted">{result.currency} {n(result.value_before, 0)} → {n(result.value_after, 0)}</span>
        <span className="chip text-ink-muted">stated valuation, not a market price</span>
        <span className="chip text-ink-muted" title={result.source_document}>{result.source_document.slice(0, 48)}</span>
        <span className="chip text-ink-muted">by {result.recorded_by}</span>
      </div>
    </div>
  );
}
