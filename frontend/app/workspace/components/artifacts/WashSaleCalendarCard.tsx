import { AlertTriangle, Check } from "lucide-react";

interface Conflict {
  account_name: string;
  acquired_on: string;
  quantity: number;
  days_ago: number;
}

interface Violation {
  lot_id: string;
  account_name: string;
  symbol: string;
  wash_sale_disallowed_loss: number;
  wash_sale_conflicts: Conflict[];
}

interface WashSalePayload {
  lots_checked: number;
  violations: Violation[];
  total_disallowed_loss: number;
}

function money(n: number) {
  return n.toLocaleString(undefined, { style: "currency", currency: "USD", maximumFractionDigits: 0 });
}

export function WashSaleCalendarCard({ result }: { result: WashSalePayload }) {
  const clean = result.violations.length === 0;

  return (
    <div className="overflow-hidden rounded-lg border border-border bg-surface">
      <div className="flex items-center justify-between border-b border-border-soft px-3 py-2">
        <div className="text-sm font-medium text-ink">Wash-sale calendar</div>
        <div className={`inline-flex items-center gap-1.5 font-mono text-xs ${clean ? "text-ok" : "text-warn"}`}>
          {clean ? <Check className="h-3.5 w-3.5" /> : <AlertTriangle className="h-3.5 w-3.5" />}
          {clean ? "clear" : `${money(result.total_disallowed_loss)} at risk`}
        </div>
      </div>
      {clean ? (
        <div className="px-3 py-3 text-xs text-ink-muted">
          {result.lots_checked} lot(s) checked across the household — nothing would be disallowed right now.
        </div>
      ) : (
        <table className="w-full text-sm">
          <tbody className="divide-y divide-border-soft">
            {result.violations.map((v) => (
              <tr key={v.lot_id}>
                <td className="px-3 py-1.5">
                  <div className="text-ink">{v.symbol}</div>
                  <div className="text-xs text-ink-muted">{v.account_name}</div>
                </td>
                <td className="px-3 py-1.5 text-xs text-ink-muted">
                  {v.wash_sale_conflicts.map((c, i) => (
                    <div key={i}>
                      {c.account_name} bought {c.quantity.toLocaleString()} · {c.days_ago}d ago
                    </div>
                  ))}
                </td>
                <td className="px-3 py-1.5 text-right font-mono tabular-nums text-warn whitespace-nowrap">
                  {money(v.wash_sale_disallowed_loss)}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      )}
    </div>
  );
}
