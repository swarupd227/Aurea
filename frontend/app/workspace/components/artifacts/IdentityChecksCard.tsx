const SHOWN = 8;
const TONE: Record<string, string> = {
  passed: "text-ok", passed_alternative: "text-ok", retry: "text-warn", review: "text-crit", queued: "text-crit", failed: "text-crit",
};

/** One check on one party: where it stands, what was returned, and how many attempts it has had. */
export function CheckRow({ r, action }: { r: any; action?: React.ReactNode }) {
  return (
    <div className="space-y-1.5 px-3 py-2" data-testid="identity-check-row">
      <div className="flex flex-wrap items-center gap-1.5">
        <span className="mr-auto text-sm text-ink">{r.party}</span>
        <span className="chip text-ink-muted">{r.kind_label}</span>
        <span className={`chip ${TONE[r.state] ?? "text-ink-muted"}`} data-testid="identity-check-state">{r.state_label}</span>
        {action}
      </div>
      <div className="flex flex-wrap gap-1.5">
        {r.reason && <span className="chip text-crit">{r.reason}</span>}
        {r.score != null && <span className="chip text-ink-muted tabular-nums">Name match {r.score}/100</span>}
        {r.name_shown && <span className="chip text-ink-muted">Shown as {r.name_shown}</span>}
        {r.last4 && <span className="chip text-ink-muted tabular-nums">{r.bank} ending {r.last4}</span>}
        {r.pan && <span className="chip text-ink-muted tabular-nums">PAN {r.pan}</span>}
        <span className="chip text-ink-muted tabular-nums">{r.tries} of {r.max_tries} attempts</span>
        {!r.passed && <span className="chip text-ink-muted" title={r.alternative}>By hand: {r.alternative}</span>}
        {r.owner && <span className="chip text-warn tabular-nums">{r.owner}{r.age_days != null ? `, ${r.age_days}d` : ""}</span>}
        {r.simulated && <span className="chip text-warn" data-testid="identity-check-simulated">simulated</span>}
      </div>
    </div>
  );
}

/** What happens when each check fails, settled before anything runs. */
export function CheckPaths({ defined }: { defined: any[] }) {
  return (
    <div className="overflow-x-auto">
      <table className="w-full text-xs" data-testid="identity-check-paths">
        <thead><tr className="text-left text-[10px] uppercase tracking-wide text-ink-muted">
          <th className="px-3 py-1 font-medium">Check</th><th className="px-3 py-1 text-right font-medium">Attempts</th>
          <th className="px-3 py-1 font-medium">Otherwise</th><th className="px-3 py-1 font-medium">Queue</th>
        </tr></thead>
        <tbody className="divide-y divide-border-soft">
          {defined.map((d) => (
            <tr key={d.kind}>
              <td className="px-3 py-1 text-ink-soft">{d.label}</td>
              <td className="px-3 py-1 text-right font-mono tabular-nums text-ink">{d.tries}</td>
              <td className="px-3 py-1 text-ink-muted">{d.alternative}</td>
              <td className="px-3 py-1 text-ink-muted">{d.then}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

/** Identity and bank checks: the queue first, then what can be retried, then what passed. */
export function IdentityChecksCard({ result }: { result: any }) {
  if (result.applies === false) {
    return <div className="rounded-lg border border-border bg-surface px-3 py-2" data-testid="identity-checks-card"><span className="chip text-ink-muted">Not an Indian firm</span></div>;
  }
  const checks: any[] = result.checks ?? [];
  const named = result.attempted;
  const shown = [...checks].sort((a, b) => Number(b.party === named?.party && b.kind === named?.kind) - Number(a.party === named?.party && a.kind === named?.kind)).slice(0, SHOWN);
  return (
    <div className="overflow-hidden rounded-lg border border-border bg-surface" data-testid="identity-checks-card">
      <div className="flex flex-wrap items-center justify-between gap-2 border-b border-border-soft px-3 py-2">
        <div className="text-sm font-medium text-ink">Identity and bank checks{result.case ? `: ${result.case}` : ""}</div>
        <div className="flex flex-wrap gap-1.5">
          <span className={`chip ${result.counts.queued ? "text-crit" : "text-ink-muted"}`}>{result.counts.queued} in queue</span>
          {result.counts.retry > 0 && <span className="chip text-warn">{result.counts.retry} to retry</span>}
          <span className="chip text-ink-muted">{result.counts.passed} passed</span>
          {!result.connected && <span className="chip text-ink-muted" title={result.not_connected}>No service connected</span>}
          {result.provider?.simulated && <span className="chip text-warn">simulated service</span>}
        </div>
      </div>
      {checks.length === 0 ? <div className="px-3 py-2"><span className="chip text-ink-muted">None run</span></div> : (
        <div className="divide-y divide-border-soft">
          {shown.map((r) => <CheckRow key={`${r.person_id ?? r.entity_id ?? r.case_id}-${r.party}-${r.kind}-${r.last4 ?? ""}`} r={r} />)}
          {checks.length > SHOWN && <div className="px-3 py-2"><span className="chip text-ink-muted">+{checks.length - SHOWN} more</span></div>}
        </div>
      )}
      {checks.length === 0 && <div className="border-t border-border-soft"><CheckPaths defined={result.defined ?? []} /></div>}
      <div className="border-t border-border-soft px-3 py-2"><span className="chip text-ink-muted" title={result.thresholds}>Name match tolerance assumed</span></div>
    </div>
  );
}
