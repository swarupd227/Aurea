"use client";
import { useState } from "react";
import Link from "next/link";
import { useParams } from "next/navigation";
import { Play, TrendingUp, Users2, Target, GitBranch, Building2, PiggyBank, SlidersHorizontal,
  CalendarClock, FileText, Sparkles, RefreshCw } from "lucide-react";
import PageHeader from "@/components/PageHeader";
import { Breadcrumb, Card, SkeletonCard, StatTile, Empty, TierBadge } from "@/components/ui";
import { AllocationDonut } from "@/components/Charts";
import RetirementPlanner from "@/components/RetirementPlanner";
import PortfolioWhatIf from "@/components/PortfolioWhatIf";
import { useAgentRunner } from "@/components/AgentConsole";
import { useApi } from "@/lib/hooks";
import { money, pct, titleCase } from "@/lib/format";

const HOUSEHOLD_AGENTS = [
  { key: "meeting_prep", label: "Meeting prep" },
  { key: "research_reporting", label: "Research note" },
  { key: "next_best_action", label: "Next-best-action" },
  { key: "client_care", label: "Client care" },
];

export default function ClientDetail() {
  const { id } = useParams<{ id: string }>();
  const { data: brain, loading } = useApi<any>(`/api/core/households/${id}`, [id]);
  const { data: planning } = useApi<any>(`/api/core/households/${id}/planning`, [id]);
  const runner = useAgentRunner();
  const [busy, setBusy] = useState<string | null>(null);
  const [showWhatIf, setShowWhatIf] = useState(false);

  if (loading) return (
    <div className="space-y-4">
      <Breadcrumb items={[{ label: "Clients", href: "/studio/clients" }, { label: "Loading…" }]} />
      <SkeletonCard rows={4} />
    </div>
  );
  if (!brain) return <Empty>Client not found.</Empty>;

  const t = brain.totals;

  function run(agentKey: string, subjectType: string, subjectId: string, label: string) {
    if (!subjectId) return;
    setBusy(agentKey + subjectId);
    setTimeout(() => setBusy(null), 600);
    runner.run({ agentKey, subjectType, subjectId, label });
  }

  return (
    <div>
      <Breadcrumb items={[{ label: "Clients", href: "/studio/clients" }, { label: brain.household.name }]} />

      <PageHeader
        title={brain.household.name}
        sub={`${titleCase(brain.household.segment)} · data confidence ${Math.round(t.data_confidence * 100)}%`}
        actions={
          <div className="flex flex-wrap items-center gap-2">
            <button className="btn-gold" onClick={() => setShowWhatIf((v) => !v)}>
              <SlidersHorizontal size={15} /> What-if
            </button>
            <button className="btn-outline" disabled={busy === "meeting_prep" + id}
              onClick={() => run("meeting_prep", "household", id, "Meeting prep")}>
              <CalendarClock size={15} /> Prep meeting
            </button>
            <button className="btn-outline" disabled={busy === "research_reporting" + id}
              onClick={() => run("research_reporting", "household", id, "Research note")}>
              <FileText size={15} /> Research note
            </button>
            <Link href={`/canvas?household_id=${id}`} className="btn-ghost">
              <Sparkles size={15} /> View as client
            </Link>
          </div>
        }
      />

      {showWhatIf && <PortfolioWhatIf allocation={t.by_asset_class} />}

      <div className="grid grid-cols-2 lg:grid-cols-4 gap-4 mb-6">
        <StatTile label="Total portfolio" value={money(t.total_value)} accent="gold" />
        <StatTile label="Accounts" value={brain.accounts.length} />
        <StatTile label="Entities & trusts" value={brain.entities.length} />
        <StatTile label="Goals" value={brain.goals.length} />
      </div>

      <div className="grid lg:grid-cols-3 gap-5">
        {/* Left: allocation + accounts */}
        <div className="lg:col-span-2 space-y-5">
          <Card>
            <div className="text-lg font-semibold text-ink mb-3 flex items-center gap-2">
              <TrendingUp size={18} className="text-navy-600" /> Total-portfolio view
            </div>
            <AllocationDonut data={t.by_asset_class} />
          </Card>

          {brain.accounts.map((acc: any) => (
            <Card key={acc.id}>
              <div className="flex items-start justify-between mb-3">
                <div>
                  <div className="font-semibold text-ink">{acc.name}</div>
                  <div className="text-xs text-ink-muted mt-0.5">{acc.custodian} · golden record</div>
                </div>
                <div className="text-right flex flex-col items-end gap-1.5">
                  <div className="font-semibold text-ink">{money(acc.total_value)}</div>
                  <button
                    disabled={busy === "drift_rebalancing" + acc.mandate_id}
                    onClick={() => run("drift_rebalancing", "mandate", acc.mandate_id, "Drift")}
                    className="btn-outline text-xs flex items-center gap-1"
                    title="Check drift vs target allocation and draft a rebalancing proposal"
                  >
                    <RefreshCw size={13} />
                    {busy === "drift_rebalancing" + acc.mandate_id ? "Running…" : "Rebalance"}
                  </button>
                </div>
              </div>
              <div className="overflow-x-auto">
                <table className="w-full text-sm">
                  <thead className="text-ink-muted text-xs uppercase tracking-wide border-b border-navy-100">
                    <tr>
                      <th className="py-1.5 text-left">Instrument</th>
                      <th className="py-1.5 text-left">Class</th>
                      <th className="py-1.5 text-right">Value</th>
                      <th className="py-1.5 text-right">Unrealised</th>
                      <th className="py-1.5 text-right">Source</th>
                    </tr>
                  </thead>
                  <tbody>
                    {acc.positions.map((p: any, i: number) => (
                      <tr key={i} className="border-b border-navy-50">
                        <td className="py-1.5 font-medium text-ink">{p.instrument}</td>
                        <td className="py-1.5 text-ink-muted">{titleCase(p.asset_class)}</td>
                        <td className="py-1.5 text-right tabular-nums">{money(p.market_value)}</td>
                        <td className={`py-1.5 text-right tabular-nums ${p.unrealised_gain >= 0 ? "text-positive" : "text-critical"}`}>
                          {money(p.unrealised_gain)}
                        </td>
                        <td className="py-1.5 text-right text-xs text-ink-muted">{p.price_source || "—"}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            </Card>
          ))}
        </div>

        {/* Right: members, goals, agents */}
        <div className="space-y-5">
          <Card>
            <div className="font-semibold text-ink mb-3 flex items-center gap-2"><Users2 size={17} /> Members & entities</div>
            <div className="space-y-2">
              {brain.persons.map((p: any) => (
                <div key={p.id} className="flex items-center gap-2 text-sm">
                  <span className="h-7 w-7 rounded-full bg-navy-100 text-navy-700 flex items-center justify-center text-xs font-semibold">
                    {p.full_name.slice(0, 1)}
                  </span>
                  <span className="text-ink-soft">{p.full_name}</span>
                  {p.is_next_gen && <span className="chip bg-gold-soft/40 text-gold-dark">Next-gen</span>}
                </div>
              ))}
              {brain.entities.map((e: any) => (
                <div key={e.id} className="flex items-center gap-2 text-sm">
                  <span className="h-7 w-7 rounded-full bg-navy-50 text-navy-600 flex items-center justify-center">
                    <Building2 size={14} />
                  </span>
                  <span className="text-ink-soft">{e.name}</span>
                  <span className="chip bg-navy-50 text-ink-muted">{titleCase(e.entity_type)}</span>
                </div>
              ))}
            </div>
          </Card>

          <Card>
            <div className="font-semibold text-ink mb-3 flex items-center gap-2"><Target size={17} /> Goals · am I on track?</div>
            {planning?.goals?.length ? (
              <div className="space-y-3">
                {planning.goals.map((g: any, i: number) => (
                  <div key={i}>
                    <div className="flex justify-between text-sm">
                      <span className="text-ink-soft">{g.goal}</span>
                      <span className={g.on_track ? "text-positive font-medium" : "text-caution font-medium"}>
                        {g.on_track ? "On track" : "Needs attention"}
                      </span>
                    </div>
                    <div className="h-2 rounded-full bg-navy-100 mt-1 overflow-hidden">
                      <div className={`h-full ${g.on_track ? "bg-positive" : "bg-caution"}`} style={{ width: `${Math.min(100, g.probability_of_success * 100)}%` }} />
                    </div>
                    <div className="text-xs text-ink-muted mt-0.5">
                      {pct(g.probability_of_success, 0)} probability · target {money(g.target_amount)}
                    </div>
                  </div>
                ))}
              </div>
            ) : (
              <div className="text-sm text-ink-muted">No goals modelled.</div>
            )}
            {planning?.risk && (
              <div className="mt-3 pt-3 border-t border-navy-100 text-xs text-ink-muted space-y-1">
                <div className="flex justify-between"><span>Expected return</span><span>{pct(planning.risk.expected_return)}</span></div>
                <div className="flex justify-between"><span>Volatility</span><span>{pct(planning.risk.volatility)}</span></div>
                <div className="flex justify-between"><span>1y 95% VaR</span><span>{money(planning.risk.var_95_1y)}</span></div>
              </div>
            )}
          </Card>

          <Card>
            <div className="font-semibold text-ink mb-1 flex items-center gap-2"><Play size={16} /> Run an agent</div>
            <div className="text-[11px] text-ink-muted mb-2">Results stream live — watch in the Workforce console that opens.</div>
            <div className="grid grid-cols-2 gap-2">
              {HOUSEHOLD_AGENTS.map((a) => (
                <button
                  key={a.key}
                  disabled={busy === a.key + id}
                  onClick={() => run(a.key, "household", id, a.label)}
                  className="btn-outline text-xs justify-start"
                >
                  {busy === a.key + id ? "Running…" : a.label}
                </button>
              ))}
            </div>
          </Card>

          {brain.relationships?.length > 0 && (
            <Card>
              <div className="font-semibold text-ink mb-2 flex items-center gap-2"><GitBranch size={16} /> Relationships</div>
              <div className="flex flex-wrap gap-1.5">
                {brain.relationships.map((r: any, i: number) => (
                  <span key={i} className="chip bg-navy-50 text-ink-soft" title={r.to_name}>{titleCase(r.kind)}</span>
                ))}
              </div>
            </Card>
          )}
        </div>
      </div>

      {/* Retirement income & decumulation plan */}
      <div className="mt-8">
        <div className="flex items-center gap-2 mb-3">
          <PiggyBank size={18} className="text-navy-600" />
          <h2 className="text-lg font-semibold text-ink">Retirement income plan</h2>
          <span className="text-xs text-ink-muted">Will the money last? · decumulation & sequence-of-returns</span>
        </div>
        <RetirementPlanner basePath={`/api/core/households/${id}/retirement`} />
      </div>
    </div>
  );
}
