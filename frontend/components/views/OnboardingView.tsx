"use client";
import { useState } from "react";
import { UserPlus, ShieldCheck, ShieldAlert, ChevronRight, FileText, AlertTriangle } from "lucide-react";
import { Card, Spinner, Segment, ErrorState } from "@/components/ui";
import { ViewLink } from "./ViewNav";
import { ViewHeader, chip } from "./ViewHeader";
import { useApi } from "@/lib/hooks";
import { api, getUser } from "@/lib/api";
import { RegistryChip } from "@/app/workspace/components/artifacts/IndiaKycRegistryCard";
import { titleCase } from "@/lib/format";

const REGISTRATION_LABELS: Record<string, string> = {
  individual: "Individual", joint_jtwros: "Joint JTWROS", joint_tic: "Joint TIC",
  traditional_ira: "Traditional IRA", roth_ira: "Roth IRA",
  employer_rollover: "Employer Rollover", trust: "Trust",
  entity_llc: "LLC", entity_corp: "Corp", entity_partnership: "Partnership",
  custodial_utma: "Custodial UTMA", custodial_ugma: "Custodial UGMA",
  estate_inherited: "Estate/Inherited", huf: "HUF",
  nri_nre: "NRI (NRE)", nri_nro: "NRI (NRO)", minor_guardian: "Minor",
  joint_all: "Joint (all sign)", joint_anyone: "Joint (anyone or survivor)", entity_llp: "LLP",
};

const AML_TIER_STYLE: Record<string, string> = {
  low: "bg-ok-bg text-ok",
  medium: "bg-warn-bg text-warn",
  high: "bg-crit-bg text-crit",
};

/**
 * The queue is grouped by who is waiting, not by pipeline stage.
 *
 * L200 describes onboarding as four tracks running in parallel; a case sits in all four at
 * once, so "which stage is it in" has no answer. What an adviser needs is "does this need
 * me, and what next" — so the columns are owners, and each card carries its four track
 * pips, its SLA age, and one next action.
 */
const OWNER_COLUMNS = [
  { key: "adviser", label: "Needs adviser" },
  { key: "ops", label: "Needs ops" },
  { key: "compliance", label: "Needs compliance" },
  { key: "external", label: "Waiting external" },
];

const TRACK_PIP: Record<string, string> = {
  complete: "bg-ok",
  in_progress: "bg-info",
  blocked: "bg-crit",
  waiting_external: "bg-warn",
  not_started: "bg-border",
};

const SLA_STYLE: Record<string, string> = {
  breached: "text-crit font-semibold",
  at_risk: "text-warn font-medium",
  on_track: "text-ink-muted",
  "n/a": "text-ink-faint",
};

function amlChip(status?: string) {
  if (status === "blocked") return <span className="chip bg-crit-bg text-crit"><ShieldAlert size={11} /> AML blocked</span>;
  if (status === "review") return <span className="chip bg-warn-bg text-warn"><ShieldAlert size={11} /> AML review</span>;
  if (status === "clear") return <span className="chip bg-ok-bg text-ok"><ShieldCheck size={11} /> AML clear</span>;
  return null;
}

/** The onboarding board: open cases by who is waiting, with the desk's operating metrics. A case
 * opens in the work pane beside the conversation when the board is there. */
export function OnboardingView() {
  const { data, loading, error, refetch } = useApi<any[]>("/api/onboarding/cases");
  const [showNew, setShowNew] = useState(false);
  const open = (data || []).filter((c) => c.status !== "approved" && c.status !== "rejected");
  const breached = open.filter((c) => c.sla_status === "breached").length;

  return (
    <div className="view-container">
      <ViewHeader
        title="Onboarding"
        meta={data ? (
          <>
            <span className={chip}>{open.length} open</span>
            {breached > 0 && <span className="chip bg-crit-bg text-crit">{breached} SLA breached</span>}
          </>
        ) : null}
        actions={<button className="btn-primary" data-testid="onboarding-new-prospect" onClick={() => setShowNew(true)}><UserPlus size={16} /> New prospect</button>}
      />

      {showNew && <NewProspect onClose={() => setShowNew(false)} onCreated={() => { setShowNew(false); refetch(); }} />}

      <MetricsStrip />

      {/* Where the book is actually stuck — L200 §4 operating metrics. */}

      {loading ? (
        <Spinner />
      ) : error ? (
        <div className="card p-8"><ErrorState what="the onboarding queue" message={error} onRetry={refetch} /></div>
      ) : (
        <>
          <div className="grid cq-sm:grid-cols-2 cq-lg:grid-cols-4 gap-4">
            {OWNER_COLUMNS.map((col) => {
              const cases = (data || []).filter(
                (c) => c.status !== "approved" && (c.tracks?.owner || "adviser") === col.key
              );
              return (
                <div key={col.key}>
                  <div className="flex items-center justify-between mb-2 px-1">
                    <span className="text-xs font-semibold uppercase tracking-wide text-ink-muted">{col.label}</span>
                    <span className="text-xs text-ink-muted">{cases.length}</span>
                  </div>
                  <div className="space-y-2">
                    {cases.map((c) => <CaseCard key={c.id} c={c} />)}
                    {cases.length === 0 && (
                      <div className="text-xs text-ink-faint px-1 py-3">Nothing waiting</div>
                    )}
                  </div>
                </div>
              );
            })}
          </div>

          {(data || []).some((c) => c.status === "approved") && (
            <div className="mt-6">
              <div className="text-xs font-semibold uppercase tracking-wide text-ink-muted mb-2 px-1">
                Activated
              </div>
              <div className="grid cq-sm:grid-cols-2 cq-lg:grid-cols-4 gap-4">
                {(data || []).filter((c) => c.status === "approved").map((c) => <CaseCard key={c.id} c={c} />)}
              </div>
            </div>
          )}
        </>
      )}
    </div>
  );
}

function Metric({ label, value, sub, tone }: { label: string; value: any; sub?: string; tone?: string }) {
  return (
    <div className="px-3.5 py-2.5">
      <div className="text-[10px] uppercase tracking-wide text-ink-muted">{label}</div>
      <div className={`text-lg font-semibold tabular-nums leading-tight mt-0.5 ${tone || "text-ink"}`}>
        {value ?? "—"}
      </div>
      {sub && <div className="text-[10px] text-ink-faint mt-0.5">{sub}</div>}
    </div>
  );
}

/**
 * L200 §4 operating metrics.
 *
 * Derived from the timestamps the evidence already carries — no separate metrics store, so
 * the numbers cannot disagree with the records they describe. Percentiles are nearest-rank:
 * on a book of this size, interpolation would invent values that never occurred.
 */
function MetricsStrip() {
  const { data, loading, error } = useApi<any>("/api/onboarding/metrics");
  if (loading || error || !data) return null;

  const ct = data.cycle_time_days.intake_to_activation;
  const fund = data.cycle_time_days.activation_to_funded;
  const age = data.cycle_time_days.open_case_age;
  const blockers = data.top_blockers || [];

  return (
    <div className="mb-5">
      <div className="card overflow-hidden">
        <div className="grid grid-cols-2 cq-sm:grid-cols-3 cq-lg:grid-cols-6 divide-x divide-y cq-lg:divide-y-0 divide-border-soft">
          <Metric label="Open" value={data.counts.open}
                  sub={`${data.counts.activated} activated`} />
          <Metric label="Cycle time P50" value={ct.p50 != null ? `${ct.p50}d` : "—"}
                  sub={ct.p90 != null ? `P90 ${ct.p90}d · n=${ct.n}` : "no activations yet"} />
          <Metric label="Days to fund" value={fund.p50 != null ? `${fund.p50}d` : "—"}
                  sub={fund.n ? `n=${fund.n}` : "none settled"} />
          <Metric label="First-pass yield"
                  value={data.quality.first_pass_yield_pct != null ? `${data.quality.first_pass_yield_pct}%` : "—"}
                  sub={`${data.quality.nigo_cases} NIGO`}
                  tone={data.quality.first_pass_yield_pct != null && data.quality.first_pass_yield_pct < 80 ? "text-warn" : undefined} />
          <Metric label="Screened"
                  value={data.screening.coverage_pct != null ? `${data.screening.coverage_pct}%` : "—"}
                  sub={`${data.screening.parties_screened}/${data.screening.parties_total} parties`}
                  tone={data.screening.coverage_pct != null && data.screening.coverage_pct < 100 ? "text-warn" : "text-ok"} />
          <Metric label="SLA breached" value={data.sla.breached}
                  sub={`${data.sla.at_risk} at risk · avg age ${age.p50 ?? "—"}d`}
                  tone={data.sla.breached > 0 ? "text-crit" : "text-ok"} />
        </div>

        {(blockers.length > 0 || data.screening.edd_backlog > 0) && (
          <div className="border-t border-border-soft px-3.5 py-2.5 flex items-center gap-x-5 gap-y-1 flex-wrap">
            {blockers.length > 0 && (
              <div className="text-[11px] text-ink-muted">
                <span className="uppercase tracking-wide text-[10px] mr-1.5">Top blockers</span>
                {blockers.slice(0, 3).map((b: any, i: number) => (
                  <span key={b.key} className="text-ink-soft">
                    {i > 0 && <span className="text-ink-faint"> · </span>}
                    {b.label} <span className="tabular-nums text-ink-muted">({b.count})</span>
                  </span>
                ))}
              </div>
            )}
            {data.screening.edd_backlog > 0 && (
              <div className="text-[11px] text-warn">
                EDD backlog {data.screening.edd_backlog} · oldest{" "}
                {data.screening.edd_backlog_age_days.p90 ?? "—"}d
              </div>
            )}
          </div>
        )}
      </div>
    </div>
  );
}

function CaseCard({ c }: { c: any }) {
  const t = c.tracks;
  return (
    <ViewLink href={`/studio/onboarding/${c.id}`} data-testid="onboarding-case-card" className="card p-3 block hover:shadow-lift transition group focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-accent">
      <div className="flex items-center justify-between gap-2">
        <span className="font-medium text-ink text-sm truncate">{c.prospect_name}</span>
        <ChevronRight size={15} className="text-ink-faint group-hover:text-ink shrink-0" />
      </div>

      {/* Four track pips — the state of A/B/C/D at a glance. */}
      {t?.tracks && (
        <div className="flex items-center gap-1 mt-2" title={t.tracks.map((x: any) => `${x.code} ${x.label}: ${x.state.replace(/_/g, " ")}`).join("\n")}>
          {t.tracks.map((x: any) => (
            <span key={x.code} className={`h-1.5 flex-1 rounded-full ${TRACK_PIP[x.state] || TRACK_PIP.not_started}`} />
          ))}
        </div>
      )}

      <div className="flex items-center gap-1.5 mt-2 flex-wrap">
        {c.registration_type ? (
          <span className="chip bg-border-soft text-ink-muted">{REGISTRATION_LABELS[c.registration_type] || titleCase(c.registration_type)}</span>
        ) : c.is_entity ? (
          <span className="chip bg-border-soft text-ink-muted">{titleCase(c.entity_type || "entity")}</span>
        ) : null}
        {c.aml_risk_tier && (
          <span className={`chip ${AML_TIER_STYLE[c.aml_risk_tier] || "bg-border-soft text-ink-muted"}`}>AML {c.aml_risk_tier}</span>
        )}
        {c.nigo_flag && <span className="chip bg-warn-bg text-warn flex items-center gap-1"><AlertTriangle size={10} /> NIGO</span>}
      </div>

      {/* India: the registry's own status, said before any timeline. */}
      {c.registry && c.status !== "approved" && (
        <div className="flex flex-wrap items-center gap-1.5 mt-2" data-testid="onboarding-card-registry">
          <RegistryChip registry={c.registry} />
          {c.registry.simulated && <span className="chip text-warn">simulated</span>}
        </div>
      )}

      <div className="flex items-center justify-between gap-2 mt-2 text-[10px]">
        <span className={SLA_STYLE[c.sla_status] || "text-ink-muted"}>
          {c.sla_status === "breached" ? "SLA breached" : c.sla_status === "at_risk" ? "SLA at risk"
            : c.registry && !c.registry.timeline_ok && c.status !== "approved" ? "No timeline yet" : `SLA ${c.sla_days}d`}
        </span>
        {t?.ready_to_activate && <span className="chip bg-ok-bg text-ok">Ready to activate</span>}
      </div>

      {t?.next_action && (
        <div className="text-[11px] text-ink-soft mt-2 pt-2 border-t border-border-soft leading-snug">
          {t.next_action}
        </div>
      )}
    </ViewLink>
  );
}

// `only`: an account type that exists in one market's law (backend aurea_core/onboarding_start.ONLY_IN).
// `not`: a market that does not use it (NOT_IN). `labelIn`: its name in a market where the US one would mislead.
export const REGISTRATION_OPTIONS: { value: string; label: string; only?: string; not?: string; labelIn?: Record<string, string> }[] = [
  { value: "individual", label: "Individual" },
  { value: "joint_jtwros", label: "Joint — JTWROS", not: "IN" },
  { value: "joint_tic", label: "Joint — Tenants in Common", not: "IN" },
  // India: how the holders operate the account is chosen here; there is no "joint" without a mode.
  { value: "joint_all", label: "Joint (all holders sign)", only: "IN" },
  { value: "joint_anyone", label: "Joint (anyone or survivor)", only: "IN" },
  { value: "traditional_ira", label: "Traditional IRA", only: "US" },
  { value: "roth_ira", label: "Roth IRA", only: "US" },
  { value: "employer_rollover", label: "Employer Rollover (401k/403b)", only: "US" },
  { value: "trust", label: "Trust" },
  { value: "entity_llc", label: "Entity — LLC", not: "IN" },
  { value: "entity_corp", label: "Entity — Corporation", labelIn: { IN: "Company" } },
  { value: "entity_partnership", label: "Entity — Partnership", labelIn: { IN: "Partnership" } },
  { value: "entity_llp", label: "Limited liability partnership (LLP)", only: "IN" },
  { value: "custodial_utma", label: "Custodial — UTMA", only: "US" },
  { value: "custodial_ugma", label: "Custodial — UGMA", only: "US" },
  { value: "estate_inherited", label: "Estate / Inherited" },
  { value: "huf", label: "HUF", only: "IN" },
  { value: "nri_nre", label: "NRI account (NRE)", only: "IN" },
  { value: "nri_nro", label: "NRI account (NRO)", only: "IN" },
  { value: "minor_guardian", label: "Minor, run by a guardian", only: "IN" },
];

// Indian account types come first, in the order an Indian family opens them, then the rest as listed.
const IN_FIRST = ["individual", "joint_all", "joint_anyone", "huf", "trust", "entity_corp", "entity_partnership", "entity_llp", "estate_inherited", "nri_nre", "nri_nro", "minor_guardian"];

/** The account types a market offers, named and ordered for it. */
export function registrationOptionsFor(market: string): { value: string; label: string }[] {
  const m = (market || "").toUpperCase();
  const rows = REGISTRATION_OPTIONS.filter((o) => (!o.only || o.only === m) && o.not !== m)
    .map((o) => ({ value: o.value, label: o.labelIn?.[m] ?? o.label }));
  if (m !== "IN") return rows;
  const rank = (v: string) => (IN_FIRST.includes(v) ? IN_FIRST.indexOf(v) : IN_FIRST.length);
  return [...rows].sort((a, b) => rank(a.value) - rank(b.value));
}

function NewProspect({ onClose, onCreated }: { onClose: () => void; onCreated: () => void }) {
  // Only what the person states is sent: a blank risk or mandate is left for the questionnaire and the adviser.
  const [form, setForm] = useState({
    prospect_name: "", registration_type: "individual", segment: "private_wealth",
    risk_profile: "", mandate_preference: "", source_of_wealth: "", household_id: "",
  });
  // The households already on the book, so a spouse joins theirs instead of making a second one.
  const { data: book } = useApi<any>("/api/advisers");
  const households: { id: string; name: string }[] = book?.households || [];
  const [busy, setBusy] = useState(false);
  const [failed, setFailed] = useState<string | null>(null);
  const market = (getUser()?.firm?.jurisdiction || "").toUpperCase();
  const options = registrationOptionsFor(market);

  const isRollover = ["employer_rollover", "traditional_ira", "roth_ira"].includes(form.registration_type);

  async function create() {
    if (!form.prospect_name) return;
    setBusy(true);
    setFailed(null);
    try {
      await api("/api/onboarding/cases", {
        body: {
          prospect_name: form.prospect_name,
          registration_type: form.registration_type,
          segment: form.segment,
          household_id: form.household_id || null,
          intake: {
            risk_profile: form.risk_profile || null, mandate_preference: form.mandate_preference || null,
            source_of_wealth: form.source_of_wealth || null,
          },
        },
      });
      onCreated();
    } catch (e: any) {
      setFailed(e?.message || "The case was not opened.");
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="fixed inset-0 bg-black/40 z-50 flex items-center justify-center p-4" onClick={onClose}>
      <div className="card p-6 w-full max-w-lg" onClick={(e) => e.stopPropagation()}>
        <h3 className="text-lg font-semibold text-ink mb-4">New prospect</h3>
        <div className="space-y-3">
          <div><label className="label">Name</label>
            <input className="input" value={form.prospect_name} onChange={(e) => setForm({ ...form, prospect_name: e.target.value })} placeholder="e.g. Daniel Okonkwo or Sokolov Family Trust" />
          </div>
          <div className="grid grid-cols-2 gap-3">
            <div><label className="label">Registration type</label>
              <select className="input" value={form.registration_type} onChange={(e) => setForm({ ...form, registration_type: e.target.value })}>
                {options.map((o) => <option key={o.value} value={o.value}>{o.label}</option>)}
              </select>
            </div>
            <div><label className="label">Segment</label>
              <select className="input" value={form.segment} onChange={(e) => setForm({ ...form, segment: e.target.value })}>
                <option value="private_wealth">Private wealth</option>
                <option value="mass_affluent">Mass affluent</option>
                <option value="for_purpose">For-purpose</option>
              </select>
            </div>
          </div>
          <div><label className="label" htmlFor="np-household">Household</label>
            <select id="np-household" className="input" data-testid="onboarding-new-household" value={form.household_id} onChange={(e) => setForm({ ...form, household_id: e.target.value })}>
              <option value="">A new household</option>
              {households.map((h) => <option key={h.id} value={h.id}>Joins {h.name}</option>)}
            </select>
          </div>
          <div className="grid grid-cols-2 gap-3">
            <div><label className="label">Risk profile</label>
              <select className="input" value={form.risk_profile} onChange={(e) => setForm({ ...form, risk_profile: e.target.value })}>
                <option value="">Not stated</option>
                <option value="conservative">Conservative</option>
                <option value="balanced">Balanced</option>
                <option value="growth">Growth</option>
                <option value="aggressive">Aggressive</option>
              </select>
            </div>
            <div><label className="label">Mandate</label>
              <select className="input" value={form.mandate_preference} onChange={(e) => setForm({ ...form, mandate_preference: e.target.value })}>
                <option value="">Not stated</option>
                <option value="advisory">Advisory</option>
                <option value="discretionary">Discretionary</option>
              </select>
            </div>
          </div>
          <div><label className="label">Source of wealth{isRollover ? " (required for PTE 2020-02)" : ""}</label>
            <input className="input" value={form.source_of_wealth} onChange={(e) => setForm({ ...form, source_of_wealth: e.target.value })} placeholder="e.g. Business sale proceeds" />
          </div>
          {isRollover && (
            <div className="rounded-lg bg-warn-bg border border-warn-soft px-3 py-2 text-xs text-warn">
              Rollover account — DOL PTE 2020-02 rationale memo will be required. Run the Rollover PTE Documenter agent after creation.
            </div>
          )}
          <div className="text-xs text-ink-muted flex items-center gap-1"><FileText size={12} /> Add identity documents on the next screen, then run the agent.</div>
          {failed && <p className="rounded-lg bg-crit-bg px-3 py-2 text-sm text-crit" role="alert">{failed}</p>}
        </div>
        <div className="flex justify-end gap-2 mt-5">
          <button className="btn-ghost" onClick={onClose}>Cancel</button>
          <button className="btn-primary" disabled={busy} onClick={create}>{busy ? "Creating…" : "Create case"}</button>
        </div>
      </div>
    </div>
  );
}
