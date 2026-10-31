"use client";

import { useState } from "react";
import Link from "next/link";
import { Play, TrendingUp, Users2, Target, GitBranch, Building2, PiggyBank, SlidersHorizontal,
  CalendarClock, FileText, Sparkles, RefreshCw, Layers, AlertTriangle, Check } from "lucide-react";
import { Breadcrumb, Card, SkeletonCard, StatTile, Empty } from "@/components/ui";
import { AllocationDonut } from "@/components/Charts";
import RetirementPlanner from "@/components/RetirementPlanner";
import PortfolioWhatIf from "@/components/PortfolioWhatIf";
import { useAgentRunner } from "@/components/AgentConsole";
import { useApi } from "@/lib/hooks";
import { api } from "@/lib/api";
import { money, pct, titleCase } from "@/lib/format";
import { withRef } from "@/lib/refs";
import { reviewDay } from "@/app/workspace/components/artifacts/ReviewsCard";
import { useViewNav } from "./ViewNav";
import { ViewHeader, chip } from "./ViewHeader";

const HOUSEHOLD_AGENTS = [
  { key: "meeting_prep", label: "Meeting prep" },
  { key: "research_reporting", label: "Research note" },
  { key: "next_best_action", label: "Next-best-action" },
  { key: "client_care", label: "Client care" },
  { key: "estate_succession", label: "Estate & succession" },
  { key: "behavioural_finance", label: "Behavioural finance" },
  { key: "ips_drafting", label: "Draft IPS" },
  { key: "glide_path", label: "Glide path" },
  { key: "asset_location", label: "Asset location" },
  { key: "wallet_share_scout", label: "Wallet share" },
];

const sectionTitle = "mb-3 flex items-center gap-2 font-semibold text-ink";

function AccountSleeves({ accountId }: { accountId: string }) {
  const { data } = useApi<any>(`/api/sleeves?account_id=${accountId}`, [accountId]);
  const { data: recon } = useApi<any>(
    data?.items?.length ? `/api/sleeves/accounts/${accountId}/reconcile` : null, [accountId, data?.items?.length]
  );

  if (!data?.items?.length) return null;

  return (
    <div className="mt-3 border-t border-border-soft pt-3">
      <div className="mb-2 flex items-center gap-2">
        <Layers size={14} className="text-ink-muted" />
        <span className="tile-label">Sleeves · {data.items.length}</span>
        {recon && (
          <span className={`ml-auto inline-flex items-center gap-1 text-[11px] ${recon.clean ? "text-ok" : "text-warn"}`}>
            {recon.clean ? <Check size={12} /> : <AlertTriangle size={12} />}
            {recon.clean ? "Reconciled" : `${recon.breaks.length} break(s)`}
          </span>
        )}
      </div>
      <div className="flex flex-wrap gap-1.5">
        {data.items.map((s: any) => (
          <span key={s.id} className={chip}>
            {s.name} · {(s.target_weight * 100).toFixed(0)}%
          </span>
        ))}
      </div>
    </div>
  );
}

const RMD_STATUS_LABEL: Record<string, string> = {
  required: "RMD required", not_yet: "No RMD yet", ten_year_rule: "10-year rule",
  life_expectancy: "Life-expectancy method", needs_election: "Needs election", unknown: "RMD status unknown",
};
const RMD_STATUS_COLOR: Record<string, string> = {
  required: "text-warn", needs_election: "text-crit", ten_year_rule: "text-warn",
};

function AccountRegistration({ accountId }: { accountId: string }) {
  const nav = useViewNav();
  const { data, refetch } = useApi<any>(`/api/accounts/${accountId}/registration`, [accountId]);
  const [adding, setAdding] = useState(false);
  const [name, setName] = useState("");
  const [pct100, setPct100] = useState("100");
  const [relationship, setRelationship] = useState("");
  const [busy, setBusy] = useState(false);

  if (!data || !data.registration_type) return null;

  async function submit() {
    if (!name.trim()) return;
    setBusy(true);
    try {
      await api(`/api/accounts/${accountId}/beneficiaries`, {
        method: "POST",
        body: { beneficiary_name: name.trim(), percentage: Number(pct100), relationship_to_owner: relationship || undefined },
      });
      setName(""); setRelationship(""); setPct100("100"); setAdding(false);
      refetch();
      nav.changed();
    } finally {
      setBusy(false);
    }
  }

  const rmd = data.rmd;
  const needsBeneficiaries = ["traditional_ira", "roth_ira", "employer_rollover", "inherited_ira",
    "estate_inherited", "plan_529", "hsa"].includes(data.registration_type);

  return (
    <div className="mt-3 border-t border-border-soft pt-3">
      <div className="mb-2 flex flex-wrap items-center gap-2">
        <span className="tile-label">{data.registration_type.replace(/_/g, " ")}</span>
        {rmd && (
          <span className={`ml-auto text-[11px] ${RMD_STATUS_COLOR[rmd.status] || "text-ink-muted"}`}>
            {RMD_STATUS_LABEL[rmd.status] || rmd.status}
            {rmd.status === "required" && ` · ${money(rmd.amount)}`}
          </span>
        )}
      </div>
      {needsBeneficiaries && (
        <div className="space-y-1">
          {data.beneficiaries.map((b: any) => (
            <div key={b.id} className="flex flex-wrap items-center gap-2 text-xs">
              <span className="text-ink-soft">{b.beneficiary_name}</span>
              <span className="text-ink-muted">{b.designation_class} · {b.percentage}%</span>
            </div>
          ))}
          {data.beneficiaries.length === 0 && <div className="text-xs text-crit">No beneficiaries</div>}
          {adding ? (
            <div className="mt-1.5 flex flex-wrap items-center gap-1.5">
              <input className="input w-32 text-xs" placeholder="Beneficiary name" aria-label="Beneficiary name" value={name}
                onChange={(e) => setName(e.target.value)} />
              <input className="input w-24 text-xs" placeholder="Relationship" aria-label="Relationship" value={relationship}
                onChange={(e) => setRelationship(e.target.value)} />
              <input className="input w-16 text-xs" type="number" aria-label="Percentage" value={pct100}
                onChange={(e) => setPct100(e.target.value)} />
              <button className="btn-primary text-xs" disabled={busy || !name.trim()} onClick={submit}>Add</button>
              <button className="btn-ghost text-xs" aria-label="Cancel" onClick={() => setAdding(false)}>×</button>
            </div>
          ) : (
            <button className="btn-ghost mt-1 text-xs" onClick={() => setAdding(true)}>+ Add beneficiary</button>
          )}
        </div>
      )}
    </div>
  );
}

function HouseholdPerformance({ householdId }: { householdId: string }) {
  const { data } = useApi<any>(`/api/core/households/${householdId}/performance`, [householdId]);
  if (!data) return null;
  const tone = (n: number | null | undefined) => (n == null ? "text-ink-muted" : n >= 0 ? "text-ok" : "text-crit");
  const fmt = (n: number | null | undefined) => (n == null ? "—" : `${n > 0 ? "+" : ""}${(n * 100).toFixed(2)}%`);
  const d = data.disclosures;
  return (
    <Card>
      <div className="mb-3 flex flex-wrap items-center justify-between gap-2">
        <div className="flex items-center gap-2 font-semibold text-ink"><TrendingUp size={17} /> Performance</div>
        <span className="font-mono text-xs text-ink-muted">
          {data.start && data.end ? `${data.start} → ${data.end} · ${data.months} mo` : "Not measurable"}
        </span>
      </div>
      <div className="flex flex-wrap gap-x-8 gap-y-2" data-testid="household-performance">
        <div><div className="tile-label">Time-weighted</div><div className={`font-mono text-lg tabular-nums ${tone(data.twr)}`}>{fmt(data.twr)}</div></div>
        <div><div className="tile-label">Money-weighted</div><div className={`font-mono text-lg tabular-nums ${tone(data.mwr?.period)}`}>{fmt(data.mwr?.period)}</div></div>
        {data.benchmark_symbol && (
          <div><div className="tile-label">Benchmark · {data.benchmark_symbol}</div><div className={`font-mono text-lg tabular-nums ${tone(data.benchmark_return)}`}>{fmt(data.benchmark_return)}</div></div>
        )}
        {data.alpha != null && (
          <div><div className="tile-label">Alpha</div><div className={`font-mono text-lg tabular-nums ${tone(data.alpha)}`}>{fmt(data.alpha)}</div></div>
        )}
      </div>
      <div className="mt-3 flex flex-wrap gap-1.5">
        <span className="chip bg-border-soft text-ink-muted">Securities only</span>
        <span className="chip bg-border-soft text-ink-muted">Gross of fees</span>
        {d.synthetic_price_symbols.length > 0 && <span className="chip bg-border-soft text-warn">Synthetic price: {d.synthetic_price_symbols.join(", ")}</span>}
        {d.declared_price_symbols?.length > 0 && <span className="chip bg-border-soft text-ink-muted">Stated valuation: {d.declared_price_symbols.join(", ")}</span>}
        {d.unpriced_flat_symbols.length > 0 && <span className="chip bg-border-soft text-warn">Held flat: {d.unpriced_flat_symbols.join(", ")}</span>}
        {d.ledger_gap_lots > 0 && <span className="chip bg-border-soft text-warn">{d.ledger_gap_lots} purchase(s) not in ledger</span>}
        {d.stale_days > 45 && <span className="chip bg-border-soft text-warn">Prices as of {d.as_of}</span>}
      </div>
    </Card>
  );
}

function WalletShareTile({ persons, totalValue }: { persons: any[]; totalValue: number }) {
  const heldAway = persons.reduce((s: number, p: any) => s + ((p.profile?.held_away) || 0), 0);
  const total = totalValue + heldAway;
  const share = total > 0 ? totalValue / total : 1;
  const isQP = totalValue >= 5_000_000;
  const isAI = totalValue >= 1_000_000;
  return (
    <div className="card p-4">
      <div className="tile-label">Wallet share</div>
      <div className="mt-1 text-2xl font-semibold tabular-nums text-ink">{Math.round(share * 100)}%</div>
      {/* Held-away assets are what the client told us about, not custodied: called declared (rule 7). */}
      <div className="mt-0.5 text-[11px] text-ink-muted tabular-nums">
        {heldAway > 0 ? `${money(heldAway)} declared held away` : "None declared held away"}
      </div>
      {isQP && <div className={`${chip} mt-1 text-[10px]`}>Qualified Purchaser</div>}
      {isAI && !isQP && <div className={`${chip} mt-1 text-[10px]`}>Accredited Investor</div>}
    </div>
  );
}

/** Renders nothing for a role that does not read reviews. */
function NextReviewChip({ householdId }: { householdId: string }) {
  const { data } = useApi<any>(`/api/reviews/households/${householdId}`, [householdId]);
  if (!data) return null;
  return (
    <span className={`${chip} ${data.status === "overdue" ? "text-crit" : ""}`} data-testid="client-next-review">
      {data.next_due ? `Next review ${reviewDay(data.next_due)}` : "No review on record"}
    </span>
  );
}

/** Renders nothing for a role that does not read IPS monitoring, or a household with no mandate. */
function IpsChip({ householdId }: { householdId: string }) {
  const { data } = useApi<any>(`/api/ips/households/${householdId}`, [householdId]);
  const s = data?.summary;
  if (!s?.mandates) return null;
  return (
    <span className={`${chip} ${s.breach ? "text-crit" : ""}`} data-testid="client-ips">
      {s.breach ? `IPS breach ${s.breach}`
        : s.excepted ? `IPS exception ${s.excepted}`
        : s.no_ips === s.mandates ? "No IPS on file"
        : s.no_ips ? `No IPS on ${s.no_ips} of ${s.mandates}`
        : s.within ? "Within IPS" : "IPS not assessed"}
    </span>
  );
}

/** One household: its total-portfolio view, accounts and positions, members, goals, and the agents
 * that work on it. In the work pane an agent run is a sentence to the conversation beside it, so it
 * goes through `run_agent` and its Confirm card (which shows the tier) like any other run. */
export function ClientView({ id }: { id: string }) {
  const nav = useViewNav();
  const { data: brain, loading } = useApi<any>(`/api/core/households/${id}`, [id]);
  const { data: planning } = useApi<any>(`/api/core/households/${id}/planning`, [id]);
  const runner = useAgentRunner();
  const [busy, setBusy] = useState<string | null>(null);
  const [showWhatIf, setShowWhatIf] = useState(false);

  if (loading) return (
    <div className="view-container space-y-4">
      {!nav.inPane && <Breadcrumb items={[{ label: "Clients", href: "/studio/clients" }, { label: "Loading…" }]} />}
      <SkeletonCard rows={4} />
    </div>
  );
  if (!brain) return <Empty>Client not found.</Empty>;

  const t = brain.totals;
  const name: string = brain.household.name;

  function run(agentKey: string, subjectType: string, subjectId: string, label: string, subjectName?: string) {
    if (!subjectId) return;
    setBusy(agentKey + subjectId);
    setTimeout(() => setBusy(null), 600);
    if (nav.say) {
      // Said in words, with the exact record riding along as a ref the reader never sees.
      const sentence =
        agentKey === "meeting_prep" ? `Prepare a meeting brief for ${name}.`
        : agentKey === "research_reporting" ? `Draft a research note for ${name}.`
        : agentKey === "drift_rebalancing" ? `Check whether ${subjectName || "this account"} for ${name} needs rebalancing.`
        : `Have the ${label} agent look at ${subjectName ? `${subjectName} for ${name}` : name}.`;
      nav.say(withRef(sentence, subjectType === "mandate" ? "mandate" : "household", subjectId));
      return;
    }
    runner.run({ agentKey, subjectType, subjectId, label });
  }

  return (
    <div className="view-container">
      {!nav.inPane && <Breadcrumb items={[{ label: "Clients", href: "/studio/clients" }, { label: name }]} />}

      <ViewHeader
        title={name}
        meta={
          <>
            {brain.household.segment && <span className={chip}>{titleCase(brain.household.segment)}</span>}
            <span className={`${chip} tabular-nums`}>Data confidence {Math.round(t.data_confidence * 100)}%</span>
            <NextReviewChip householdId={id} />
            <IpsChip householdId={id} />
          </>
        }
        actions={
          <>
            <button className="btn-outline" data-testid="client-whatif" aria-pressed={showWhatIf} onClick={() => setShowWhatIf((v) => !v)}>
              <SlidersHorizontal size={15} /> What-if
            </button>
            <button className="btn-outline" data-testid="client-prep-meeting" disabled={busy === "meeting_prep" + id}
              onClick={() => run("meeting_prep", "household", id, "Meeting prep")}>
              <CalendarClock size={15} /> Prep meeting
            </button>
            <button className="btn-outline" data-testid="client-research-note" disabled={busy === "research_reporting" + id}
              onClick={() => run("research_reporting", "household", id, "Research note")}>
              <FileText size={15} /> Research note
            </button>
            <Link href={`/canvas?household_id=${id}`} className="btn-ghost">
              <Sparkles size={15} /> View as client
            </Link>
          </>
        }
      />

      {showWhatIf && <PortfolioWhatIf allocation={t.by_asset_class} />}

      <div className="mb-6 grid grid-cols-2 gap-3 cq-md:grid-cols-3 cq-lg:grid-cols-5">
        <StatTile label="Total portfolio" value={<span className="tabular-nums">{money(t.total_value)}</span>} />
        <StatTile label="Accounts" value={brain.accounts.length} />
        <StatTile label="Entities & trusts" value={brain.entities.length} />
        <StatTile label="Goals" value={brain.goals.length} />
        <WalletShareTile persons={brain.persons} totalValue={t.total_value} />
      </div>

      <div className="grid gap-5 cq-lg:grid-cols-3">
        <div className="space-y-5 cq-lg:col-span-2">
          <Card>
            <div className={sectionTitle}><TrendingUp size={17} className="text-ink-muted" /> Total portfolio</div>
            <AllocationDonut data={t.by_asset_class} />
          </Card>

          <HouseholdPerformance householdId={id} />

          {brain.accounts.map((acc: any) => (
            <Card key={acc.id}>
              <div className="mb-3 flex items-start justify-between gap-3">
                <div className="min-w-0">
                  <div className="truncate font-semibold text-ink">{acc.name}</div>
                  <div className="mt-0.5 text-xs text-ink-muted">{acc.custodian}</div>
                </div>
                <div className="flex shrink-0 flex-col items-end gap-1.5 text-right">
                  <div className="font-semibold tabular-nums text-ink">{money(acc.total_value)}</div>
                  <button
                    data-testid="client-rebalance"
                    disabled={busy === "drift_rebalancing" + acc.mandate_id}
                    onClick={() => run("drift_rebalancing", "mandate", acc.mandate_id, "Drift", acc.name)}
                    className="btn-outline flex items-center gap-1 text-xs"
                    title="Check drift against target and draft a rebalancing proposal"
                  >
                    <RefreshCw size={13} />
                    {busy === "drift_rebalancing" + acc.mandate_id ? "Running…" : "Rebalance"}
                  </button>
                </div>
              </div>
              <div className="overflow-x-auto">
                <table className="w-full text-sm">
                  <thead className="border-b border-border text-[11px] uppercase tracking-wide text-ink-muted">
                    <tr>
                      <th className="py-1.5 text-left">Instrument</th>
                      <th className="py-1.5 text-left">Class</th>
                      <th className="py-1.5 text-right">Value</th>
                      <th className="py-1.5 text-right">Unrealised</th>
                      <th className="py-1.5 text-right">Price</th>
                    </tr>
                  </thead>
                  <tbody>
                    {acc.positions.map((p: any, i: number) => (
                      <tr key={i} className="border-b border-border-soft">
                        <td className="py-1.5 pr-2 font-medium text-ink">{p.instrument}</td>
                        <td className="py-1.5 pr-2 text-ink-muted">{titleCase(p.asset_class)}</td>
                        <td className="py-1.5 text-right tabular-nums text-ink-soft">{money(p.market_value)}</td>
                        <td className={`py-1.5 text-right tabular-nums ${p.unrealised_gain >= 0 ? "text-ok" : "text-crit"}`}>
                          {money(p.unrealised_gain)}
                        </td>
                        <td className={`py-1.5 pl-2 text-right text-xs ${p.price_source === "synthetic" ? "text-warn" : "text-ink-muted"}`}>
                          {p.price_source || "—"}
                        </td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
              <AccountSleeves accountId={acc.id} />
              <AccountRegistration accountId={acc.id} />
            </Card>
          ))}
        </div>

        <div className="space-y-5">
          <Card>
            <div className={sectionTitle}><Users2 size={17} className="text-ink-muted" /> Members & entities</div>
            <div className="space-y-2">
              {brain.persons.map((p: any) => (
                <div key={p.id} className="flex items-center gap-2 text-sm">
                  <span aria-hidden className="flex h-7 w-7 items-center justify-center rounded-full bg-border-soft text-xs font-semibold text-ink-soft">
                    {p.full_name.slice(0, 1)}
                  </span>
                  <span className="text-ink-soft">{p.full_name}</span>
                  {p.is_next_gen && <span className={chip}>Next-gen</span>}
                </div>
              ))}
              {brain.entities.map((e: any) => (
                <div key={e.id} className="flex items-center gap-2 text-sm">
                  <span aria-hidden className="flex h-7 w-7 items-center justify-center rounded-full bg-border-soft text-ink-muted">
                    <Building2 size={14} />
                  </span>
                  <span className="text-ink-soft">{e.name}</span>
                  <span className="chip bg-border-soft text-ink-muted">{titleCase(e.entity_type)}</span>
                </div>
              ))}
            </div>
          </Card>

          <Card>
            <div className={sectionTitle}><Target size={17} className="text-ink-muted" /> Goals</div>
            {planning?.goals?.length ? (
              <div className="space-y-3">
                {planning.goals.map((g: any, i: number) => (
                  <div key={i}>
                    <div className="flex justify-between gap-2 text-sm">
                      <span className="text-ink-soft">{g.goal}</span>
                      <span className={`shrink-0 font-medium ${g.on_track ? "text-ok" : "text-warn"}`}>
                        {g.on_track ? "On track" : "Off track"}
                      </span>
                    </div>
                    <div className="mt-1 h-2 overflow-hidden rounded-full bg-border">
                      <div className={`h-full ${g.on_track ? "bg-ok" : "bg-warn"}`} style={{ width: `${Math.min(100, g.probability_of_success * 100)}%` }} />
                    </div>
                    <div className="mt-0.5 text-xs tabular-nums text-ink-muted">
                      {pct(g.probability_of_success, 0)} probability · target {money(g.target_amount)}
                    </div>
                  </div>
                ))}
              </div>
            ) : (
              <div className="text-sm text-ink-muted">No goals</div>
            )}
            {planning?.risk && (
              <dl className="mt-3 space-y-1 border-t border-border pt-3 text-xs text-ink-muted">
                <div className="flex justify-between"><dt>Expected return</dt><dd className="tabular-nums">{pct(planning.risk.expected_return)}</dd></div>
                <div className="flex justify-between"><dt>Volatility</dt><dd className="tabular-nums">{pct(planning.risk.volatility)}</dd></div>
                <div className="flex justify-between"><dt>1y 95% VaR</dt><dd className="tabular-nums">{money(planning.risk.var_95_1y)}</dd></div>
              </dl>
            )}
          </Card>

          <Card>
            <div className={sectionTitle}><Play size={16} className="text-ink-muted" /> Run an agent</div>
            <div className="grid grid-cols-2 gap-2">
              {HOUSEHOLD_AGENTS.map((a) => (
                <button
                  key={a.key}
                  disabled={busy === a.key + id}
                  onClick={() => run(a.key, "household", id, a.label)}
                  className="btn-outline justify-start text-xs"
                >
                  {busy === a.key + id ? "Running…" : a.label}
                </button>
              ))}
            </div>
          </Card>

          {brain.relationships?.length > 0 && (
            <Card>
              <div className={sectionTitle}><GitBranch size={16} className="text-ink-muted" /> Relationships</div>
              <div className="flex flex-wrap gap-1.5">
                {brain.relationships.map((r: any, i: number) => (
                  <span key={i} className={chip} title={r.to_name}>{titleCase(r.kind)}</span>
                ))}
              </div>
            </Card>
          )}
        </div>
      </div>

      <div className="mt-8">
        <div className="mb-3 flex items-center gap-2">
          <PiggyBank size={18} className="text-ink-muted" />
          <h2 className="text-lg font-semibold text-ink">Retirement income</h2>
        </div>
        <RetirementPlanner basePath={`/api/core/households/${id}/retirement`} />
      </div>
    </div>
  );
}
