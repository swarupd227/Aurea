import { money } from "@/lib/format";
import { returnsLabel, targetBasisLabel } from "@/lib/planBasis";
import { TierChip, TiersRow } from "@/components/GoalTiers";

// A goal after `update_goal`, as the goals page now reads it (goal_tradeoff.likelihoods): one goal, one likelihood.

export const isGoalUpdated = (r: any) => !!r?.goal && typeof r.goal.success_probability === "number";

export function GoalUpdatedCard({ result }: { result: any }) {
  const g = result.goal;
  const basis = targetBasisLabel(g);
  return (
    <div className="overflow-hidden rounded-lg border border-border bg-surface" data-testid="goal-updated-card">
      <div className="flex flex-wrap items-center justify-between gap-2 border-b border-border-soft px-3 py-2">
        <div className="text-sm font-medium text-ink">Goal · {g.name}</div>
        <div className="flex flex-wrap gap-1.5">
          <span className="chip text-ink-muted">estimate</span>
          <span className="chip text-ink-muted">{returnsLabel(result.assumptions_source)}</span>
        </div>
      </div>
      <div className="grid grid-cols-3 gap-3 px-3 py-2.5">
        {[
          ["Likelihood", `${Math.round(g.success_probability * 100)}%`, g.on_track ? "text-ok" : "text-warn"],
          ["Target by its date", money(g.target_amount), "text-ink"],
          ["Years", g.years ?? "—", "text-ink-muted"],
        ].map(([label, v, tone]: any) => (
          <div key={label}>
            <div className="tile-label">{label}</div>
            <div className={`font-mono text-sm tabular-nums ${tone}`}>{v}</div>
          </div>
        ))}
      </div>
      <div className="flex flex-wrap gap-1.5 border-t border-border-soft px-3 py-2" data-testid="goal-updated-basis">
        <TierChip tier={g.tier} />
        {g.retirement_age != null && <span className="chip tabular-nums text-ink-muted">retire at {g.retirement_age}</span>}
        {g.monthly_contribution != null && <span className="chip tabular-nums text-ink-muted">saving {money(g.monthly_contribution)} a month</span>}
        {g.contribution_basis === "sip_registration" && <span className="chip text-ink-muted">projected from its SIP registration</span>}
        {g.funding_share != null && <span className="chip tabular-nums text-ink-muted">share {Math.round(g.funding_share * 100)}%</span>}
        {basis ? <span className="chip text-ink-muted">{basis}</span> : <span className="chip text-ink-muted">target as stated</span>}
        {/* The yearly figure follows the monthly one, and `annual_income` is a retirement goal's spending. */}
        {Object.keys(result.updates || {}).filter((k) => k !== "annual_contribution").map((k) => (
          <span key={k} className="chip text-ink-muted">{k === "annual_income" ? "retirement spending" : k.replace(/_/g, " ")} changed</span>
        ))}
      </div>
      {result.tiers && <div className="border-t border-border-soft px-3 py-2"><TiersRow tiers={result.tiers} /></div>}
    </div>
  );
}
