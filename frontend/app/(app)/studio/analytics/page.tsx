"use client";
import { ReactNode, useState } from "react";
import {
  TrendingUp, Users, Wallet, ShieldCheck, Activity, Layers, AlertTriangle, Target,
  PiggyBank, GitMerge, Database, BarChart3, Zap, Clock, CheckCircle2, XCircle,
} from "lucide-react";
import PageHeader from "@/components/PageHeader";
import { Card, Spinner, SeverityBadge } from "@/components/ui";
import { AllocationDonut } from "@/components/Charts";
import { useApi } from "@/lib/hooks";
import { money, pct, titleCase } from "@/lib/format";

export default function Analytics() {
  const { data, loading } = useApi<any>("/api/analytics/overview");
  const { data: adoptionData, loading: adoptionLoading } = useApi<any>("/api/analytics/adoption");
  const { data: scorecardData, loading: scorecardLoading } = useApi<any>("/api/analytics/scorecards");
  const [tab, setTab] = useState("overview");
  if (loading || !data) return <Spinner label="Loading analytics…" />;
  const h = data.headline;
  const ci = data.client_intelligence, pf = data.portfolio, ad = data.advice, pr = data.practice, rd = data.risk_data;
  const TABS = [
    { id: "overview", label: "Overview" },
    { id: "client", label: "Client & household" },
    { id: "portfolio", label: "Portfolio" },
    { id: "advice", label: "Advice" },
    { id: "practice", label: "Practice" },
    { id: "risk", label: "Risk & data" },
    { id: "adoption", label: "Adoption & ROI" },
    { id: "scorecards", label: "Adviser Scorecards" },
  ];

  return (
    <div>
      <PageHeader
        title="Analytics"
        sub="Metrics across portfolios, clients, the practice and risk — computed live from the client brain."
      />

      <div className="flex gap-1 mb-6 border-b border-navy-100 overflow-x-auto">
        {TABS.map((t) => (
          <button key={t.id} onClick={() => setTab(t.id)}
            className={`px-4 py-2.5 text-sm font-medium border-b-2 -mb-px whitespace-nowrap transition ${
              tab === t.id ? "border-navy-800 text-navy-800" : "border-transparent text-ink-muted hover:text-ink"}`}>
            {t.label}
          </button>
        ))}
      </div>

      {tab === "overview" && (<>
      {/* Maturity climb */}
      <div className="flex flex-wrap gap-2 mb-6 overflow-x-auto pb-1">
        {data.maturity.map((m: any, i: number) => (
          <div key={m.stage} className="flex items-center gap-2 shrink-0">
            <div className={`rounded-xl border px-3 py-2 ${m.live ? "border-navy-200 bg-surface" : "border-navy-100 opacity-60"}`}>
              <div className="text-sm font-medium text-ink flex items-center gap-1.5">
                {m.stage} {m.live && <span className="h-1.5 w-1.5 rounded-full bg-positive animate-pulse" />}
              </div>
              <div className="text-[11px] text-ink-muted">{m.question}</div>
            </div>
            {i < data.maturity.length - 1 && <span className="text-navy-300 shrink-0">→</span>}
          </div>
        ))}
      </div>

      {/* Headline KPIs — click to jump to the relevant tab */}
      <div className="text-xs text-ink-muted mb-2 flex items-center gap-1">
        Click any metric to open the detail tab ↓
      </div>
      <div className="grid grid-cols-2 md:grid-cols-4 gap-3 mb-4">
        <Kpi icon={Wallet}        label="Firm AUM"        value={money(h.firm_aum)}               onClick={() => setTab("portfolio")} />
        <Kpi icon={Users}         label="Clients"         value={h.clients}                        onClick={() => setTab("client")} />
        <Kpi icon={TrendingUp}    label="Total return"    value={pct(h.total_return)}              accent="positive" onClick={() => setTab("portfolio")} />
        <Kpi icon={Wallet}        label="Wallet share"    value={pct(h.firm_wallet_share, 0)}
          hint={`${money(h.consolidation_opportunity)} held away — consolidation opportunity`}     onClick={() => setTab("client")} />
        <Kpi icon={PiggyBank}     label="Firm margin"     value={pct(h.firm_margin, 0)}
          hint={`${money(h.fee_revenue)} fee revenue`}                                              onClick={() => setTab("practice")} />
        <Kpi icon={Target}        label="Goals on track"  value={h.goals_on_track_pct === null ? "—" : pct(h.goals_on_track_pct, 0)} onClick={() => setTab("client")} />
        <Kpi icon={AlertTriangle} label="At-risk clients" value={h.attrition_high}
          accent={h.attrition_high ? "critical" : undefined}                                       onClick={() => setTab("client")} />
        <Kpi icon={Database}      label="Data quality"    value={pct(h.data_quality_score, 0)}    accent="positive" onClick={() => setTab("risk")} />
      </div>
      </>)}

      {/* 2.1 Client & household intelligence */}
      <Layer active={tab === "client"} n="2.1" title="Client & household intelligence" q="Who do we serve, and how well?" icon={Users}>
        <div className="grid lg:grid-cols-3 gap-4">
          <Card>
            <CardTitle>Total-portfolio view</CardTitle>
            <AllocationDonut data={ci.total_portfolio.by_asset_class} size={150} />
            <div className="text-xs text-ink-muted mt-2">
              {money(ci.total_portfolio.public_value)} public · {money(ci.total_portfolio.private_value)} private
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
            <div className="text-xs text-ink-muted mt-2">Consolidation opportunity {money(ci.wallet_share.consolidation_opportunity)}</div>
          </Card>
          <Card className="lg:col-span-3">
            <CardTitle>Lifetime value & segmentation</CardTitle>
            <div className="flex gap-2 mb-3 flex-wrap">
              {Object.entries(ci.clv_segmentation.by_tier).map(([t, n]: any) => (
                <span key={t} className="chip bg-navy-50 text-ink-soft">{t}: {n}</span>
              ))}
              <span className="chip bg-gold-soft/40 text-gold-dark ml-auto">Total CLV {money(ci.clv_segmentation.total_lifetime_value)}</span>
            </div>
            <Table head={["Client", "Segment", "Tier", "AUM", "Annual fee", "Lifetime value"]}
              rows={ci.clv_segmentation.clients.map((c: any) => [c.household, titleCase(c.segment), c.tier, money(c.aum), money(c.annual_fee), money(c.lifetime_value)])} />
          </Card>
        </div>
      </Layer>

      {/* 2.2 Portfolio & investment */}
      <Layer active={tab === "portfolio"} n="2.2" title="Portfolio & investment analytics" q="Is this the right advice?" icon={BarChart3}>
        <div className="grid lg:grid-cols-3 gap-4">
          <Card>
            <CardTitle>Performance & attribution{pf.performance.period ? ` · ${pf.performance.period}` : ""}</CardTitle>
            <Metric k="Total return" v={pct(pf.performance.total_return)} accent={pf.performance.total_return >= 0 ? "positive" : "critical"} />
            <Metric k="Unrealised gain" v={money(pf.performance.unrealised_gain)} />
            <div className="mt-2"><BarList items={pf.performance.attribution.map((a: any) => ({ label: titleCase(a.asset_class), value: Math.max(a.contribution, 0), right: pct(a.contribution) }))} /></div>
          </Card>
          <Card>
            <CardTitle>Whole-portfolio risk</CardTitle>
            <Metric k="Expected return" v={pct(pf.risk.expected_return)} />
            <Metric k="Volatility" v={pct(pf.risk.volatility)} />
            <Metric k="1y 95% VaR" v={money(pf.risk.var_95_1y)} />
            <div className="mt-2 text-xs text-ink-muted">Stress (GFC): {pct(pf.risk.stress_test?.gfc_2008?.impact_pct)} · {money(pf.risk.stress_test?.gfc_2008?.impact_value)}</div>
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
      <Layer active={tab === "advice"} n="2.3" title="Advice & next-best-action" q="What should we do now?" icon={Target}>
        <div className="grid lg:grid-cols-3 gap-4">
          <Card>
            <CardTitle>Opportunity & risk detection</CardTitle>
            {Object.entries(ad.opportunities.by_kind).map(([k, n]: any) => (
              <Metric key={k} k={titleCase(k)} v={n} />
            ))}
            <div className="text-xs text-ink-muted mt-1">{ad.opportunities.total} signals across the book</div>
          </Card>
          <Card>
            <CardTitle>Attrition / churn risk</CardTitle>
            {ad.attrition.by_household.map((a: any, i: number) => (
              <div key={i} className="mb-2">
                <div className="flex justify-between text-sm">
                  <span className="text-ink-soft">{a.household}</span>
                  <span className={a.level === "high" ? "text-critical" : a.level === "medium" ? "text-caution" : "text-positive"}>{titleCase(a.level)} {pct(a.risk, 0)}</span>
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
      <Layer active={tab === "practice"} n="2.4" title="Practice & business analytics" q="How is the firm doing?" icon={PiggyBank}>
        <div className="grid lg:grid-cols-3 gap-4">
          <Card>
            <CardTitle>Capacity & productivity</CardTitle>
            <Metric k="Agent runs" v={pr.capacity.agent_runs} />
            <Metric k="Decisions made" v={pr.capacity.decisions} />
            <Metric k="Hours reclaimed" v={`${pr.capacity.hours_reclaimed}h`} accent="positive" />
          </Card>
          <Card>
            <CardTitle>Cost-to-serve & profitability</CardTitle>
            <Metric k="Fee revenue" v={money(pr.cost_to_serve.revenue)} />
            <Metric k="Cost to serve" v={money(pr.cost_to_serve.cost)} />
            <Metric k="Profit" v={money(pr.cost_to_serve.profit)} accent="positive" />
            <Metric k="Firm margin" v={pct(pr.cost_to_serve.firm_margin, 0)} />
          </Card>
          <Card>
            <CardTitle>Growth & referral</CardTitle>
            <Metric k="Onboarding pipeline" v={pr.growth.pipeline_cases} />
            <Metric k="Consolidation opportunity" v={money(pr.growth.consolidation_opportunity)} />
            <Metric k="Referral-ready clients" v={pr.growth.referral_ready_clients} />
          </Card>
          <Card className="lg:col-span-2">
            <CardTitle>Fee & margin by segment</CardTitle>
            <Table head={["Segment", "Revenue", "Effective rate", "Margin"]}
              rows={Object.entries(pr.fee_margin.by_segment).map(([s, v]: any) => [titleCase(s), money(v.revenue), pct(v.effective_rate), pct(v.margin, 0)])} />
            <div className="text-xs text-ink-muted mt-2">Estimated fee leakage {money(pr.fee_margin.fee_leakage_estimate)}</div>
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
      <Layer active={tab === "adoption"} n="2.6" title="Adoption & ROI" q="Who uses what, and what is the return on AI advice?" icon={Zap}>
        {adoptionLoading || !adoptionData ? (
          <Spinner label="Loading adoption data…" />
        ) : (
          <div className="space-y-4">
            {/* Summary KPIs */}
            <div className="grid grid-cols-2 md:grid-cols-5 gap-3">
              <Kpi icon={Users}      label="Active advisers"   value={adoptionData.summary.total_advisers_active} />
              <Kpi icon={Activity}   label="Agent runs"        value={adoptionData.summary.total_agent_runs} />
              <Kpi icon={CheckCircle2} label="Decisions made"  value={adoptionData.summary.total_decisions} />
              <Kpi icon={Clock}      label="Hours saved"       value={`${adoptionData.summary.hours_saved}h`} accent="positive" />
              <Kpi icon={PiggyBank}  label="Cost displaced"    value={money(adoptionData.summary.cost_displaced_usd)} accent="positive"
                hint="Analyst time at $75/h" />
            </div>

            <div className="grid lg:grid-cols-2 gap-4">
              {/* Per-agent uptake */}
              <Card>
                <CardTitle>Agent usage & adviser uptake</CardTitle>
                {adoptionData.by_agent.length === 0 ? (
                  <div className="text-sm text-ink-muted py-4 text-center">No agent runs recorded yet.</div>
                ) : (
                  <div className="space-y-3 mt-1">
                    {adoptionData.by_agent.map((a: any) => {
                      const uptakePct = a.uptake_pct ?? 0;
                      return (
                        <div key={a.agent_key}>
                          <div className="flex items-center justify-between text-sm mb-1">
                            <span className="text-ink font-medium">{titleCase(a.agent_key.replace(/_/g, " "))}</span>
                            <div className="flex items-center gap-3 text-xs text-ink-muted">
                              <span>{a.runs} run{a.runs !== 1 ? "s" : ""}</span>
                              {a.decisions > 0 && (
                                <span className="flex items-center gap-1">
                                  <CheckCircle2 size={11} className="text-positive" />
                                  {pct(a.approval_rate ?? 0, 0)} approved
                                </span>
                              )}
                              <span>{a.advisers_using} adviser{a.advisers_using !== 1 ? "s" : ""}</span>
                            </div>
                          </div>
                          <div className="h-1.5 rounded-full bg-navy-100 overflow-hidden">
                            <div className="h-full bg-navy-600 rounded-full" style={{ width: `${Math.min(uptakePct * 100, 100)}%` }} />
                          </div>
                          <div className="text-[10px] text-ink-muted mt-0.5">{pct(uptakePct, 0)} adviser uptake</div>
                        </div>
                      );
                    })}
                  </div>
                )}
              </Card>

              {/* Per-adviser engagement */}
              <Card>
                <CardTitle>Adviser engagement leaderboard</CardTitle>
                {adoptionData.by_adviser.length === 0 ? (
                  <div className="text-sm text-ink-muted py-4 text-center">No decisions recorded yet.</div>
                ) : (
                  <div className="overflow-x-auto mt-1">
                    <table className="w-full text-sm">
                      <thead className="text-ink-muted text-xs uppercase tracking-wide">
                        <tr>
                          <th className="py-1.5 text-left">Adviser</th>
                          <th className="py-1.5 text-right">Decisions</th>
                          <th className="py-1.5 text-right">Approval</th>
                          <th className="py-1.5 text-right">Agents used</th>
                        </tr>
                      </thead>
                      <tbody>
                        {adoptionData.by_adviser.map((a: any, i: number) => (
                          <tr key={i} className="border-t border-navy-50">
                            <td className="py-1.5 text-left">
                              <div className="text-ink font-medium text-sm">{a.name}</div>
                              <div className="text-[11px] text-ink-muted">{a.email}</div>
                            </td>
                            <td className="py-1.5 text-right text-ink-soft tabular-nums">{a.decisions}</td>
                            <td className="py-1.5 text-right tabular-nums">
                              {a.approval_rate !== null ? (
                                <span className={a.approval_rate >= 0.7 ? "text-positive" : a.approval_rate >= 0.4 ? "text-caution" : "text-critical"}>
                                  {pct(a.approval_rate, 0)}
                                </span>
                              ) : <span className="text-ink-muted">—</span>}
                            </td>
                            <td className="py-1.5 text-right text-ink-soft tabular-nums">{a.agents_used}</td>
                          </tr>
                        ))}
                      </tbody>
                    </table>
                  </div>
                )}
              </Card>
            </div>

            {/* Decision breakdown by outcome */}
            {adoptionData.by_agent.some((a: any) => a.decisions > 0) && (
              <Card>
                <CardTitle>Decision outcomes by agent</CardTitle>
                <div className="overflow-x-auto">
                  <table className="w-full text-sm">
                    <thead className="text-ink-muted text-xs uppercase tracking-wide">
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
                        <tr key={i} className="border-t border-navy-50">
                          <td className="py-1.5 text-left text-ink font-medium">{titleCase(a.agent_key.replace(/_/g, " "))}</td>
                          <td className="py-1.5 text-right text-ink-soft tabular-nums">{a.decisions}</td>
                          <td className="py-1.5 text-right text-positive tabular-nums">{a.approved}</td>
                          <td className="py-1.5 text-right text-caution tabular-nums">{a.modified}</td>
                          <td className="py-1.5 text-right text-critical tabular-nums">{a.dismissed}</td>
                          <td className="py-1.5 text-right tabular-nums">
                            {a.approval_rate !== null ? (
                              <span className={a.approval_rate >= 0.7 ? "text-positive font-medium" : "text-ink-soft"}>
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

            <div className="text-xs text-ink-muted bg-navy-50 rounded-lg px-4 py-3">
              <strong>ROI methodology:</strong> Hours saved = (decisions × 25 min) + (agent runs × 15 min) of analyst / compliance time displaced.
              Cost displaced = hours × $75/h analyst rate. These are indicative estimates — configure assumptions to match your firm's cost structure.
            </div>
          </div>
        )}
      </Layer>

      {/* Adviser Scorecards */}
      <Layer active={tab === "scorecards"} n="H2" title="Adviser Scorecards" q="Per-adviser AUM, household count, and recommendation throughput (last 90 days)." icon={Users}>
        {scorecardLoading || !scorecardData ? (
          <Spinner label="Loading scorecards…" />
        ) : !scorecardData.scorecards?.length ? (
          <Card><div className="text-sm text-ink-muted text-center py-6">No advisers found.</div></Card>
        ) : (
          <Card className="overflow-x-auto p-0">
            <table className="w-full text-sm">
              <thead>
                <tr className="border-b border-navy-100 bg-surface text-left text-xs text-ink-muted uppercase tracking-wide">
                  <th className="px-4 py-3">Adviser</th>
                  <th className="px-4 py-3 text-right">AUM</th>
                  <th className="px-4 py-3 text-right">Households</th>
                  <th className="px-4 py-3 text-right">Recs approved (90d)</th>
                  <th className="px-4 py-3 text-right">Meetings (90d)</th>
                </tr>
              </thead>
              <tbody>
                {scorecardData.scorecards.map((s: any) => (
                  <tr key={s.adviser_id} className="border-b border-navy-50 last:border-0 hover:bg-navy-50/40">
                    <td className="px-4 py-3">
                      <div className="font-medium text-ink">{s.adviser_name}</div>
                      <div className="text-[11px] text-ink-muted">{s.adviser_email}</div>
                    </td>
                    <td className="px-4 py-3 text-right font-mono text-ink">{money(s.aum, 0)}</td>
                    <td className="px-4 py-3 text-right text-ink-soft">{s.household_count}</td>
                    <td className="px-4 py-3 text-right">
                      <span className={s.recs_approved_90d > 0 ? "text-positive font-medium" : "text-ink-muted"}>{s.recs_approved_90d}</span>
                    </td>
                    <td className="px-4 py-3 text-right text-ink-soft">{s.meetings_90d}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </Card>
        )}
      </Layer>

      {/* 2.5 Risk, conduct & data */}
      <Layer active={tab === "risk"} n="2.5" title="Risk, conduct & data analytics" q="Can we defend this?" icon={ShieldCheck}>
        <div className="grid lg:grid-cols-3 gap-4">
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
          <Card className="lg:col-span-2">
            <CardTitle>Audit & explainability</CardTitle>
            <div className="flex items-center gap-3">
              <ShieldCheck className={rd.audit.chain_valid ? "text-positive" : "text-critical"} />
              <div className="text-sm text-ink-soft">
                <span className="font-semibold text-ink">{rd.audit.ledger_entries}</span> ledger entries ·
                chain <span className={rd.audit.chain_valid ? "text-positive" : "text-critical"}>{rd.audit.chain_valid ? "valid" : "broken"}</span> ·
                every decision fully reconstructable.
              </div>
            </div>
          </Card>
        </div>
      </Layer>
    </div>
  );
}

function Kpi({ icon: Icon, label, value, hint, accent, onClick }: any) {
  const c = accent === "positive" ? "text-positive" : accent === "critical" ? "text-critical" : "text-ink";
  const base = "card p-4 transition";
  const interactive = onClick ? " cursor-pointer hover:shadow-lift hover:border-navy-200 focus:outline-none focus:ring-2 focus:ring-navy-300" : "";
  return (
    <div className={base + interactive} onClick={onClick} tabIndex={onClick ? 0 : undefined}
         onKeyDown={(e) => e.key === "Enter" && onClick?.()} role={onClick ? "button" : undefined}>
      <div className="flex items-center gap-1.5 tile-label"><Icon size={13} /> {label}</div>
      <div className={`text-xl font-semibold mt-1 ${c}`}>{value}</div>
      {hint && <div className="text-[11px] text-ink-muted mt-0.5" title={hint}>{hint}</div>}
    </div>
  );
}

function Layer({ n, title, q, icon: Icon, children, active = true }: any) {
  if (!active) return null;
  return (
    <div className="mb-8">
      <div className="flex items-center gap-2 mb-3">
        <span className="h-8 w-8 rounded-lg bg-navy-800 text-white flex items-center justify-center"><Icon size={16} /></span>
        <div>
          <div className="font-semibold text-ink">{n} · {title}</div>
          <div className="text-xs text-ink-muted">{q}</div>
        </div>
      </div>
      {children}
    </div>
  );
}

const CardTitle = ({ children }: { children: ReactNode }) => <div className="tile-label mb-2">{children}</div>;
const Metric = ({ k, v, accent }: any) => (
  <div className="flex justify-between py-0.5 text-sm">
    <span className="text-ink-muted">{k}</span>
    <span className={`font-medium ${accent === "positive" ? "text-positive" : accent === "critical" ? "text-critical" : "text-ink-soft"}`}>{v}</span>
  </div>
);

function BarList({ items }: { items: { label: string; value: number; right: string }[] }) {
  const max = Math.max(...items.map((i) => i.value), 0.0001);
  return (
    <div className="space-y-1.5">
      {items.map((it, i) => (
        <div key={i}>
          <div className="flex justify-between text-xs"><span className="text-ink-soft">{it.label}</span><span className="text-ink-muted">{it.right}</span></div>
          <div className="h-1.5 rounded-full bg-navy-100 overflow-hidden"><div className="h-full bg-navy-600" style={{ width: `${(it.value / max) * 100}%` }} /></div>
        </div>
      ))}
    </div>
  );
}

function Table({ head, rows }: { head: string[]; rows: any[][] }) {
  return (
    <div className="overflow-x-auto">
      <table className="w-full text-sm">
        <thead className="text-ink-muted text-xs uppercase tracking-wide">
          <tr>{head.map((h, i) => <th key={i} className={`py-1.5 ${i === 0 ? "text-left" : "text-right"}`}>{h}</th>)}</tr>
        </thead>
        <tbody>
          {rows.map((r, i) => (
            <tr key={i} className="border-t border-navy-50">
              {r.map((c, j) => <td key={j} className={`py-1.5 ${j === 0 ? "text-left text-ink font-medium" : "text-right text-ink-soft tabular-nums"}`}>{c}</td>)}
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}
