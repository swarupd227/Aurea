"use client";

import { Card, Empty, ErrorState, SkeletonList } from "@/components/ui";
import { money } from "@/lib/format";
import { useApi } from "@/lib/hooks";
import { bandChip, pct1 } from "@/app/workspace/components/artifacts/PlanCard";
import { ScenariosTable } from "@/app/workspace/components/artifacts/ScenarioCard";
import { SocialSecurityTable } from "@/app/workspace/components/artifacts/SocialSecurityCard";
import { agesOfLabel, basisLabel, planToLabel, RATE_ASSUMED, returnsLabel } from "@/lib/planBasis";
import { PlanInputsChips, SequenceIllustration } from "@/components/PlanInputs";
import { FiguresTable, FreshnessChip, PlanStateChip, ReviewNeedChip } from "@/app/workspace/components/artifacts/PlanRecordCard";
import { reviewDay } from "@/app/workspace/components/artifacts/ReviewsCard";
import { withRef } from "@/lib/refs";
import { ViewLink, useViewNav } from "./ViewNav";
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

/** Social Security for a US couple, pair by pair (B-184). It lays the claims out; the claim is the client's. */
function PlanSocialSecurity({ household }: { household: string }) {
  const { data } = useApi<any>(`/api/plan/social-security?household=${household}`, [household]);
  if (!data || data.status === "not_assessed" || !(data.pairs ?? []).length) return null;
  return (
    <Card className="mb-4 overflow-hidden p-0" data-testid="plan-social-security">
      <div className="flex flex-wrap items-center gap-2 border-b border-border-soft px-4 py-2.5">
        <div className="text-sm font-medium text-ink">Social Security</div>
        <span className="chip text-ink-muted">today&rsquo;s money</span>
        <span className="chip text-ink-muted">lays out · the claim is the client&rsquo;s</span>
        {data.status === "one_statement" && <span className="chip text-warn">one statement · spousal and survivor not assessed</span>}
      </div>
      <SocialSecurityTable result={data} />
    </Card>
  );
}

/** The household's saved scenarios, ranked (B-179). They are run and saved in the conversation. */
function PlanScenarios({ household }: { household: string }) {
  const { data } = useApi<any>(`/api/plan/scenarios?household=${household}`, [household]);
  const rows: any[] = data?.scenarios ?? [];
  if (!rows.length) return null;
  return (
    <Card className="mb-4 overflow-hidden p-0" data-testid="plan-scenarios">
      <div className="flex flex-wrap items-center gap-2 border-b border-border-soft px-4 py-2.5">
        <div className="text-sm font-medium text-ink">Scenarios</div>
        <span className="chip text-ink-muted">ranked by points per 10k given up</span>
        <span className="chip text-ink-muted">saving changes no record</span>
      </div>
      <ScenariosTable rows={rows} />
    </Card>
  );
}
const num = "px-4 py-2 text-right font-mono text-xs tabular-nums text-ink-muted";

const EVENT_KIND: Record<string, string> = { baseline: "Baseline", review: "Review", delivery: "Delivery" };

/** The household's plan of record (B-181): where it stands, worked out from what is recorded; the baseline's frozen
 * figures beside today's; the delivered plan, its freshness and tasks; and every event. A baseline, a review and a
 * delivery are recorded through the conversation: in the pane these buttons are sentences handed to it, so each goes
 * through its tool and Confirm card. Readers outside the record's roles get nothing back, and see nothing. */
function PlanRecordSection({ household }: { household: string }) {
  const nav = useViewNav();
  const { data, error } = useApi<any>(`/api/plan-record?household=${household}`, [household]);
  if (error || !data || data.household_id !== household) return null;
  const st = data.state as string;
  const say = nav.say;
  const name = data.household as string;
  const ask = (sentence: string) => say?.(withRef(sentence, "household", data.household_id));
  const canBaseline = data.can_prepare && data.data.missing.length === 0 && (["data_complete", "delivered", "implementing", "complete"].includes(st) || !!data.sent_back);
  const canReview = data.can_review && ["baseline", "scenarios", "in_review"].includes(st);
  const canDeliver = data.can_prepare && (st === "reviewed" || (["baseline", "scenarios"].includes(st) && !data.review_need?.needed));
  const record = data.plan_of_record;
  const c = data.complexity;
  return (
    <Card className="mb-4 overflow-hidden p-0" data-testid="plan-record">
      <div className="flex flex-wrap items-center justify-between gap-2 border-b border-border-soft px-4 py-2.5">
        <div className="flex flex-wrap items-center gap-1.5">
          <div className="mr-1 text-sm font-medium text-ink">Plan of record</div>
          <PlanStateChip state={st} />
          {data.version ? <span className="chip tabular-nums text-ink-muted">v{data.version}</span> : null}
          <span className="chip text-ink-muted">{data.owner} next</span>
          <ReviewNeedChip need={data.review_need} />
          <FreshnessChip f={data.freshness} />
        </div>
        {say && (
          <div className="flex flex-wrap gap-1.5">
            {canBaseline && <button className="btn-outline text-xs" data-testid="plan-record-baseline" onClick={() => ask(`Record a plan baseline for ${name}.`)}>Record baseline</button>}
            {canReview && <button className="btn-outline text-xs" data-testid="plan-record-review" onClick={() => ask(`Record my review of ${name}'s plan baseline.`)}>Record review</button>}
            {canDeliver && <button className="btn-outline text-xs" data-testid="plan-record-deliver" onClick={() => ask(`Record that ${name}'s plan was delivered.`)}>Record delivery</button>}
          </div>
        )}
      </div>
      <div className="flex flex-wrap gap-1.5 px-4 py-2.5" data-testid="plan-record-facts">
        <span className="chip tabular-nums text-ink-muted">facts {data.data.on_file}/{data.data.read}</span>
        {data.data.missing.map((m: any) => <span key={m.text} className="chip text-warn">missing {m.text}</span>)}
        <span className="chip tabular-nums text-ink-muted" title={c.parts.filter((p: any) => p.count).map((p: any) => `${p.count} ${p.part.replace(/_/g, " ")}`).join(", ")}>complexity {c.score}</span>
        {data.scenarios_since_baseline > 0 && <span className="chip tabular-nums text-ink-muted">{data.scenarios_since_baseline} scenarios since baseline</span>}
        {data.sent_back && <span className="chip text-warn" title={data.sent_back.findings ?? undefined}>sent back {reviewDay(data.sent_back.happened_on)} · new baseline needed</span>}
        {data.review && <span className={`chip ${data.review.outcome === "passed" ? "text-ok" : "text-warn"}`}>review {data.review.outcome} · {data.review.recorded_by}</span>}
      </div>
      {data.baseline && (
        <div className="border-t border-border-soft">
          <div className="flex flex-wrap gap-1.5 px-4 pt-2.5">
            <span className="chip text-ink-muted">baseline {reviewDay(data.baseline.happened_on)} · {data.baseline.recorded_by}</span>
            <span className="chip text-ink-muted">assumptions {data.baseline.figures?.assumption_version}</span>
            <span className="chip text-ink-muted">estimates</span>
          </div>
          <FiguresTable frozen={data.baseline.figures} today={data.today} differences={data.differences} />
        </div>
      )}
      {record && (
        <div className="border-t border-border-soft" data-testid="plan-record-delivered">
          <div className="flex flex-wrap gap-1.5 px-4 py-2.5">
            <span className="chip text-ok">v{record.version} delivered {reviewDay(record.happened_on)}</span>
            <span className="chip text-ink-muted" title={record.evidence ?? undefined}>{record.evidence_kind}</span>
            <span className="chip text-ink-muted">figures frozen · assumptions {record.figures?.assumption_version}</span>
            <span className="chip text-ink-muted">{record.review?.needed ? `reviewed ${reviewDay(record.review.on)} · ${record.review.by}` : "no review needed"}</span>
          </div>
          {record.tasks.length > 0 && (
            <div className="overflow-x-auto">
              <table className="w-full text-sm" data-testid="plan-record-tasks">
                <thead><tr className="text-left text-[10px] uppercase tracking-wide text-ink-muted"><th className={th}>Action item</th><th className={th}>For</th><th className={th}>Due</th><th className={th}>Status</th></tr></thead>
                <tbody className="divide-y divide-border-soft">
                  {record.tasks.map((t: any) => (
                    <tr key={t.id}>
                      <td className="px-4 py-2 text-ink">{t.title}</td>
                      <td className="px-4 py-2 text-xs text-ink-muted">{t.assigned_to ?? "unassigned"}</td>
                      <td className={`px-4 py-2 font-mono text-xs tabular-nums ${t.overdue ? "text-crit" : "text-ink-muted"}`}>{t.due_date ? reviewDay(t.due_date) : "—"}</td>
                      <td className="px-4 py-2"><span className={`chip ${t.status === "open" ? "text-info" : "text-ink-muted"}`}>{t.status}</span></td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}
        </div>
      )}
      {data.history.length > 0 && (
        <div className="overflow-x-auto border-t border-border-soft">
          <table className="w-full text-sm" data-testid="plan-record-history">
            <thead><tr className="text-left text-[10px] uppercase tracking-wide text-ink-muted"><th className={th}>Event</th><th className={`${th} text-right`}>Version</th><th className={th}>On</th><th className={th}>By</th><th className={th}>Outcome</th></tr></thead>
            <tbody className="divide-y divide-border-soft">
              {data.history.map((e: any) => (
                <tr key={e.id}>
                  <td className="px-4 py-2 text-ink">{EVENT_KIND[e.kind] ?? e.kind}</td>
                  <td className={num}>v{e.version}</td>
                  <td className="px-4 py-2 font-mono text-xs tabular-nums text-ink-muted">{reviewDay(e.happened_on)}</td>
                  <td className="px-4 py-2 text-xs text-ink-muted">{e.recorded_by}</td>
                  <td className="px-4 py-2">
                    {e.outcome && <span className={`chip ${e.outcome === "passed" ? "text-ok" : "text-warn"}`} title={e.findings ?? undefined}>{e.outcome}</span>}
                    {e.evidence_kind && <span className="chip text-ink-muted" title={e.evidence ?? undefined}>{e.evidence_kind}{e.action_items ? ` · ${e.action_items} items` : ""}</span>}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </Card>
  );
}

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
  const f = data.funded;
  return (
    <div className="view-container">
      <ViewHeader title={`Financial plan · ${data.household}`} actions={<AllClients href="/studio/plan" />} />
      <div className="mb-4 grid grid-cols-2 gap-3 cq-md:grid-cols-5" data-testid="plan-summary">
        <Tile label="Probability of success" value={r ? `${Math.round(r.success_probability * 100)}%` : "—"} tone={r?.on_track ? "text-ok" : "text-warn"} />
        <Tile label="Funded ratio" value={f?.ratio != null ? f.ratio.toFixed(2) : "—"} tone={f?.ratio != null && f.ratio < 1 ? "text-warn" : "text-ink"} />
        <Tile label="Sustainable a year" value={r ? money(r.sustainable_income) : "—"} />
        <Tile label="Withdrawal rate" value={r?.withdrawal_rate != null ? pct1(r.withdrawal_rate) : "—"} tone="text-ink-muted" />
        <Tile label="Cash reserve" value={reserve.status === "assessed" ? `${reserve.months} months` : "not assessed"} tone={reserve.status === "assessed" && reserve.months < reserve.target_months ? "text-warn" : "text-ink-muted"} />
      </div>
      <Card className="mb-4">
        <div className="flex flex-wrap gap-1.5" data-testid="plan-basis">
          <span className="chip text-ink-muted">estimates from what is on file</span>
          <span className="chip text-ink-muted" data-testid="plan-returns-source">{returnsLabel(data.assumptions_source)}</span>
          {data.assumptions_version != null && <span className="chip tabular-nums text-ink-muted">assumptions v{data.assumptions_version}</span>}
          {r?.band && bandChip[r.band] && <span className={`chip ${bandChip[r.band][1]}`} data-testid="plan-band">{bandChip[r.band][0]}</span>}
          {f?.ratio != null && (
            <span className="chip tabular-nums text-ink-muted" data-testid="plan-funded">
              {money(f.assets)} book / {money(f.pv_needs)} needs today · {pct1(f.discount_rate)} {f.discount_whose === "firm" ? "firm rate" : "cash return"}
            </span>
          )}
          {f?.ratio != null && <span className="chip text-ink-muted">contributions, earnings and held away not counted</span>}
          {f?.tiers === "not_on_file" && <span className="chip text-ink-muted">no goal tiers · every goal counted</span>}
          {r?.failed_shortfall_today ? (
            <span className="chip tabular-nums text-warn">failed paths · out at {r.median_depletion_age} · {money(r.failed_shortfall_today)} unpaid</span>
          ) : null}
          {r && <span className="chip text-ink-muted" data-testid="plan-money-basis">today&rsquo;s money</span>}
          {r && <span className="chip text-ink-muted">{{ stated: "income stated", recorded_spending: "income from recorded spending", assumed_draw: "income assumed at 5% of the pot" }[r.income_basis as string]}</span>}
          {c.basis === "assets_only" && <span className="chip text-warn">assets only</span>}
          <span className="chip text-ink-muted">tax and survivor income not modelled</span>
          {(data.assumption_basis ?? []).map((b: any) => (
            <span key={b.key} className="chip text-ink-muted" data-testid="plan-market-basis" title={`${b.source} · ${b.basis}`}>{basisLabel(b)}</span>
          ))}
        </div>
      </Card>

      <PlanRecordSection household={household} />

      {data.inputs && (
        <Card className="mb-4 overflow-hidden p-0" data-testid="plan-inputs-card">
          <div className="border-b border-border-soft px-4 py-2.5 text-sm font-medium text-ink">What it works from</div>
          <div className="grid grid-cols-2 gap-3 px-4 py-3 cq-md:grid-cols-4">
            {[["Oldest input", data.inputs.oldest ? `${data.inputs.oldest.days}d` : "—"],
              [`Older than ${data.inputs.line_days}d`, `${data.inputs.older_than_line} of ${data.inputs.inputs}`],
              ["Undated", data.inputs.undated],
              ["Facts on file", data.inputs.completeness?.read ? `${data.inputs.completeness.on_file}/${data.inputs.completeness.read}` : "—"]].map(([label, v]) => (
              <div key={label as string}><div className="tile-label">{label}</div><div className="font-mono text-sm tabular-nums text-ink">{v}</div></div>
            ))}
          </div>
          <div className="border-t border-border-soft px-4 py-2.5">
            <PlanInputsChips inputs={data.inputs} />
          </div>
        </Card>
      )}

      {r && (
        <Card className="mb-4 overflow-hidden p-0" data-testid="plan-retirement">
          <div className="flex flex-wrap items-center justify-between gap-2 border-b border-border-soft px-4 py-2.5">
            <div className="text-sm font-medium text-ink" data-testid="plan-retirement-span">Retirement at {r.retirement_age}, {planToLabel(r)}</div>
            <div className="flex flex-wrap gap-1.5">
              {agesOfLabel(r) && <span className="chip text-ink-muted">{agesOfLabel(r)}</span>}
              {r.plan_to?.assumed && <span className="chip text-warn">plan-to age assumed</span>}
            </div>
          </div>
          <div className="grid grid-cols-2 gap-3 px-4 py-3 cq-md:grid-cols-4">
            {[["Income needed", money(r.income_need ?? r.income_target)], ["Counted on", money(r.guaranteed_income)], ["From the portfolio", money(r.income_target)], ["Projected pot", money(r.projected_at_retirement_today ?? r.projected_at_retirement)],
              ["At the 4% rule", money(r.rule_of_thumb_income)], ["Early crash", `${Math.round(r.sequence_risk.early_crash * 100)}%`],
              ["Essentials covered", r.essentials_covered != null ? pct1(r.essentials_covered) : "—"], ["Depletes at", r.median_depletion_age ?? "—"]].map(([label, v]) => (
              <div key={label as string}><div className="tile-label">{label}</div><div className="font-mono text-sm tabular-nums text-ink">{v}</div></div>
            ))}
          </div>
          <div className="flex flex-wrap gap-1.5 border-t border-border-soft px-4 py-2.5">
            {r.levers.map((l: any) => <span key={l.key} className="chip text-ink-muted">{l.label}: {Math.round(l.success * 100)}%</span>)}
          </div>
          {r.sequence_risk?.illustration && (
            <div className="border-t border-border-soft px-4 py-2.5">
              <SequenceIllustration ill={r.sequence_risk.illustration} testid="plan-sequence-illustration" />
            </div>
          )}
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

      <PlanSocialSecurity household={household} />
      <PlanScenarios household={household} />

      {data.education.length > 0 && (
        <Card className="overflow-hidden p-0">
          <div className="border-b border-border-soft px-4 py-2.5 text-sm font-medium text-ink">Education</div>
          <div className="overflow-x-auto">
            <table className="w-full text-sm" data-testid="plan-education">
              <thead><tr className="text-left text-[10px] uppercase tracking-wide text-ink-muted"><th className={th}>For</th><th className={`${th} text-right`}>From</th><th className={`${th} text-right`}>Total cost</th><th className={`${th} text-right`}>Likelihood</th><th className={`${th} text-right`}>In 529s</th><th className={th}>Basis</th></tr></thead>
              <tbody className="divide-y divide-border-soft">
                {data.education.map((e: any) => (
                  <tr key={e.goal} title={e.cost_source}>
                    <td className="px-4 py-2 text-ink">{e.student ?? e.goal}</td>
                    <td className={num}>{e.start_year ?? "—"}</td>
                    <td className={num}>{e.total_cost != null ? money(e.total_cost) : "not assessed"}</td>
                    <td className={e.likelihood == null ? num : num.replace("text-ink-muted", e.on_track ? "text-ok" : "text-warn")}>{e.likelihood != null ? `${Math.round(e.likelihood * 100)}%` : "—"}</td>
                    <td className={num}>{money(e.in_529 ?? 0)}</td>
                    <td className="px-4 py-2">
                      <div className="flex flex-wrap gap-1.5">
                        {e.inflation != null && <span className="chip text-ink-muted">{money(e.total_cost_today)} today · +{pct1(e.inflation)}/yr</span>}
                        {(e.assumed || []).filter((k: string) => RATE_ASSUMED[k]).map((k: string) => <span key={k} className="chip text-warn">{RATE_ASSUMED[k]}</span>)}
                      </div>
                    </td>
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
