interface NetOrder {
  symbol: string;
  side: string | null;
  quantity: number;
  crossed_quantity: number;
  gross_buy_quantity: number;
  gross_sell_quantity: number;
}

interface NettingPayload {
  net_orders: NetOrder[];
}

export function SleeveNettingCard({ result }: { result: NettingPayload }) {
  const totalCrossed = result.net_orders.reduce((s, o) => s + o.crossed_quantity, 0);
  const totalGross = result.net_orders.reduce((s, o) => s + o.gross_buy_quantity + o.gross_sell_quantity, 0);

  return (
    <div className="overflow-hidden rounded-lg border border-border bg-surface">
      <div className="flex items-center justify-between border-b border-border-soft px-3 py-2">
        <div className="text-sm font-medium text-ink">Sleeve netting</div>
        <div className="font-mono text-xs tabular-nums text-ink-muted">
          {totalCrossed.toLocaleString()} of {totalGross.toLocaleString()} sh crossed internally
        </div>
      </div>
      <table className="w-full text-sm">
        <tbody className="divide-y divide-border-soft">
          {result.net_orders.map((o, i) => (
            <tr key={i}>
              <td className="px-3 py-1.5 text-ink">{o.symbol}</td>
              <td className="px-3 py-1.5 text-xs text-ink-muted">
                {o.side ? `${o.side} ${o.quantity.toLocaleString()}` : "fully crossed — no trade"}
              </td>
              <td className="px-3 py-1.5 text-right font-mono text-xs tabular-nums text-ink-muted whitespace-nowrap">
                {o.gross_buy_quantity.toLocaleString()} buy / {o.gross_sell_quantity.toLocaleString()} sell gross
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}
