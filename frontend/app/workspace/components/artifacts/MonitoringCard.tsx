export function MonitoringCard({ result }: { result: any }) {
  const s = result.summary;
  const open = result.alerts.filter((a: any) => !a.decision);
  return (
    <div className="overflow-hidden rounded-lg border border-border bg-surface" data-testid="monitoring-card">
      <div className="flex flex-wrap items-center justify-between gap-2 border-b border-border-soft px-3 py-2">
        <div className="text-sm font-medium text-ink">Transaction monitoring</div>
        <div className="flex gap-1.5">
          <span className={`chip ${s.open_alerts ? "text-warn" : "text-ok"}`}>{s.open_alerts} open</span>
          <span className="chip">{s.open_investigations} investigation(s)</span>
          {s.reports_due_7_days > 0 && <span className="chip text-critical">{s.reports_due_7_days} report(s) due in 7d</span>}
        </div>
      </div>
      {open.length > 0 && (
        <table className="w-full text-xs"><tbody className="divide-y divide-border-soft">
          {open.slice(0, 6).map((a: any) => (
            <tr key={a.key}><td className="px-3 py-1.5 text-ink">{a.household}</td><td className="px-3 py-1.5 text-ink-muted">{a.label}</td><td className="px-3 py-1.5 font-mono text-ink-muted">{a.last_on}</td></tr>
          ))}
        </tbody></table>
      )}
    </div>
  );
}
