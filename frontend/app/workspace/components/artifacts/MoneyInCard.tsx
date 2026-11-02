import { money } from "@/lib/format";

export function MoneyInCard({ result }: { result: any }) {
  const s = result.summary;
  return (
    <div className="overflow-hidden rounded-lg border border-border bg-surface" data-testid="money-in-card">
      <div className="flex flex-wrap items-center justify-between gap-2 border-b border-border-soft px-3 py-2">
        <div className="text-sm font-medium text-ink">Money in</div>
        <div className="flex gap-1.5">
          {s.on_hold > 0 && <span className="chip text-warn">{money(s.on_hold)} on hold</span>}
          {s.returned > 0 && <span className="chip text-critical">{s.returned} returned</span>}
          {s.instructions_overdue > 0 && <span className="chip text-warn">{s.instructions_overdue} to revalidate</span>}
        </div>
      </div>
      {result.deposits.length > 0 && (
        <table className="w-full text-xs"><tbody className="divide-y divide-border-soft">
          {result.deposits.slice(0, 5).map((d: any) => (
            <tr key={d.id}>
              <td className="px-3 py-1.5 text-ink">{d.client}</td>
              <td className="px-3 py-1.5 font-mono uppercase text-ink-muted">{d.method}</td>
              <td className="px-3 py-1.5 text-right font-mono">{money(d.amount)}</td>
              <td className="px-3 py-1.5">{d.returned_on ? <span className="chip text-critical">{d.return_code}</span> : d.on_hold ? <span className="chip text-warn">held</span> : <span className="chip text-ok">available</span>}</td>
            </tr>
          ))}
        </tbody></table>
      )}
    </div>
  );
}
