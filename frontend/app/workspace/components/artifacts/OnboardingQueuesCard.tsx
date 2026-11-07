export function OnboardingQueuesCard({ result }: { result: any }) {
  const s = result.summary;
  const c = result.clocks;
  const queues = Object.values(result.queues ?? {}).filter((q: any) => q.items.length) as any[];
  return (
    <div className="overflow-hidden rounded-lg border border-border bg-surface" data-testid="onboarding-queues-card">
      <div className="flex flex-wrap items-center justify-between gap-2 border-b border-border-soft px-3 py-2">
        <div className="text-sm font-medium text-ink">Onboarding queues</div>
        <div className="flex flex-wrap gap-1.5">
          <span className="chip text-ink-muted">{s.accounts} accounts</span>
          {s.overdue > 0 && <span className="chip text-crit">{s.overdue} past SLA</span>}
        </div>
      </div>
      <div className="flex flex-wrap gap-1.5 border-b border-border-soft px-3 py-2">
        <span className="chip text-ink-muted">paperwork {c.paperwork.p50 ?? "—"}d</span>
        <span className="chip text-ink-muted">money {c.money.p50 ?? "—"}d</span>
        <span className="chip text-ink-muted">invested {c.invested.p50 ?? "—"}d</span>
        {(c.out_of_order ?? []).length > 0 && (
          <span className="chip text-warn" data-testid="onboarding-card-out-of-order">{c.out_of_order.length} dates out of order</span>
        )}
      </div>
      <div className="flex flex-wrap gap-1.5 px-3 py-2">
        {queues.length === 0 ? <span className="chip text-ink-muted">nothing stuck</span> : queues.map((q) => <span key={q.label} className="chip text-warn">{q.label} {q.items.length}</span>)}
      </div>
    </div>
  );
}
