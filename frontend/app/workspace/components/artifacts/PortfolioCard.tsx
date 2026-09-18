interface Holding {
  symbol: string;
  name: string;
  asset_class: string;
  quantity: number;
  market_value: number;
}

interface PortfolioResult {
  mandate_id: string;
  mandate_name: string;
  holdings: Holding[];
  cash: number;
  total_value: number;
}

const ASSET_CLASS_COLOR: Record<string, string> = {
  equity: "bg-accent",
  fixed_income: "bg-ok",
  property: "bg-info",
  alternatives: "bg-warn",
  multi_asset: "bg-ink-muted",
};

function money(n: number) {
  return n.toLocaleString(undefined, { style: "currency", currency: "USD", maximumFractionDigits: 0 });
}

export function PortfolioCard({ result }: { result: PortfolioResult }) {
  const rows = [...result.holdings].sort((a, b) => b.market_value - a.market_value);
  const total = result.total_value || 1;

  return (
    <div className="overflow-hidden rounded-lg border border-border bg-surface">
      <div className="flex items-center justify-between border-b border-border-soft px-3 py-2">
        <div className="text-sm font-medium text-ink">{result.mandate_name}</div>
        <div className="font-mono text-sm tabular-nums text-ink">{money(result.total_value)}</div>
      </div>
      <table className="w-full text-sm">
        <tbody className="divide-y divide-border-soft">
          {rows.map((h) => {
            const pct = h.market_value / total;
            return (
              <tr key={h.symbol}>
                <td className="px-3 py-1.5">
                  <div className="text-ink">{h.symbol}</div>
                  <div className="text-xs text-ink-muted">{h.name}</div>
                </td>
                <td className="px-3 py-1.5 w-32">
                  <div className="h-1.5 w-full overflow-hidden rounded-full bg-border-soft">
                    <div
                      className={`h-full rounded-full ${ASSET_CLASS_COLOR[h.asset_class] || "bg-ink-muted"}`}
                      style={{ width: `${Math.min(100, pct * 100)}%` }}
                    />
                  </div>
                </td>
                <td className="px-3 py-1.5 text-right font-mono tabular-nums text-ink whitespace-nowrap">
                  {money(h.market_value)}
                </td>
                <td className="px-3 py-1.5 text-right font-mono text-xs tabular-nums text-ink-muted whitespace-nowrap">
                  {(pct * 100).toFixed(1)}%
                </td>
              </tr>
            );
          })}
          {result.cash > 0 && (
            <tr>
              <td className="px-3 py-1.5 text-ink-muted">Cash</td>
              <td className="px-3 py-1.5" />
              <td className="px-3 py-1.5 text-right font-mono tabular-nums text-ink-muted whitespace-nowrap">
                {money(result.cash)}
              </td>
              <td className="px-3 py-1.5 text-right font-mono text-xs tabular-nums text-ink-muted whitespace-nowrap">
                {((result.cash / total) * 100).toFixed(1)}%
              </td>
            </tr>
          )}
        </tbody>
      </table>
    </div>
  );
}
