import { money } from "@/lib/format";

const SHOWN = 8;

export function SipCard({ result }: { result: any }) {
  const ccy: string | undefined = result.currency || undefined;
  const rows: any[] = result.sips.slice(0, SHOWN);
  return (
    <div className="overflow-hidden rounded-lg border border-border bg-surface" data-testid="sip-card">
      <div className="flex flex-wrap items-center justify-between gap-2 border-b border-border-soft px-3 py-2">
        <div className="text-sm font-medium text-ink">SIPs</div>
        <div className="flex flex-wrap gap-1.5">
          <span className="chip text-ink-muted">{result.active} active</span>
          <span className="chip text-ink-muted">{money(result.monthly, ccy)} a month</span>
        </div>
      </div>
      {rows.length > 0 && (
        <div className="overflow-x-auto">
          <table className="w-full text-sm">
            <tbody className="divide-y divide-border-soft">
              {rows.map((r) => {
                const missed = r.last?.status === "missed";
                return (
                  <tr key={r.id} data-testid="sip-row">
                    <td className="px-3 py-1.5">
                      <div className="text-ink">{r.scheme ?? r.fund}</div>
                      <div className="text-xs text-ink-muted">{r.account}</div>
                    </td>
                    <td className="px-3 py-1.5">
                      <div className="flex flex-wrap gap-1">
                        {r.plan && <span className="chip text-ink-muted">{r.plan}</span>}
                        {r.simulated && <span className="chip text-warn">simulated</span>}
                        {r.status === "ended" && <span className="chip text-ink-muted">ended</span>}
                        {missed && <span className="chip text-crit" title={r.last.reason}>missed {r.last.month}</span>}
                      </div>
                    </td>
                    <td className="px-3 py-1.5 text-right font-mono text-xs tabular-nums text-ink-muted">
                      {money(r.amount, ccy)} · day {r.day_of_month}
                    </td>
                    <td className="px-3 py-1.5 text-right font-mono text-xs tabular-nums text-ink-muted">
                      {r.bought}/{r.instalments} · {r.units} units
                    </td>
                    <td className="px-3 py-1.5 text-right font-mono text-xs text-ink-muted">{r.next_instalment ?? "—"}</td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        </div>
      )}
      {result.sips.length > SHOWN && (
        <div className="border-t border-border-soft px-3 py-1.5 text-xs text-ink-muted">+{result.sips.length - SHOWN} more</div>
      )}
    </div>
  );
}
