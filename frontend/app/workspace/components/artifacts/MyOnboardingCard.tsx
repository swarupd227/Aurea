/** A client's own accounts and the step each has reached (India, B-392). Their reading only: no checks, no reasons, no dates promised. */

export function MyOnboardingAccounts({ accounts }: { accounts: any[] }) {
  if (accounts.length === 0) return <div className="px-3 py-2"><span className="chip text-ink-muted">No account being opened</span></div>;
  return (
    <div className="divide-y divide-border-soft">
      {accounts.map((a, i) => (
        <div key={`${a.name}-${a.account_type}-${i}`} className="space-y-1.5 px-3 py-2" data-testid="my-onboarding-account">
          <div className="flex flex-wrap items-center gap-1.5">
            <span className="mr-auto text-sm text-ink">{a.name}</span>
            <span className="chip text-ink-muted">{a.account_type}</span>
            <span className={`chip ${a.done ? "text-ok" : a.needs.length ? "text-warn" : ""}`} data-testid="my-onboarding-step">{a.step_label}</span>
          </div>
          <div className="flex gap-0.5" role="img" aria-label={`Step ${a.position} of ${a.of}`}>
            {Array.from({ length: a.of }, (_, n) => <span key={n} className={`h-1 flex-1 rounded-full ${n < a.position ? "bg-ok" : "bg-border"}`} />)}
          </div>
          <div className="flex flex-wrap gap-1.5">
            <span className="chip text-ink-muted tabular-nums">Step {a.position} of {a.of}</span>
            {a.since && <span className="chip text-ink-muted tabular-nums">Since {a.since}</span>}
            {a.opened_on && <span className="chip text-ink-muted tabular-nums">Opened {a.opened_on}</span>}
            <span className="chip text-ink-muted">Next: {a.whose_move}</span>
            {a.needs.map((n: string) => <span key={n} className="chip text-warn" data-testid="my-onboarding-need">{n}</span>)}
          </div>
        </div>
      ))}
    </div>
  );
}

export function MyOnboardingCard({ result }: { result: any }) {
  return (
    <div className="overflow-hidden rounded-lg border border-border bg-surface" data-testid="my-onboarding-card">
      <div className="flex flex-wrap items-center gap-1.5 border-b border-border-soft px-3 py-2">
        <span className="mr-auto text-sm font-medium text-ink">Your accounts</span>
        {result.adviser && <span className="chip text-ink-muted">Adviser: {result.adviser}</span>}
      </div>
      <MyOnboardingAccounts accounts={result.accounts ?? []} />
    </div>
  );
}
