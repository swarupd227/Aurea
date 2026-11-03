/** read_advisers and assign_adviser: each adviser and how many households they hold, and the households nobody holds. */
export function AdvisersCard({ result }: { result: any }) {
  const unassigned = (result.households as any[]).filter((h) => !h.adviser_id);
  return (
    <div className="overflow-hidden rounded-lg border border-border bg-surface" data-testid="advisers-card">
      <div className="flex flex-wrap items-center justify-between gap-2 border-b border-border-soft px-3 py-2">
        <span className="text-sm font-medium text-ink">Advisers</span>
        <span className={`chip ${result.unassigned ? "text-warn" : "text-ok"}`}>{result.unassigned} unassigned</span>
      </div>
      {result.advisers.length === 0 ? <div className="px-3 py-2 text-xs text-ink-muted">No active advisers.</div> : (
        <table className="w-full text-xs"><tbody className="divide-y divide-border-soft">
          {result.advisers.map((a: any) => (
            <tr key={a.id}>
              <td className="px-3 py-1.5 text-ink">{a.name}</td>
              <td className="px-3 py-1.5 text-right font-mono text-ink">{a.households}</td>
            </tr>
          ))}
        </tbody></table>
      )}
      {unassigned.length > 0 && (
        <div className="flex flex-wrap gap-1 border-t border-border-soft px-3 py-2">
          {unassigned.slice(0, 12).map((h) => <span key={h.id} className="chip text-ink-muted">{h.name}</span>)}
          {unassigned.length > 12 && <span className="chip text-ink-muted">+{unassigned.length - 12}</span>}
        </div>
      )}
    </div>
  );
}
