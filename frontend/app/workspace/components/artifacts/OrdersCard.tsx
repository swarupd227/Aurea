import { Check, X } from "lucide-react";

interface Fill {
  order_id: string;
  symbol: string;
  side: string;
  quantity: number;
  price: number;
  fees: number;
  cash_delta: number;
}

interface Failed {
  order_id?: string;
  symbol: string;
  reason: string;
}

interface OrdersResult {
  mandate_id: string;
  orders_settled: number;
  orders_failed: number;
  venue: string;
  fills: Fill[];
  failed: Failed[];
}

function money(n: number) {
  const sign = n < 0 ? "-" : "";
  return `${sign}$${Math.abs(n).toLocaleString(undefined, { maximumFractionDigits: 2 })}`;
}

export function OrdersCard({ result }: { result: OrdersResult }) {
  return (
    <div className="overflow-hidden rounded-lg border border-border bg-surface">
      <div className="flex items-center justify-between border-b border-border-soft px-3 py-2">
        <div className="text-sm font-medium text-ink">Orders — {result.venue} venue</div>
        <div className="font-mono text-xs tabular-nums text-ink-muted">
          {result.orders_settled} settled{result.orders_failed ? `, ${result.orders_failed} failed` : ""}
        </div>
      </div>
      {result.fills.length > 0 && (
        <table className="w-full text-sm">
          <tbody className="divide-y divide-border-soft">
            {result.fills.map((f) => (
              <tr key={f.order_id}>
                <td className="px-3 py-1.5">
                  <Check className="h-3.5 w-3.5 text-ok" />
                </td>
                <td className="px-3 py-1.5 text-ink">
                  {f.side} {f.quantity} {f.symbol}
                </td>
                <td className="px-3 py-1.5 text-right font-mono text-xs tabular-nums text-ink-muted whitespace-nowrap">
                  @ {money(f.price)}
                </td>
                <td className="px-3 py-1.5 text-right font-mono tabular-nums text-ink whitespace-nowrap">
                  {money(f.cash_delta)}
                </td>
              </tr>
            ))}
            {result.failed.map((f, i) => (
              <tr key={f.order_id || i}>
                <td className="px-3 py-1.5">
                  <X className="h-3.5 w-3.5 text-crit" />
                </td>
                <td className="px-3 py-1.5 text-ink" colSpan={3}>
                  {f.symbol} — <span className="text-ink-muted">{f.reason}</span>
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      )}
    </div>
  );
}
