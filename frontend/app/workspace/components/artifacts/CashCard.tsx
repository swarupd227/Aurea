import { money } from "@/lib/format";

export function CashCard({ result }: { result: any }) {
  const s = result.summary;
  const rows: any[] = result.requests.filter((r: any) => r.status === "requested").slice(0, 6);
  return (
    <div className="overflow-hidden rounded-lg border border-border bg-surface" data-testid="cash-card">
      <div className="flex flex-wrap items-center justify-between gap-2 border-b border-border-soft px-3 py-2">
        <div className="text-sm font-medium text-ink">Cash movements</div>
        <div className="flex flex-wrap gap-1.5">
          <span className="chip text-ink-muted">{s.open} open · {money(s.value_open)}</span>
          {s.short > 0 && <span className="chip text-crit">{s.short} short of cash</span>}
          {s.rmd_left > 0 && <span className="chip text-warn">RMD left {money(s.rmd_left)}</span>}
          <span className="chip text-ink-muted">{s.standing} standing</span>
        </div>
      </div>
      {rows.length > 0 && (
        <div className="overflow-x-auto">
          <table className="w-full text-sm">
            <tbody className="divide-y divide-border-soft">
              {rows.map((r) => (
                <tr key={r.id}>
                  <td className="px-3 py-1.5 text-ink">{r.client ?? r.account}</td>
                  <td className="px-3 py-1.5 text-xs text-ink-muted">{r.payee}{r.third_party ? " · third party" : ""}</td>
                  <td className="px-3 py-1.5 text-right font-mono text-xs tabular-nums text-ink-muted">{money(r.amount)}</td>
                  <td className="px-3 py-1.5">{r.short_by > 0 && <span className="chip text-crit">short {money(r.short_by)}</span>}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </div>
  );
}
