"use client";

import { ReactNode, useState } from "react";
import {
  TrendingUp, Users, Wallet, ShieldCheck, Activity, AlertTriangle, Target,
  PiggyBank, Database, BarChart3, Zap, Clock, CheckCircle2, ClipboardList,
} from "lucide-react";
import { Card, ErrorState, SkeletonList } from "@/components/ui";
import { AllocationDonut } from "@/components/Charts";
import { useApi } from "@/lib/hooks";
import { money, pct, titleCase } from "@/lib/format";
import { ViewHeader } from "./ViewHeader";

const TABS = [
  { id: "overview", label: "Overview" },
  { id: "client", label: "Client & household" },
  { id: "portfolio", label: "Portfolio" },
  { id: "advice", label: "Advice" },
  { id: "practice", label: "Practice" },
  { id: "risk", label: "Risk & data" },
  { id: "adoption", label: "Adoption & ROI" },
  { id: "scorecards", label: "Adviser Scorecards" },
  { id: "onboarding", label: "Onboarding ops" },
  { id: "pte", label: "PTE 2020-02" },
];

const muted = "chip bg-border-soft text-ink-muted";
const warnChip = "chip bg-warn-bg text-warn";
const th = "px-4 py-2 font-medium";
const thead = "text-left text-[10px] uppercase tracking-wide text-ink-muted";
const grid3 = "grid gap-4 cq-sm:grid-cols-2 cq-md:grid-cols-3";
const levelText = (l: string) => (l === "high" ? "text-crit" : l === "medium" ? "text-warn" : "text-ok");
const levelChip = (l: string) => (l === "high" ? "bg-crit-bg text-crit" : l === "medium" ? "bg-warn-bg text-warn" : "bg-ok-bg text-ok");

/** The firm's figures - portfolios, clients, the practice and risk - one tab at a time. Read only. */
export function AnalyticsView() {
  const { data, loading, error, refetch } = useApi<any>("/api/analytics/overview");
  const { data: adoptionData, loading: adoptionLoading } = useApi<any>("/api/analytics/adoption");
  const { data: scorecardData, loading: scorecardLoading } = useApi<any>("/api/analytics/scorecards");
  const { data: obData, loading: obLoading } = useApi<any>("/api/analytics/onboarding");
  const { data: pteData, loading: pteLoading } = useApi<any>("/api/analytics/pte-tracker");
  const [tab, setTab] = useState("overview");
  if (error) return <div className="view-container"><Card><ErrorState what="analytics" message={error} onRetry={refetch} /></Card></div>;
  if (loading || !data || !data.headline || !data.client_intelligence || !data.portfolio || !data.advice || !data.practice || !data.risk_data) {
    return <div className="view-container"><SkeletonList count={4} /></div>;
  }
  const h = data.headline;
  const ci = data.client_intelligence, pf = data.portfolio, ad = data.advice, pr = data.practice, rd = data.risk_data;

  return (
    <div className="view-container">
      <ViewHeader title="Analytics" />

      <div className="mb-6 flex gap-1 overflow-x-auto border-b border-border" role="tablist" data-testid="analytics-tabs">
        {TABS.map((t) => (
          <button key={t.id} data-testid={`analytics-tab-${t.id}`} role="tab"
            aria-selected={tab === t.id} onClick={() => setTab(t.id)}
            className={`-mb-px whitespace-nowrap border-b-2 px-4 py-2.5 text-sm font-medium transition focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-inset focus-visible:ring-accent ${
              tab === t.id ? "border-ink text-ink" : "border-transparent text-ink-muted hover:text-ink"}`}>
            {t.label}
          </button>
        ))}
      </div>

      {tab === "overview" && (
        <div className="mb-4 grid grid-cols-2 gap-3 cq-md:grid-cols-4" data-testid="analytics-headline">
          <Kpi icon={Wallet} label="Firm AUM" value={money(h.firm_aum)} onClick={() => setTab("portfolio")} />
          <Kpi icon={Users} label="Clients" value={h.clients} onClick={() => setTab("client")} />
          <Kpi icon={TrendingUp} label="Total return" value={pct(h.total_return)} accent="positive" onClick={() => setTab("portfolio")} />
          <Kpi icon={Wallet} label="Wallet share" value={pct(h.firm_wallet_share, 0)}
            hint={`${money(h.consolidation_opportunity)} held away`} onClick={() => setTab("client")} />
          <Kpi icon={PiggyBank} label="Firm margin" value={pct(h.firm_margin, 0)}
            hint={`${money(h.fee_revenue)} est. fee revenue`} onClick={() => setTab("practice")} />
          <Kpi icon={Target} label="Goals on track" value={h.goals_on_track_pct === null ? "—" : pct(h.goals_on_track_pct, 0)} onClick={() => setTab("client")} />
          <Kpi icon={AlertTriangle} label="At-risk clients" value={h.attrition_high}
            accent={h.attrition_high ? "critical" : undefined} onClick={() => setTab("client")} />
          <Kpi icon={Database} label="Data quality" value={pct(h.data_quality_score, 0)} accent="positive" onClick={() => setTab("risk")} />
        </div>
      )}

      {/* 2.1 Client & household intelligence */}
      <Layer active={tab === "client"} title="Client & household intelligence" icon={Users}>
        <div className={grid3}>
          <Card>
            <CardTitle>Total-portfolio view</CardTitle>
            <AllocationDonut data={ci.total_portfolio.by_asset_class} size={150} />
            <div className="mt-2 flex flex-wrap gap-1.5">
              <span className={muted}>public {money(ci.total_portfolio.public_value)}</span>
              <span className={muted}>private {money(ci.total_portfolio.private_value)}</span>
            </div>
          </Card>
          <Card>
            <CardTitle>Householding & relationships</CardTitle>
            <Metric k="Households" v={ci.householding.households} />
            <Metric k="People / entities" v={`${ci.householding.persons} / ${ci.householding.entities}`} />
            <Metric k="Multi-entity households" v={ci.householding.multi_entity_households} />
            <Metric k="Relationship edges" v={ci.householding.relationships} />
            <Metric k="Next-gen members" v={ci.householding.next_gen_members} />
          </Card>
          <Card>
            <CardTitle>Wallet-share & held-away</CardTitle>
            <BarList items={ci.wallet_share.by_household.map((w: any) => ({ label: w.household, value: w.wallet_share, right: pct(w.wallet_share, 0) }))} />
            <Metric k="Consolidation opportunity" v={money(ci.wallet_share.consolidation_opportunity)} />
          </Card>
          <Card className="cq-sm:col-span-2 cq-md:col-span-3">
            <CardTitle>Lifetime value & segmentation</CardTitle>
            <div className="mb-3 flex flex-wrap gap-2">
              {Object.entries(ci.clv_segmentation.by_tier).map(([t, n]: any) => (
                <span key={t} className="chip bg-border-soft text-ink-soft">{t}: {n}</span>
              ))}
              <span className="chip ml-auto bg-border-soft text-ink">Total CLV {money(ci.clv_segmentation.total_lifetime_value)}</span>
            </div>
            <Table head={["Client", "Segment", "Tier", "AUM", "Annual fee", "Lifetime value"]}
              rows={ci.clv_segmentation.clients.map((c: any) => [c.household, titleCase(c.segment), c.tier, money(c.aum), money(c.annual_fee), money(c.lifetime_value)])} />
          </Card>
        </div>
      </Layer>

      {/* 2.2 Portfolio & investment */}
      <Layer active={tab === "portfolio"} title="Portfolio & investment analytics" icon={BarChart3}>
        <div className={grid3}>
          <Card>
            <CardTitle>Performance & attribution{pf.performance.period ? ` · ${pf.performance.period}` : ""}</CardTitle>
            <Metric k="Time-weighted return" v={pct(pf.performance.total_return)} accent={pf.performance.total_return == null ? undefined : pf.performance.total_return >= 0 ? "positive" : "critical"} />
            {pf.performance.mwr && (
              <Metric k="Money-weighted return" v={pct(pf.performance.mwr.period)} accent={pf.performance.mwr.period >= 0 ? "positive" : "critical"} />
            )}
            {pf.performance.benchmark_symbol && (
              <Metric k={`Benchmark (${pf.performance.benchmark_symbol})`} v={pf.performance.benchmark_return != null ? pct(pf.performance.benchmark_return) : "—"} />
            )}
            {pf.performance.alpha != null && (
              <Metric k="Alpha vs benchmark" v={pct(pf.performance.alpha)} accent={pf.performance.alpha >= 0 ? "positive" : "critical"} />
            )}
            <Metric k="Unrealised gain" v={money(pf.performance.unrealised_gain)} />
            <div className="mt-2"><BarList items={pf.performance.attribution.map((a: any) => ({ label: titleCase(a.asset_class), value: Math.max(a.contribution, 0), right: pct(a.contribution) }))} /></div>
            <div className="mt-3 flex flex-wrap gap-1.5 border-t border-border-soft pt-2" data-testid="performance-disclosures">
              <span className={muted}>Securities only</span>
              <span className={muted}>Gross of fees</span>
              {pf.performance.disclosures?.synthetic_price_symbols?.length > 0 && (
                <span className={warnChip}>Synthetic price: {pf.performance.disclosures.synthetic_price_symbols.join(", ")}</span>
              )}
              {pf.performance.disclosures?.declared_price_symbols?.length > 0 && (
                <span className={muted}>Stated valuation: {pf.performance.disclosures.declared_price_symbols.join(", ")}</span>
              )}
              {pf.performance.disclosures?.unpriced_flat_symbols?.length > 0 && (
                <span className={warnChip}>Held flat: {pf.performance.disclosures.unpriced_flat_symbols.join(", ")}</span>
              )}
              {pf.performance.disclosures?.ledger_gap_lots > 0 && (
                <span className={warnChip}>{pf.performance.disclosures.ledger_gap_lots} purchase(s) not in ledger</span>
              )}
              {pf.performance.disclosures?.stale_days > 45 && (
                <span className={warnChip}>Prices as of {pf.performance.disclosures.as_of}</span>
              )}
            </div>
          </Card>
          <Card>
            <CardTitle>Whole-portfolio risk</CardTitle>
            <Metric k="Expected return" v={pct(pf.risk.expected_return)} />
            <Metric k="Volatility" v={pct(pf.risk.volatility)} />
            <Metric k="1y 95% VaR" v={money(pf.risk.var_95_1y)} />
            <Metric k="Stress (GFC)" v={`${pct(pf.risk.stress_test?.gfc_2008?.impact_pct)} · ${money(pf.risk.stress_test?.gfc_2008?.impact_value)}`} />
          </Card>
          <Card>
            <CardTitle>Drift & tax</CardTitle>
            <Metric k="Mandates monitored" v={pf.drift.mandates_monitored} />
            <Metric k="Breaching tolerance" v={pf.drift.mandates_breached} accent={pf.drift.mandates_breached ? "critical" : undefined} />
            <Metric k="Harvestable losses" v={money(pf.tax.harvestable_losses)} />
            <Metric k="Estimated tax alpha" v={money(pf.tax.estimated_tax_alpha)} accent="positive" />
          </Card>
          <Card>
            <CardTitle>Goals-based projections</CardTitle>
            <Metric k="Goals tracked" v={pf.goals.total} />
            <Metric k="On track" v={pf.goals.on_track} />
            <Metric k="Avg probability" v={pf.goals.avg_probability === null ? "—" : pct(pf.goals.avg_probability, 0)} />
          </Card>
          <Card>
            <CardTitle>Values / ESG alignment</CardTitle>
            <Metric k="Alignment score" v={pct(pf.esg.alignment_score, 0)} accent={pf.esg.alignment_score >= 0.95 ? "positive" : "critical"} />
            <Metric k="Equity screened" v={money(pf.esg.equity_value)} />
            <Metric k="Excluded / conflicting" v={money(pf.esg.excluded_value)} accent={pf.esg.excluded_value ? "critical" : undefined} />
          </Card>
          <Card>
            <CardTitle>Alternatives</CardTitle>
            <Metric k="Alternatives value" v={money(pf.alternatives.value)} />
            <Metric k="% of book" v={pct(pf.alternatives.pct_of_book, 0)} />
            <Metric k="Illiquid" v={pct(pf.alternatives.illiquid_pct, 0)} />
          </Card>
        </div>
      </Layer>

      {/* 2.3 Advice & next-best-action */}
      <Layer active={tab === "advice"} title="Advice & next-best-action" icon={Target}>
        <div className={grid3}>
          <Card>
            <CardTitle>Opportunity & risk detection</CardTitle>
            {Object.entries(ad.opportunities?.by_kind || {}).map(([k, n]: any) => (
              <Metric key={k} k={titleCase(k)} v={n} />
            ))}
            <Metric k="Signals across the book" v={ad.opportunities.total} />
          </Card>
          <Card>
            <CardTitle>Attrition / churn risk</CardTitle>
            {ad.attrition.by_household.map((a: any, i: number) => (
              <div key={i} className="mb-2">
                <div className="flex justify-between gap-2 text-sm">
                  <span className="text-ink-soft">{a.household}</span>
                  <span className={`tabular-nums ${levelText(a.level)}`}>{titleCase(a.level)} {pct(a.risk, 0)}</span>
                </div>
                {a.factors.length > 0 && <div className="text-[11px] text-ink-muted">{a.factors.join(" · ")}</div>}
              </div>
            ))}
          </Card>
          <Card>
            <CardTitle>Goal tracking & engagement</CardTitle>
            <Metric k="Goals on track" v={`${ad.goal_tracking.on_track}/${ad.goal_tracking.total}`} />
            <Metric k="Avg goal probability" v={ad.goal_tracking.avg_probability === null ? "—" : pct(ad.goal_tracking.avg_probability, 0)} />
            <Metric k="Avg engagement" v={ad.engagement.avg_score === null ? "—" : pct(ad.engagement.avg_score, 0)} />
          </Card>
        </div>
      </Layer>

      {/* 2.4 Practice & business */}
      <Layer active={tab === "practice"} title="Practice & business analytics" icon={PiggyBank}>
        <div className={grid3}>
          <Card>
            <CardTitle>Capacity & productivity</CardTitle>
            <Metric k="Agent runs" v={pr.capacity.agent_runs} />
            <Metric k="Decisions made" v={pr.capacity.decisions} />
            <Metric k="Hours reclaimed" v={`${pr.capacity.hours_reclaimed}h`} accent="positive" />
          </Card>
          <Card>
            <CardTitle>Cost-to-serve & profitability</CardTitle>
            <Metric k="Fee revenue" v={money(pr.cost_to_serve.revenue)} />
            <Metric k="Revenue yield (ROA)"
              v={pr.capacity.firm_aum > 0
                ? `${Math.round((pr.cost_to_serve.revenue / pr.capacity.firm_aum) * 10000)} bps`
                : "—"} />
            <Metric k="Cost to serve" v={money(pr.cost_to_serve.cost)} />
            <Metric k="Profit" v={money(pr.cost_to_serve.profit)} accent="positive" />
            <Metric k="Firm margin" v={pct(pr.cost_to_serve.firm_margin, 0)} />
          </Card>
          <Card>
            <CardTitle>Growth & referral</CardTitle>
            <Metric k="Onboarding pipeline" v={pr.growth.pipeline_cases} />
            <Metric k="Net New Assets (NNA)" v={money(pr.growth.net_new_assets ?? 0)} accent={pr.growth.net_new_assets > 0 ? "positive" : undefined} />
            <Metric k="Consolidation opportunity" v={money(pr.growth.consolidation_opportunity)} />
            <Metric k="Referral-ready clients" v={pr.growth.referral_ready_clients} />
            <Metric k="NIGO rate" v={pr.growth.nigo_rate != null ? `${Math.round((pr.growth.nigo_rate ?? 0) * 100)}%` : "—"} />
          </Card>
          <Card className="cq-sm:col-span-2">
            <CardTitle>Fee & margin by segment</CardTitle>
            <Table head={["Segment", "Revenue", "Rate (bps)", "Margin"]}
              rows={Object.entries(pr.fee_margin?.by_segment || {}).map(([s, v]: any) => [
                titleCase(s),
                money(v.revenue),
                `${Math.round(v.effective_rate * 10000)} bps`,
                pct(v.margin, 0),
              ])} />
            <div className="mt-3 flex flex-wrap gap-1.5" data-testid="fee-billing-position">
              <span className={muted}>revenue is an estimate from segment rates</span>
              <span className={muted}>
                Billed this year {pr.fee_margin.billed_this_year == null ? "—" : money(pr.fee_margin.billed_this_year)}
              </span>
              {pr.fee_margin.fee_at_risk != null && (
                <span className={pr.fee_margin.fee_at_risk > 0 ? warnChip : muted}>
                  Unpaid {money(pr.fee_margin.unpaid)} · held {money(pr.fee_margin.held)}
                </span>
              )}
            </div>
          </Card>
          <Card>
            <CardTitle>M&A / book integration</CardTitle>
            <Metric k="Acquisitions" v={pr.book_integration.acquisitions} />
            <Metric k="Committed" v={pr.book_integration.committed} />
            <Metric k="Open conflicts" v={pr.book_integration.open_conflicts} />
            <Metric k="Recon. progress" v={pct(pr.book_integration.reconciliation_progress, 0)} />
          </Card>
        </div>
      </Layer>

      {/* 2.6 Adoption & ROI */}
      <Layer active={tab === "adoption"} title="Adoption & ROI" icon={Zap}>
        {adoptionLoading || !adoptionData ? (
          <SkeletonList count={2} />
        ) : (
          <div className="space-y-4">
            <div className="grid grid-cols-2 gap-3 cq-sm:grid-cols-3 cq-md:grid-cols-5" data-testid="adoption-summary">
              <Kpi icon={Users} label="Active advisers" value={adoptionData.summary.total_advisers_active} />
              <Kpi icon={Activity} label="Agent runs" value={adoptionData.summary.total_agent_runs} />
              <Kpi icon={CheckCircle2} label="Decisions made" value={adoptionData.summary.total_decisions} />
              <Kpi icon={Clock} label="Hours saved" value={`${adoptionData.summary.hours_saved}h`} accent="positive" />
              <Kpi icon={PiggyBank} label="Cost displaced" value={money(adoptionData.summary.cost_displaced_usd)} accent="positive"
                hint="Analyst time at $75/h" />
            </div>

            <div className="flex flex-wrap gap-1.5" data-testid="adoption-basis">
              <span className={warnChip}>estimate</span>
              <span className={muted}>25 min a decision</span>
              <span className={muted}>15 min an agent run</span>
              <span className={muted}>$75 an hour</span>
            </div>

            <div className="grid gap-4 cq-md:grid-cols-2">
              <Card>
                <CardTitle>Agent usage & adviser uptake</CardTitle>
                {adoptionData.by_agent.length === 0 ? (
                  <div className="py-4 text-center text-sm text-ink-muted">No agent runs recorded yet.</div>
                ) : (
                  <div className="mt-1 space-y-3">
                    {adoptionData.by_agent.map((a: any) => {
                      const uptakePct = a.uptake_pct ?? 0;
                      return (
                        <div key={a.agent_key}>
                          <div className="mb-1 flex flex-wrap items-center justify-between gap-x-3 text-sm">
                            <span className="font-medium text-ink">{titleCase(a.agent_key.replace(/_/g, " "))}</span>
                            <div className="flex items-center gap-3 text-xs text-ink-muted">
                              <span>{a.runs} run{a.runs !== 1 ? "s" : ""}</span>
                              {a.decisions > 0 && (
                                <span className="flex items-center gap-1">
                                  <CheckCircle2 size={11} className="text-ok" />
                                  {pct(a.approval_rate ?? 0, 0)} approved
                                </span>
                              )}
                              <span>{a.advisers_using} adviser{a.advisers_using !== 1 ? "s" : ""}</span>
                            </div>
                          </div>
                          <Bar value={Math.min(uptakePct * 100, 100)} />
                          <div className="mt-0.5 text-[10px] text-ink-muted">{pct(uptakePct, 0)} adviser uptake</div>
                        </div>
                      );
                    })}
                  </div>
                )}
              </Card>

              <Card>
                <CardTitle>Adviser engagement leaderboard</CardTitle>
                {adoptionData.by_adviser.length === 0 ? (
                  <div className="py-4 text-center text-sm text-ink-muted">No decisions recorded yet.</div>
                ) : (
                  <div className="mt-1 overflow-x-auto">
                    <table className="w-full text-sm">
                      <thead className="text-xs uppercase tracking-wide text-ink-muted">
                        <tr>
                          <th className="py-1.5 text-left">Adviser</th>
                          <th className="py-1.5 text-right">Decisions</th>
                          <th className="py-1.5 text-right">Approval</th>
                          <th className="py-1.5 text-right">Agents used</th>
                        </tr>
                      </thead>
                      <tbody>
                        {adoptionData.by_adviser.map((a: any, i: number) => (
                          <tr key={i} className="border-t border-border-soft">
                            <td className="py-1.5 text-left">
                              <div className="text-sm font-medium text-ink">{a.name}</div>
                              <div className="text-[11px] text-ink-muted">{a.email}</div>
                            </td>
                            <td className="py-1.5 text-right tabular-nums text-ink-soft">{a.decisions}</td>
                            <td className="py-1.5 text-right tabular-nums">
                              {a.approval_rate !== null ? (
                                <span className={a.approval_rate >= 0.7 ? "text-ok" : a.approval_rate >= 0.4 ? "text-warn" : "text-crit"}>
                                  {pct(a.approval_rate, 0)}
                                </span>
                              ) : <span className="text-ink-muted">—</span>}
                            </td>
                            <td className="py-1.5 text-right tabular-nums text-ink-soft">{a.agents_used}</td>
                          </tr>
                        ))}
                      </tbody>
                    </table>
                  </div>
                )}
              </Card>
            </div>

            {adoptionData.by_agent.some((a: any) => a.decisions > 0) && (
              <Card>
                <CardTitle>Decision outcomes by agent</CardTitle>
                <div className="overflow-x-auto">
                  <table className="w-full text-sm">
                    <thead className="text-xs uppercase tracking-wide text-ink-muted">
                      <tr>
                        <th className="py-1.5 text-left">Agent</th>
                        <th className="py-1.5 text-right">Total</th>
                        <th className="py-1.5 text-right">Approved</th>
                        <th className="py-1.5 text-right">Modified</th>
                        <th className="py-1.5 text-right">Dismissed</th>
                        <th className="py-1.5 text-right">Approval rate</th>
                      </tr>
                    </thead>
                    <tbody>
                      {adoptionData.by_agent.filter((a: any) => a.decisions > 0).map((a: any, i: number) => (
                        <tr key={i} className="border-t border-border-soft">
                          <td className="py-1.5 text-left font-medium text-ink">{titleCase(a.agent_key.replace(/_/g, " "))}</td>
                          <td className="py-1.5 text-right tabular-nums text-ink-soft">{a.decisions}</td>
                          <td className="py-1.5 text-right tabular-nums text-ok">{a.approved}</td>
                          <td className="py-1.5 text-right tabular-nums text-warn">{a.modified}</td>
                          <td className="py-1.5 text-right tabular-nums text-crit">{a.dismissed}</td>
                          <td className="py-1.5 text-right tabular-nums">
                            {a.approval_rate !== null ? (
                              <span className={a.approval_rate >= 0.7 ? "font-medium text-ok" : "text-ink-soft"}>
                                {pct(a.approval_rate, 0)}
                              </span>
                            ) : "—"}
                          </td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </div>
              </Card>
            )}
          </div>
        )}
      </Layer>

      {/* Adviser Scorecards */}
      <Layer active={tab === "scorecards"} title="Adviser Scorecards" icon={Users} meta={<span className={muted}>last 90 days</span>}>
        {scorecardLoading || !scorecardData ? (
          <SkeletonList count={2} />
        ) : !scorecardData.scorecards?.length ? (
          <Card><div className="py-6 text-center text-sm text-ink-muted">No advisers found.</div></Card>
        ) : (
          <Card className="overflow-hidden p-0">
            <div className="overflow-x-auto">
              <table className="w-full text-sm" data-testid="analytics-scorecards">
                <thead>
                  <tr className={`border-b border-border-soft ${thead}`}>
                    <th className={th}>Adviser</th>
                    <th className={`${th} text-right`}>AUM</th>
                    <th className={`${th} text-right`}>Households</th>
                    <th className={`${th} text-right`}>Recs approved (90d)</th>
                    <th className={`${th} text-right`}>Meetings (90d)</th>
                  </tr>
                </thead>
                <tbody className="divide-y divide-border-soft">
                  {scorecardData.scorecards.map((s: any) => (
                    <tr key={s.adviser_id} className="hover:bg-border-soft/40">
                      <td className="px-4 py-3">
                        <div className="font-medium text-ink">{s.adviser_name}</div>
                        <div className="text-[11px] text-ink-muted">{s.adviser_email}</div>
                      </td>
                      <td className="px-4 py-3 text-right font-mono tabular-nums text-ink">{money(s.aum)}</td>
                      <td className="px-4 py-3 text-right tabular-nums text-ink-soft">{s.household_count}</td>
                      <td className="px-4 py-3 text-right tabular-nums">
                        <span className={s.recs_approved_90d > 0 ? "font-medium text-ok" : "text-ink-muted"}>{s.recs_approved_90d}</span>
                      </td>
                      <td className="px-4 py-3 text-right tabular-nums text-ink-soft">{s.meetings_90d}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          </Card>
        )}
      </Layer>

      {/* F5 Onboarding operating metrics */}
      <Layer active={tab === "onboarding"} title="Onboarding operating metrics" icon={ClipboardList}>
        {obLoading || !obData ? (
          <SkeletonList count={2} />
        ) : (
          <div className="space-y-4">
            <div className="grid grid-cols-2 gap-3 cq-md:grid-cols-4" data-testid="onboarding-summary">
              <Kpi icon={ClipboardList} label="Total cases" value={obData.total_cases} />
              <Kpi icon={CheckCircle2} label="First-pass yield" value={`${Math.round(obData.first_pass_yield * 100)}%`} accent="positive" />
              <Kpi icon={AlertTriangle} label="NIGO cases" value={obData.nigo.total} accent={obData.nigo.total > 0 ? "critical" : undefined} />
              <Kpi icon={Clock} label="EDD backlog" value={obData.edd_backlog.pending_count} accent={obData.edd_backlog.pending_count > 0 ? "critical" : undefined} />
            </div>

            <div className={grid3}>
              <Card>
                <CardTitle>Pipeline funnel</CardTitle>
                {["intake", "screening", "review", "approved", "rejected"].map((s) => (
                  obData.funnel[s] != null ? (
                    <div key={s} className="flex items-center gap-2 py-0.5">
                      <div className="w-20 text-xs capitalize text-ink-muted">{s}</div>
                      <div className="flex-1"><Bar value={Math.min(((obData.funnel[s] || 0) / Math.max(obData.total_cases, 1)) * 100, 100)} thick /></div>
                      <div className="w-6 text-right text-xs tabular-nums text-ink-soft">{obData.funnel[s]}</div>
                    </div>
                  ) : null
                ))}
              </Card>

              <Card>
                <CardTitle>Cycle time (approved cases)</CardTitle>
                <div className="mb-3 flex gap-4">
                  <div className="text-center">
                    <div className="text-xl font-semibold tabular-nums text-ink">{obData.cycle_time.overall_p50_days}d</div>
                    <div className="text-[11px] text-ink-muted">P50</div>
                  </div>
                  <div className="text-center">
                    <div className="text-xl font-semibold tabular-nums text-ink">{obData.cycle_time.overall_p90_days}d</div>
                    <div className="text-[11px] text-ink-muted">P90</div>
                  </div>
                </div>
                {obData.cycle_time.by_registration_type.length > 0 ? (
                  <Table
                    head={["Type", "Count", "P50", "P90"]}
                    rows={obData.cycle_time.by_registration_type.map((r: any) => [
                      r.registration_type.replace(/_/g, " "), r.count, `${r.p50_days}d`, `${r.p90_days}d`,
                    ])}
                  />
                ) : (
                  <div className="py-2 text-center text-sm text-ink-muted">No approved cases yet.</div>
                )}
              </Card>

              <Card>
                <CardTitle>NIGO root-cause breakdown</CardTitle>
                {Object.keys(obData.nigo.by_root_cause).length === 0 ? (
                  <div className="py-2 text-center text-sm text-ink-muted">No NIGO flags recorded.</div>
                ) : (
                  <BarList items={Object.entries(obData.nigo.by_root_cause).map(([k, v]: any) => ({
                    label: k.replace(/_/g, " "),
                    value: v,
                    right: String(v),
                  }))} />
                )}
                <div className="mt-3 flex flex-wrap gap-1.5 border-t border-border-soft pt-2">
                  <span className={muted}>NIGO rate {Math.round(obData.nigo.rate * 100)}%</span>
                  <span className={muted}>first-pass yield {Math.round(obData.first_pass_yield * 100)}%</span>
                </div>
              </Card>

              <Card>
                <CardTitle>Abandonment / stall</CardTitle>
                <div className="mb-2 flex gap-4">
                  <div>
                    <div className="text-lg font-semibold tabular-nums text-warn">{obData.abandonment.stalled_amber_3d}</div>
                    <div className="text-[11px] text-ink-muted">Stalled 3–7d</div>
                  </div>
                  <div>
                    <div className="text-lg font-semibold tabular-nums text-crit">{obData.abandonment.stalled_red_7d}</div>
                    <div className="text-[11px] text-ink-muted">Stalled 7d+</div>
                  </div>
                </div>
                {Object.keys(obData.abandonment.by_status).length > 0 && (
                  <BarList items={Object.entries(obData.abandonment.by_status).map(([k, v]: any) => ({
                    label: k.replace(/_/g, " "),
                    value: v,
                    right: String(v),
                  }))} />
                )}
              </Card>

              <Card>
                <CardTitle>AML risk tier distribution</CardTitle>
                {["low", "medium", "high"].map((t) => (
                  obData.aml_by_tier[t] != null ? (
                    <div key={t} className="flex items-center gap-2 py-0.5">
                      <span className={`w-14 text-xs font-medium capitalize ${levelText(t)}`}>{t}</span>
                      <div className="h-2 flex-1 overflow-hidden rounded-full bg-border-soft">
                        <div className={`h-full rounded-full ${t === "high" ? "bg-crit" : t === "medium" ? "bg-warn" : "bg-ok"}`}
                          style={{ width: `${(obData.aml_by_tier[t] / Math.max(obData.total_cases, 1)) * 100}%` }} />
                      </div>
                      <div className="w-6 text-right text-xs tabular-nums text-ink-soft">{obData.aml_by_tier[t]}</div>
                    </div>
                  ) : null
                ))}
                <div className="mt-3 flex flex-wrap gap-1.5 border-t border-border-soft pt-2">
                  <span className={`chip ${obData.edd_backlog.pending_count > 0 ? "bg-crit-bg text-crit" : "bg-ok-bg text-ok"}`}>
                    {obData.edd_backlog.pending_count} case{obData.edd_backlog.pending_count !== 1 ? "s" : ""} pending EDD
                  </span>
                </div>
              </Card>

              <Card>
                <CardTitle>Avg readiness score</CardTitle>
                <div className="text-3xl font-semibold tabular-nums text-ink">
                  {obData.avg_readiness_score != null ? obData.avg_readiness_score : "—"}
                </div>
                <div className="mt-2 flex flex-wrap gap-1.5">
                  <span className={muted}>out of 100</span>
                  <span className={muted}>lower = more gaps</span>
                  <span className={muted}>scored by the NIGO Prevention agent</span>
                </div>
              </Card>
            </div>
          </div>
        )}
      </Layer>

      {/* U7 PTE 2020-02 tracker */}
      <Layer active={tab === "pte"} title="DOL PTE 2020-02 status tracker" icon={ClipboardList} meta={<span className={muted}>rollover best-interest memos</span>}>
        {pteLoading || !pteData ? (
          <SkeletonList count={2} />
        ) : (
          <div className="space-y-4">
            <div className="grid grid-cols-2 gap-3 cq-md:grid-cols-4" data-testid="pte-summary">
              <Kpi icon={ClipboardList} label="Rollover cases" value={pteData.total_rollover_cases} />
              <Kpi icon={CheckCircle2} label="Memo generated" value={pteData.memo_generated} accent="positive" />
              <Kpi icon={AlertTriangle} label="Missing memo" value={pteData.missing_memo} accent={pteData.missing_memo > 0 ? "critical" : undefined} />
              <Kpi icon={Activity} label="Pending review" value={pteData.by_pte_status?.["pending"] || 0} />
            </div>
            <Card className="overflow-hidden p-0">
              <div className="overflow-x-auto">
                <table className="w-full text-sm" data-testid="pte-cases">
                  <thead>
                    <tr className={`border-b border-border-soft ${thead}`}>
                      <th className={th}>Prospect</th>
                      <th className={th}>Type</th>
                      <th className={th}>Case status</th>
                      <th className={th}>PTE status</th>
                      <th className={th}>Memo</th>
                      <th className={th}>CIP</th>
                      <th className={th}>AML tier</th>
                    </tr>
                  </thead>
                  <tbody className="divide-y divide-border-soft">
                    {pteData.cases?.length === 0 ? (
                      <tr><td colSpan={7} className="px-4 py-6 text-center text-sm text-ink-muted">No rollover cases yet.</td></tr>
                    ) : (
                      pteData.cases?.map((r: any) => (
                        <tr key={r.case_id} className="hover:bg-border-soft/40">
                          <td className="px-4 py-3 font-medium text-ink">{r.prospect_name}</td>
                          <td className="px-4 py-3 text-ink-soft">{titleCase((r.registration_type || "").replace(/_/g, " "))}</td>
                          <td className="px-4 py-3"><span className={muted}>{r.status}</span></td>
                          <td className="px-4 py-3">
                            <span className={`chip ${r.pte_status === "generated" ? "bg-ok-bg text-ok" : r.pte_status === "reviewed" ? "bg-border-soft text-ok" : "bg-warn-bg text-warn"}`}>
                              {r.pte_status || "pending"}
                            </span>
                          </td>
                          <td className="px-4 py-3">
                            {r.has_memo
                              ? <span className="flex items-center gap-1 text-xs text-ok"><CheckCircle2 size={12} /> Yes</span>
                              : <span className="flex items-center gap-1 text-xs text-warn"><AlertTriangle size={12} /> No</span>}
                          </td>
                          <td className="px-4 py-3">
                            {r.cip_status
                              ? <span className={`chip text-[10px] ${r.cip_status === "verified" ? "bg-ok-bg text-ok" : r.cip_status === "review" ? "bg-warn-bg text-warn" : "bg-crit-bg text-crit"}`}>{r.cip_status}</span>
                              : <span className="text-xs text-ink-faint">—</span>}
                          </td>
                          <td className="px-4 py-3">
                            {r.aml_risk_tier
                              ? <span className={`chip text-[10px] ${levelChip(r.aml_risk_tier)}`}>{r.aml_risk_tier}</span>
                              : <span className="text-xs text-ink-faint">—</span>}
                          </td>
                        </tr>
                      ))
                    )}
                  </tbody>
                </table>
              </div>
            </Card>
          </div>
        )}
      </Layer>

      {/* 2.5 Risk, conduct & data */}
      <Layer active={tab === "risk"} title="Risk, conduct & data analytics" icon={ShieldCheck}>
        <div className={grid3}>
          <Card>
            <CardTitle>Conduct surveillance</CardTitle>
            <Metric k="Open flags" v={rd.conduct.open_flags} />
            {Object.entries(rd.conduct.by_category).map(([c, n]: any) => <Metric key={c} k={titleCase(c)} v={n} />)}
          </Card>
          <Card>
            <CardTitle>AML / CFT</CardTitle>
            {Object.entries(rd.aml.by_status).map(([s, n]: any) => <Metric key={s} k={titleCase(s)} v={n} />)}
            <Metric k="Watchlist hits" v={rd.aml.total_hits} />
          </Card>
          <Card>
            <CardTitle>Data quality</CardTitle>
            <Metric k="Score" v={pct(rd.data_quality.score, 0)} accent="positive" />
            <Metric k="Completeness" v={pct(rd.data_quality.completeness, 0)} />
            <Metric k="Timeliness" v={pct(rd.data_quality.timeliness, 0)} />
            <Metric k="Avg confidence" v={pct(rd.data_quality.avg_confidence, 0)} />
          </Card>
          <Card>
            <CardTitle>Agent performance</CardTitle>
            <Metric k="Agents evaluated" v={rd.agent_performance.agents_evaluated} />
            <Metric k="Healthy" v={rd.agent_performance.healthy} />
            <Metric k="Avg quality" v={rd.agent_performance.avg_quality === null ? "Run evaluation" : pct(rd.agent_performance.avg_quality, 0)} />
          </Card>
          <Card className="cq-sm:col-span-2">
            <CardTitle>Audit & explainability</CardTitle>
            <div className="flex items-center gap-3">
              <ShieldCheck className={`shrink-0 ${rd.audit.chain_valid ? "text-ok" : "text-crit"}`} />
              <div className="flex-1">
                <Metric k="Ledger entries" v={rd.audit.ledger_entries} />
                <Metric k="Chain" v={rd.audit.chain_valid ? "valid" : "broken"} accent={rd.audit.chain_valid ? "positive" : "critical"} />
              </div>
            </div>
          </Card>
        </div>
      </Layer>
    </div>
  );
}

function Kpi({ icon: Icon, label, value, hint, accent, onClick }: {
  icon: typeof Users; label: string; value: ReactNode; hint?: string; accent?: "positive" | "critical"; onClick?: () => void;
}) {
  const c = accent === "positive" ? "text-ok" : accent === "critical" ? "text-crit" : "text-ink";
  const body = (
    <>
      <div className="tile-label flex items-center gap-1.5"><Icon size={13} /> {label}</div>
      <div className={`mt-1 text-xl font-semibold tabular-nums ${c}`}>{value}</div>
      {hint && <div className="mt-0.5 truncate text-[11px] text-ink-muted" title={hint}>{hint}</div>}
    </>
  );
  if (!onClick) return <div className="card min-w-0 p-4">{body}</div>;
  return (
    <button type="button" onClick={onClick}
      className="card min-w-0 p-4 text-left transition hover:border-border hover:shadow-lift focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-accent">
      {body}
    </button>
  );
}

function Layer({ title, icon: Icon, meta, children, active = true }: {
  title: string; icon: typeof Users; meta?: ReactNode; children: ReactNode; active?: boolean;
}) {
  if (!active) return null;
  return (
    <div className="mb-8">
      <div className="mb-3 flex flex-wrap items-center gap-2">
        <span className="flex h-8 w-8 items-center justify-center rounded-lg bg-border-soft text-ink-soft"><Icon size={16} /></span>
        <div className="font-semibold text-ink">{title}</div>
        {meta}
      </div>
      {children}
    </div>
  );
}

const CardTitle = ({ children }: { children: ReactNode }) => <div className="tile-label mb-2">{children}</div>;

function Metric({ k, v, accent }: { k: string; v: ReactNode; accent?: "positive" | "critical" }) {
  return (
    <div className="flex justify-between gap-3 py-0.5 text-sm">
      <span className="text-ink-muted">{k}</span>
      <span className={`text-right font-medium tabular-nums ${accent === "positive" ? "text-ok" : accent === "critical" ? "text-crit" : "text-ink-soft"}`}>{v}</span>
    </div>
  );
}

function Bar({ value, thick = false }: { value: number; thick?: boolean }) {
  return (
    <div className={`${thick ? "h-2" : "h-1.5"} overflow-hidden rounded-full bg-border-soft`}>
      <div className="h-full rounded-full bg-ink-muted" style={{ width: `${value}%` }} />
    </div>
  );
}

function BarList({ items }: { items: { label: string; value: number; right: string }[] }) {
  const max = Math.max(...items.map((i) => i.value), 0.0001);
  return (
    <div className="space-y-1.5">
      {items.map((it, i) => (
        <div key={i}>
          <div className="flex justify-between gap-2 text-xs"><span className="text-ink-soft">{it.label}</span><span className="tabular-nums text-ink-muted">{it.right}</span></div>
          <Bar value={(it.value / max) * 100} />
        </div>
      ))}
    </div>
  );
}

function Table({ head, rows }: { head: string[]; rows: ReactNode[][] }) {
  return (
    <div className="overflow-x-auto">
      <table className="w-full text-sm">
        <thead className="text-xs uppercase tracking-wide text-ink-muted">
          <tr>{head.map((h, i) => <th key={i} className={`py-1.5 ${i === 0 ? "text-left" : "text-right"}`}>{h}</th>)}</tr>
        </thead>
        <tbody>
          {rows.map((r, i) => (
            <tr key={i} className="border-t border-border-soft">
              {r.map((c, j) => <td key={j} className={`py-1.5 ${j === 0 ? "text-left font-medium text-ink" : "text-right tabular-nums text-ink-soft"}`}>{c}</td>)}
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}
