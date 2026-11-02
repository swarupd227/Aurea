import { money } from "@/lib/format";

export const pct1 = (v: number) => `${(v * 100).toFixed(1)}%`;

export function PlanCard({ result }: { result: any }) {
  const r = result.retirement;
  const reserve = result.cash.reserve;
  const debts: any[] = result.cash.debts;
  return (
    <div className="overflow-hidden rounded-lg border border-border bg-surface" data-testid="plan-card">
      <div className="flex flex-wrap items-center justify-between gap-2 border-b border-border-soft px-3 py-2">
        <div className="text-sm font-medium text-ink">Financial plan · {result.household}</div>
        <span className="chip text-ink-muted">estimates</span>
      </div>
      <div className="grid grid-cols-2 gap-3 px-3 py-2.5 sm:grid-cols-4">
        {[
          ["Success", r ? `${Math.round(r.success_probability * 100)}%` : "—", r?.on_track ? "text-ok" : "text-warn"],
          ["Sustainable", r ? `${money(r.sustainable_income)}/yr` : "—", "text-ink"],
          ["Reserve", reserve.status === "assessed" ? `${reserve.months} months` : "not assessed", reserve.status === "assessed" && reserve.months < reserve.target_months ? "text-warn" : "text-ink-muted"],
          ["Debts", debts.length || "none on file", "text-ink-muted"],
        ].map(([label, v, tone]: any) => (
          <div key={label}>
            <div className="tile-label">{label}</div>
            <div className={`font-mono text-sm tabular-nums ${tone}`}>{v}</div>
          </div>
        ))}
      </div>
      {result.education.length > 0 && (
        <div className="flex flex-wrap gap-1.5 border-t border-border-soft px-3 py-2">
          {result.education.map((e: any) => <span key={e.goal} className="chip text-ink-muted">{e.student}: {e.status === "assessed" ? `${pct1(e.funded)} funded` : "not assessed"}</span>)}
        </div>
      )}
    </div>
  );
}
