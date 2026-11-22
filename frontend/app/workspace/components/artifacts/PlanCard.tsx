import { money } from "@/lib/format";
import { basisLabel, returnsLabel } from "@/lib/planBasis";
import { PlanInputsChips } from "@/components/PlanInputs";

export const pct1 = (v: number) => `${(v * 100).toFixed(1)}%`;

// Where a likelihood sits against the firm's band (B-177): above the ceiling reads possibly over-saving.
export const bandChip: Record<string, [string, string]> = {
  below: ["below the band", "text-warn"],
  within: ["within the band", "text-ok"],
  above: ["above the band · possibly over-saving", "text-info"],
};

export function PlanCard({ result }: { result: any }) {
  const r = result.retirement;
  const reserve = result.cash.reserve;
  const debts: any[] = result.cash.debts;
  const f = result.funded;
  return (
    <div className="overflow-hidden rounded-lg border border-border bg-surface" data-testid="plan-card">
      <div className="flex flex-wrap items-center justify-between gap-2 border-b border-border-soft px-3 py-2">
        <div className="text-sm font-medium text-ink">Financial plan · {result.household}</div>
        <div className="flex flex-wrap gap-1.5">
          <span className="chip text-ink-muted">estimates</span>
          <span className="chip text-ink-muted" data-testid="plan-card-returns-source">{returnsLabel(result.assumptions_source)}</span>
          {r && <span className="chip text-ink-muted">today&rsquo;s money</span>}
          {(result.assumption_basis ?? []).length > 0 && (
            <span className="chip text-ink-muted" title={(result.assumption_basis as any[]).map((b) => `${basisLabel(b)}: ${b.source}`).join("\n")}>
              market&rsquo;s suggested assumptions
            </span>
          )}
        </div>
      </div>
      <div className="grid grid-cols-2 gap-3 px-3 py-2.5 sm:grid-cols-5">
        {[
          ["Success", r ? `${Math.round(r.success_probability * 100)}%` : "—", r?.on_track ? "text-ok" : "text-warn"],
          ["Funded", f?.ratio != null ? f.ratio.toFixed(2) : "—", f?.ratio != null && f.ratio < 1 ? "text-warn" : "text-ink"],
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
      {(r?.band || f?.ratio != null || r?.failed_shortfall_today) && (
        <div className="flex flex-wrap gap-1.5 border-t border-border-soft px-3 py-2" data-testid="plan-card-funded">
          {r?.band && bandChip[r.band] && <span className={`chip ${bandChip[r.band][1]}`}>{bandChip[r.band][0]}</span>}
          {f?.ratio != null && (
            <span className="chip tabular-nums text-ink-muted">
              {money(f.assets)} / {money(f.pv_needs)} · {pct1(f.discount_rate)} {f.discount_whose === "firm" ? "firm rate" : "cash return"}
            </span>
          )}
          {f?.tiers === "not_on_file" && <span className="chip text-ink-muted">no goal tiers · every goal counted</span>}
          {r?.failed_shortfall_today ? (
            <span className="chip tabular-nums text-warn">failed paths · out at {r.median_depletion_age} · {money(r.failed_shortfall_today)} unpaid</span>
          ) : null}
        </div>
      )}
      {result.inputs && (
        <div className="border-t border-border-soft px-3 py-2">
          <PlanInputsChips inputs={result.inputs} testid="plan-card-inputs" />
        </div>
      )}
      {result.education.length > 0 && (
        <div className="flex flex-wrap gap-1.5 border-t border-border-soft px-3 py-2">
          {result.education.map((e: any) => <span key={e.goal} className="chip text-ink-muted">{e.student ?? e.goal}: {e.likelihood != null ? `${Math.round(e.likelihood * 100)}% likely` : "not assessed"}</span>)}
        </div>
      )}
    </div>
  );
}
