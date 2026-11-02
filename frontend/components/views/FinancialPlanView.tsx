"use client";

import { Card, Empty, ErrorState, SkeletonList } from "@/components/ui";
import { money } from "@/lib/format";
import { useApi } from "@/lib/hooks";
import { pct1 } from "@/app/workspace/components/artifacts/PlanCard";
import { ViewLink } from "./ViewNav";
import { AllClients, ViewHeader } from "./ViewHeader";

function Tile({ label, value, tone = "text-ink" }: { label: string; value: string | number; tone?: string }) {
  return (
    <Card>
      <div className="tile-label">{label}</div>
      <div className={`font-mono text-lg tabular-nums ${tone}`}>{value}</div>
    </Card>
  );
}

const th = "px-4 py-2 font-medium";
const num = "px-4 py-2 text-right font-mono text-xs tabular-nums text-ink-muted";

/** One household's plan, read beside the conversation. What it works from is recorded through Astra, because
 * each figure comes from a statement; with no household it lists whom to open. */
export function FinancialPlanView({ household = null }: { household?: string | null }) {
  const { data, loading, error, refetch } = useApi<any>(household ? `/api/plan?household=${household}` : "/api/plan", [household]);
  if (error) return <div className="view-container"><Card><ErrorState what="the financial plan" message={error} onRetry={refetch} /></Card></div>;
  // Moving between the list and one client, the last response is still here for a render.
  if (loading || !data || (data.household_id ?? null) !== household) return <div className="view-container"><SkeletonList count={4} /></div>;

  if (!household) {
    return (
      <div className="view-container">
        <ViewHeader title="Financial plan" />
        {data.households.length === 0 ? <Empty>No households.</Empty> : (
          <Card className="overflow-hidden p-0">
            <div className="divide-y divide-border-soft" data-testid="plan-clients">
              {data.households.map((h: any) => (
                <ViewLink key={h.household_id} href={`/studio/plan?household=${h.household_id}`} className="block px-4 py-2 text-sm text-ink hover:underline">{h.household}</ViewLink>
              ))}
            </div>
          </Card>
        )}
      </div>
    );
  }

  const r = data.retirement;
  const c = data.cash;
  const reserve = c.reserve;
  return (
    <div className="view-container">
      <ViewHeader title={`Financial plan · ${data.household}`} actions={<AllClients href="/studio/plan" />} />
      <div className="mb-4 grid grid-cols-2 gap-3 cq-md:grid-cols-4" data-testid="plan-summary">
        <Tile label="Probability of success" value={r ? `${Math.round(r.success_probability * 100)}%` : "—"} tone={r?.on_track ? "text-ok" : "text-warn"} />
        <Tile label="Sustainable a year" value={r ? money(r.sustainable_income) : "—"} />
        <Tile label="Withdrawal rate" value={r?.withdrawal_rate != null ? pct1(r.withdrawal_rate) : "—"} tone="text-ink-muted" />
        <Tile label="Cash reserve" value={reserve.status === "assessed" ? `${reserve.months} months` : "not assessed"} tone={reserve.status === "assessed" && reserve.months < reserve.target_months ? "text-warn" : "text-ink-muted"} />
      </div>
      <Card className="mb-4">
        <div className="flex flex-wrap gap-1.5" data-testid="plan-basis">
          <span className="chip text-ink-muted">estimates from what is on file</span>
          {r && <span className="chip text-ink-muted">{{ stated: "income stated", recorded_spending: "income from recorded spending", assumed_draw: "income assumed at 5% of the pot" }[r.income_basis as string]}</span>}
          {c.basis === "assets_only" && <span className="chip text-warn">assets only</span>}
          <span className="chip text-ink-muted">tax and survivor income not modelled</span>
        </div>
      </Card>

      {r && (
        <Card className="mb-4 overflow-hidden p-0" data-testid="plan-retirement">
          <div className="border-b border-border-soft px-4 py-2.5 text-sm font-medium text-ink">Retirement at {r.retirement_age}, to age {r.longevity_age}</div>
          <div className="grid grid-cols-2 gap-3 px-4 py-3 cq-md:grid-cols-4">
            {[["Income needed", money(r.income_need ?? r.income_target)], ["Counted on", money(r.guaranteed_income)], ["From the portfolio", money(r.income_target)], ["Projected pot", money(r.projected_at_retirement)],
              ["At the 4% rule", money(r.rule_of_thumb_income)], ["Early crash", `${Math.round(r.sequence_risk.early_crash * 100)}%`],
              ["Essentials covered", r.essentials_covered != null ? pct1(r.essentials_covered) : "—"], ["Depletes at", r.median_depletion_age ?? "—"]].map(([label, v]) => (
              <div key={label as string}><div className="tile-label">{label}</div><div className="font-mono text-sm tabular-nums text-ink">{v}</div></div>
            ))}
          </div>
          <div className="flex flex-wrap gap-1.5 border-t border-border-soft px-4 py-2.5">
            {r.levers.map((l: any) => <span key={l.key} className="chip text-ink-muted">{l.label}: {Math.round(l.success * 100)}%</span>)}
          </div>
        </Card>
      )}

      <Card className="mb-4 overflow-hidden p-0">
        <div className="border-b border-border-soft px-4 py-2.5 text-sm font-medium text-ink">Income to count on</div>
        {data.streams.length === 0 ? <div className="px-4 py-3"><span className="chip text-ink-muted">none on file</span></div> : (
          <div className="overflow-x-auto">
            <table className="w-full text-sm" data-testid="plan-streams">
              <thead><tr className="text-left text-[10px] uppercase tracking-wide text-ink-muted"><th className={th}>Income</th><th className={`${th} text-right`}>A year</th><th className={`${th} text-right`}>From age</th><th className={th}>Choices</th></tr></thead>
              <tbody className="divide-y divide-border-soft">
                {data.streams.map((s: any) => (
                  <tr key={s.id} title={s.document}>
                    <td className="px-4 py-2 text-ink">{s.name}<span className="ml-1 text-xs text-ink-muted">{s.person}</span></td>
                    <td className={num}>{money(s.planned_annual)}</td>
                    <td className={num}>{s.start_age}</td>
                    <td className="px-4 py-2">
                      <div className="flex flex-wrap gap-1.5">
                        {s.claiming?.claims.map((x: any) => <span key={x.age} className="chip text-ink-muted" title={x.breaks_even ? Object.entries(x.breaks_even).map(([k, v]) => `beats ${k} by ${v}`).join(", ") : undefined}>at {x.age}: {money(x.annual)}</span>)}
                        {s.pension_options?.lump_sum != null && <span className="chip text-ink-muted">lump {money(s.pension_options.lump_sum)} · {s.pension_options.years_to_match_lump}y{s.pension_options.implied_return != null ? ` · ${pct1(s.pension_options.implied_return)} implied` : ""}</span>}
                        {s.pension_options?.joint_annual != null && <span className="chip text-ink-muted">joint {money(s.pension_options.joint_annual)} · costs {pct1(s.pension_options.cost_of_joint)}</span>}
                      </div>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </Card>

      <Card className="mb-4 overflow-hidden p-0">
        <div className="flex flex-wrap items-center justify-between gap-2 border-b border-border-soft px-4 py-2.5">
          <div className="text-sm font-medium text-ink">Cash and debts</div>
          <div className="flex flex-wrap gap-1.5">
            <span className="chip text-ink-muted">{money(reserve.cash)} cash</span>
            {reserve.status === "assessed" && reserve.short_by > 0 && <span className="chip text-warn">short {money(reserve.short_by)} of {reserve.target_months} months</span>}
            {c.cash_flow.status === "assessed" && <span className={`chip ${c.cash_flow.surplus < 0 ? "text-crit" : "text-ok"}`}>{money(c.cash_flow.surplus)}/yr surplus</span>}
          </div>
        </div>
        {c.debts.length === 0 ? <div className="px-4 py-3"><span className="chip text-ink-muted">no debt on file</span></div> : (
          <div className="overflow-x-auto">
            <table className="w-full text-sm" data-testid="plan-debts">
              <thead><tr className="text-left text-[10px] uppercase tracking-wide text-ink-muted"><th className={th}>Debt</th><th className={`${th} text-right`}>Owed</th><th className={`${th} text-right`}>Rate</th><th className={`${th} text-right`}>Clears in</th><th className={`${th} text-right`}>Interest left</th><th className={th}>Status</th></tr></thead>
              <tbody className="divide-y divide-border-soft">
                {c.debts.map((d: any) => (
                  <tr key={d.id}>
                    <td className="px-4 py-2 text-ink">{d.name}</td>
                    <td className={num}>{money(d.balance)}</td>
                    <td className={num}>{d.rate != null ? pct1(d.rate) : "—"}</td>
                    <td className={num}>{d.years_to_clear != null ? `${d.years_to_clear}y` : "—"}</td>
                    <td className={num}>{d.interest_left != null ? money(d.interest_left) : "—"}</td>
                    <td className="px-4 py-2">{d.status === "assessed" ? d.dearer_than_the_portfolio && <span className="chip text-warn">dearer than the portfolio</span> : <span className="chip text-ink-muted">not assessed</span>}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </Card>

      <Card className="mb-4 overflow-hidden p-0">
        <div className="border-b border-border-soft px-4 py-2.5 text-sm font-medium text-ink">Net worth, projected at {pct1(c.expected_return)}</div>
        <div className="overflow-x-auto">
          <table className="w-full text-sm" data-testid="plan-projection">
            <thead><tr className="text-left text-[10px] uppercase tracking-wide text-ink-muted"><th className={th}>Year</th><th className={`${th} text-right`}>Invested</th><th className={`${th} text-right`}>Owed</th><th className={`${th} text-right`}>Net worth</th></tr></thead>
            <tbody className="divide-y divide-border-soft">
              {c.projection.filter((_: any, i: number) => i % 3 === 0 || i === c.projection.length - 1).map((y: any) => (
                <tr key={y.year}><td className="px-4 py-2 font-mono text-xs text-ink">{y.year}</td><td className={num}>{money(y.invested)}</td><td className={num}>{money(y.owed)}</td><td className={num}>{money(y.net_worth)}</td></tr>
              ))}
            </tbody>
          </table>
        </div>
      </Card>

      {data.education.length > 0 && (
        <Card className="overflow-hidden p-0">
          <div className="border-b border-border-soft px-4 py-2.5 text-sm font-medium text-ink">Education</div>
          <div className="overflow-x-auto">
            <table className="w-full text-sm" data-testid="plan-education">
              <thead><tr className="text-left text-[10px] uppercase tracking-wide text-ink-muted"><th className={th}>For</th><th className={`${th} text-right`}>From</th><th className={`${th} text-right`}>Total cost</th><th className={`${th} text-right`}>Funded</th><th className={`${th} text-right`}>To save a year</th></tr></thead>
              <tbody className="divide-y divide-border-soft">
                {data.education.map((e: any) => (
                  <tr key={e.goal} title={e.cost_source}>
                    <td className="px-4 py-2 text-ink">{e.student ?? e.goal}</td>
                    <td className={num}>{e.start_year ?? "—"}</td>
                    <td className={num}>{e.total_cost != null ? money(e.total_cost) : "not assessed"}</td>
                    <td className={num}>{e.funded != null ? pct1(e.funded) : "—"}</td>
                    <td className={num}>{e.saving_a_year != null ? money(e.saving_a_year) : "—"}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </Card>
      )}
    </div>
  );
}
