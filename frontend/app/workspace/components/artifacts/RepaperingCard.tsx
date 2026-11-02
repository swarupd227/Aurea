import { repaperTone } from "@/components/views/RepaperingView";

export function RepaperingCard({ result }: { result: any }) {
  return (
    <div className="overflow-hidden rounded-lg border border-border bg-surface" data-testid="repapering-card">
      <div className="border-b border-border-soft px-3 py-2 text-sm font-medium text-ink">Repapering</div>
      {result.campaigns.length === 0 ? <div className="px-3 py-2 text-xs text-ink-muted">No campaigns.</div> : (
        <table className="w-full text-xs"><tbody className="divide-y divide-border-soft">
          {result.campaigns.map((c: any) => (
            <tr key={c.id}>
              <td className="px-3 py-1.5 text-ink">{c.name}</td>
              <td className="px-3 py-1.5 text-ink-muted">{c.consent}</td>
              <td className="px-3 py-1.5"><div className="flex flex-wrap gap-1">
                {Object.entries(c.counts as Record<string, number>).map(([s, n]) => <span key={s} className={`chip ${repaperTone[s] ?? ""}`}>{s.replace(/_/g, " ")} {n}</span>)}
              </div></td>
              <td className="px-3 py-1.5 font-mono text-ink">{c.ready}/{c.total}</td>
            </tr>
          ))}
        </tbody></table>
      )}
    </div>
  );
}
