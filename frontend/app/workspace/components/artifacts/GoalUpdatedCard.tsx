import { money } from "@/lib/format";
import { returnsLabel, targetBasisLabel } from "@/lib/planBasis";

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
        {basis ? <span className="chip text-ink-muted">{basis}</span> : <span className="chip text-ink-muted">target as stated</span>}
        {Object.keys(result.updates || {}).map((k) => <span key={k} className="chip text-ink-muted">{k.replace(/_/g, " ")} changed</span>)}
      </div>
    </div>
  );
}
