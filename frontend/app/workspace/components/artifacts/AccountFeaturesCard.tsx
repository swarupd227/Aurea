export function AccountFeaturesCard({ result }: { result: any }) {
  const s = result.summary;
  const live = result.holds.filter((h: any) => h.state === "active");
  const waiting = result.features.filter((f: any) => f.status === "requested");
  return (
    <div className="overflow-hidden rounded-lg border border-border bg-surface" data-testid="features-card">
      <div className="flex flex-wrap items-center justify-between gap-2 border-b border-border-soft px-3 py-2">
        <div className="text-sm font-medium text-ink">Account features and holds</div>
        <div className="flex gap-1.5">
          <span className="chip">{s.waiting} waiting</span>
          {s.holds_active > 0 && <span className="chip text-critical">{s.holds_active} hold(s)</span>}
          {s.notices_late > 0 && <span className="chip text-critical">{s.notices_late} notice late</span>}
        </div>
      </div>
      {(live.length > 0 || waiting.length > 0) && (
        <table className="w-full text-xs"><tbody className="divide-y divide-border-soft">
          {live.map((h: any) => (
            <tr key={h.id}><td className="px-3 py-1.5 text-ink">{h.client}</td><td className="px-3 py-1.5 text-ink-muted">{h.account}</td><td className="px-3 py-1.5"><span className="chip text-critical">hold to {h.expires_on}</span></td></tr>
          ))}
          {waiting.map((f: any) => (
            <tr key={f.id}><td className="px-3 py-1.5 text-ink">{f.client}</td><td className="px-3 py-1.5 text-ink-muted">{f.label}{f.level ? ` ${f.level}` : ""}</td><td className="px-3 py-1.5"><span className="chip text-warn">requested</span></td></tr>
          ))}
        </tbody></table>
      )}
    </div>
  );
}
