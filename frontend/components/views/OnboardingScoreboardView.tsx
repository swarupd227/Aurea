"use client";

import { Card, ErrorState, SkeletonList } from "@/components/ui";
import { useApi } from "@/lib/hooks";
import { ViewHeader } from "./ViewHeader";

const th = "px-4 py-2 font-medium";
const days = (x: number | null | undefined) => (x == null ? "—" : `${x}d`);

/** Onboarding, measured from the record: time to invest, where it stalls, screening, NPS, rejects, capacity and who does what. */
export function OnboardingScoreboardView() {
  const { data, loading, error, refetch } = useApi<any>("/api/onboarding-scoreboard", []);
  if (error) return <div className="view-container"><Card><ErrorState what="the onboarding scoreboard" message={error} onRetry={refetch} /></Card></div>;
  if (loading || !data) return <div className="view-container"><SkeletonList count={4} /></div>;
  const d = data.days_to_invest, n = data.nps, sc = data.screening, cap = data.capacity, ab = data.abandonment;
  const tiles: [string, string][] = [
    ["Days to invest, median", d.n ? days(d.p50) : "—"], ["NPS", n.nps != null ? `${n.nps} (${n.responses})` : "not measured"],
    ["Screening alert rate", sc.alert_rate_pct != null ? `${sc.alert_rate_pct}%` : "—"], ["Accounts per ops person, 90d", cap.accounts_per_operations_person != null ? String(cap.accounts_per_operations_person) : "—"],
  ];
  return (
    <div className="view-container">
      <ViewHeader title="Onboarding scoreboard" />
      <div className="mb-4 grid grid-cols-2 gap-3 sm:grid-cols-4" data-testid="scoreboard-tiles">
        {tiles.map(([label, v]) => <Card key={label}><div className="text-[10px] uppercase tracking-wide text-ink-muted">{label}</div><div className="font-mono text-xl text-ink">{v}</div></Card>)}
      </div>
      <Card className="mb-4"><div className="flex flex-wrap gap-1.5" data-testid="scoreboard-clocks">
        <span className="chip">Paperwork p50 {days(data.clocks.paperwork.p50)}</span>
        <span className="chip">Money p50 {days(data.clocks.money.p50)}</span>
        <span className="chip">Invested p50 {days(data.clocks.invested.p50)}</span>
        {(data.clocks.out_of_order ?? []).length > 0 && (
          <span className="chip text-warn" data-testid="scoreboard-out-of-order">{data.clocks.out_of_order.length} dates out of order</span>
        )}
        <span className="chip">Days to invest p90 {days(d.p90)}</span>
        <span className="chip text-warn">{ab.stalled} stalled over {ab.stalled_days}d</span>
        <span className="chip">{ab.rejected} rejected of {ab.cases}</span>
        {data.transfer_rejects.map((r: any) => <span key={r.code} className="chip text-warn">{r.label} · {r.count}</span>)}
      </div></Card>
      <Card className="mb-4 overflow-hidden p-0">
        <div className="border-b border-border-soft px-4 py-2 text-sm font-medium text-ink">By step</div>
        <div className="overflow-x-auto"><table className="w-full text-sm" data-testid="scoreboard-steps">
          <thead><tr className="text-left text-[10px] uppercase tracking-wide text-ink-muted"><th className={th}>Step</th><th className={`${th} text-right`}>Reached</th><th className={`${th} text-right`}>Stalled here</th></tr></thead>
          <tbody className="divide-y divide-border-soft">
            {ab.by_step.map((s: any) => (
              <tr key={s.state}><td className="px-4 py-2 text-ink">{s.state.replace(/_/g, " ")}</td><td className="px-4 py-2 text-right font-mono text-xs">{s.reached}</td>
                <td className={`px-4 py-2 text-right font-mono text-xs ${s.stalled ? "text-warn" : "text-ink-muted"}`}>{s.stalled}</td></tr>
            ))}
          </tbody>
        </table></div>
      </Card>
      <Card className="overflow-hidden p-0">
        <div className="border-b border-border-soft px-4 py-2 text-sm font-medium text-ink">Who does what</div>
        <div className="overflow-x-auto"><table className="w-full text-sm" data-testid="scoreboard-raci">
          <thead><tr className="text-left text-[10px] uppercase tracking-wide text-ink-muted"><th className={th}>Activity</th><th className={th}>Responsible</th><th className={th}>Accountable</th><th className={th}>Consulted</th><th className={th}>Informed</th></tr></thead>
          <tbody className="divide-y divide-border-soft">
            {data.raci.map((r: any) => (
              <tr key={r.activity}><td className="px-4 py-2 text-ink">{r.activity}</td><td className="px-4 py-2 text-xs">{r.R}</td><td className="px-4 py-2 text-xs">{r.A}</td>
                <td className="px-4 py-2 text-xs text-ink-muted">{r.C}</td><td className="px-4 py-2 text-xs text-ink-muted">{r.I}</td></tr>
            ))}
          </tbody>
        </table></div>
      </Card>
    </div>
  );
}
