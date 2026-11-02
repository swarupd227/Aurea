export function OnboardingScoreboardCard({ result }: { result: any }) {
  const d = result.days_to_invest, n = result.nps, sc = result.screening, ab = result.abandonment;
  return (
    <div className="overflow-hidden rounded-lg border border-border bg-surface" data-testid="scoreboard-card">
      <div className="border-b border-border-soft px-3 py-2 text-sm font-medium text-ink">Onboarding scoreboard</div>
      <div className="flex flex-wrap gap-1.5 px-3 py-2">
        <span className="chip">Days to invest p50 {d.n ? `${d.p50}d` : "—"}</span>
        <span className="chip">{ab.stalled} stalled</span>
        <span className="chip">{ab.rejected} rejected</span>
        <span className="chip">Alert rate {sc.alert_rate_pct != null ? `${sc.alert_rate_pct}%` : "—"}</span>
        <span className={`chip ${n.nps == null ? "text-ink-muted" : ""}`}>NPS {n.nps != null ? n.nps : "not measured"}</span>
      </div>
    </div>
  );
}
