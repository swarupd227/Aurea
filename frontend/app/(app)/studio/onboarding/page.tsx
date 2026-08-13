"use client";
import { useState } from "react";
import Link from "next/link";
import { UserPlus, ShieldCheck, ShieldAlert, ChevronRight, FileText, AlertTriangle } from "lucide-react";
import PageHeader from "@/components/PageHeader";
import { Card, Spinner, Segment } from "@/components/ui";
import { useApi } from "@/lib/hooks";
import { api } from "@/lib/api";
import { titleCase } from "@/lib/format";

const REGISTRATION_LABELS: Record<string, string> = {
  individual: "Individual", joint_jtwros: "Joint JTWROS", joint_tic: "Joint TIC",
  traditional_ira: "Traditional IRA", roth_ira: "Roth IRA",
  employer_rollover: "Employer Rollover", trust: "Trust",
  entity_llc: "LLC", entity_corp: "Corp", entity_partnership: "Partnership",
  custodial_utma: "Custodial UTMA", custodial_ugma: "Custodial UGMA",
  estate_inherited: "Estate/Inherited",
};

const AML_TIER_STYLE: Record<string, string> = {
  low: "bg-positive/10 text-positive",
  medium: "bg-caution/10 text-caution",
  high: "bg-critical/10 text-critical",
};

const STATUS_FLOW = ["intake", "screening", "review", "approved"];
const STATUS_STYLE: Record<string, string> = {
  intake: "bg-navy-100 text-navy-700",
  screening: "bg-gold-soft/40 text-gold-dark",
  review: "bg-gold-soft/40 text-gold-dark",
  approved: "bg-positive/10 text-positive",
  rejected: "bg-navy-50 text-ink-muted",
};

function amlChip(status?: string) {
  if (status === "blocked") return <span className="chip bg-critical/10 text-critical"><ShieldAlert size={11} /> AML blocked</span>;
  if (status === "review") return <span className="chip bg-caution/10 text-caution"><ShieldAlert size={11} /> AML review</span>;
  if (status === "clear") return <span className="chip bg-positive/10 text-positive"><ShieldCheck size={11} /> AML clear</span>;
  return null;
}

export default function OnboardingPipeline() {
  const { data, loading, refetch } = useApi<any[]>("/api/onboarding/cases");
  const [showNew, setShowNew] = useState(false);

  return (
    <div>
      <PageHeader
        title="Onboarding"
        sub="Intake → document checks → AML/CFT screening → suitability & mandate → compliance sign-off."
        actions={<button className="btn-gold" onClick={() => setShowNew(true)}><UserPlus size={16} /> New prospect</button>}
      />

      {showNew && <NewProspect onClose={() => setShowNew(false)} onCreated={() => { setShowNew(false); refetch(); }} />}

      {loading ? (
        <Spinner />
      ) : (
        <div className="grid lg:grid-cols-4 gap-4">
          {STATUS_FLOW.map((status) => {
            const cases = (data || []).filter((c) => c.status === status);
            return (
              <div key={status}>
                <div className="flex items-center justify-between mb-2 px-1">
                  <span className="text-xs font-semibold uppercase tracking-wide text-ink-muted">{titleCase(status)}</span>
                  <span className="text-xs text-ink-muted">{cases.length}</span>
                </div>
                <div className="space-y-2">
                  {cases.map((c) => (
                    <Link key={c.id} href={`/studio/onboarding/${c.id}`} className="card p-3 block hover:shadow-lift transition group">
                      <div className="flex items-center justify-between">
                        <span className="font-medium text-ink text-sm">{c.prospect_name}</span>
                        <ChevronRight size={15} className="text-navy-300 group-hover:text-navy-600" />
                      </div>
                      <div className="flex items-center gap-1.5 mt-2 flex-wrap">
                        <Segment>{c.segment}</Segment>
                        {c.registration_type ? (
                          <span className="chip bg-navy-50 text-ink-muted">{REGISTRATION_LABELS[c.registration_type] || titleCase(c.registration_type)}</span>
                        ) : c.is_entity ? (
                          <span className="chip bg-navy-50 text-ink-muted">{titleCase(c.entity_type || "entity")}</span>
                        ) : null}
                      </div>
                      <div className="flex items-center gap-1.5 mt-2 flex-wrap">
                        {amlChip(c.screening?.status)}
                        {c.aml_risk_tier && (
                          <span className={`chip ${AML_TIER_STYLE[c.aml_risk_tier] || "bg-navy-50 text-ink-muted"}`}>AML {c.aml_risk_tier}</span>
                        )}
                        {c.nigo_flag && <span className="chip bg-caution/10 text-caution flex items-center gap-1"><AlertTriangle size={10} /> NIGO</span>}
                        {c.exceptions?.length > 0 && (
                          <span className="chip bg-caution/10 text-caution">{c.exceptions.length} exception</span>
                        )}
                      </div>
                    </Link>
                  ))}
                  {cases.length === 0 && <div className="text-xs text-ink-muted px-1 py-3">—</div>}
                </div>
              </div>
            );
          })}
        </div>
      )}
    </div>
  );
}

const REGISTRATION_OPTIONS = [
  { value: "individual", label: "Individual" },
  { value: "joint_jtwros", label: "Joint — JTWROS" },
  { value: "joint_tic", label: "Joint — Tenants in Common" },
  { value: "traditional_ira", label: "Traditional IRA" },
  { value: "roth_ira", label: "Roth IRA" },
  { value: "employer_rollover", label: "Employer Rollover (401k/403b)" },
  { value: "trust", label: "Trust" },
  { value: "entity_llc", label: "Entity — LLC" },
  { value: "entity_corp", label: "Entity — Corporation" },
  { value: "entity_partnership", label: "Entity — Partnership" },
  { value: "custodial_utma", label: "Custodial — UTMA" },
  { value: "custodial_ugma", label: "Custodial — UGMA" },
  { value: "estate_inherited", label: "Estate / Inherited" },
];

function NewProspect({ onClose, onCreated }: { onClose: () => void; onCreated: () => void }) {
  const [form, setForm] = useState({
    prospect_name: "", registration_type: "individual", segment: "private_wealth",
    risk_profile: "balanced", mandate_preference: "advisory", source_of_wealth: "",
  });
  const [busy, setBusy] = useState(false);

  const isRollover = ["employer_rollover", "traditional_ira", "roth_ira"].includes(form.registration_type);

  async function create() {
    if (!form.prospect_name) return;
    setBusy(true);
    try {
      await api("/api/onboarding/cases", {
        body: {
          prospect_name: form.prospect_name,
          registration_type: form.registration_type,
          segment: form.segment,
          intake: {
            risk_profile: form.risk_profile, mandate_preference: form.mandate_preference,
            source_of_wealth: form.source_of_wealth, objectives: ["growth"],
            time_horizon_years: 15, associated_parties: [],
          },
        },
      });
      onCreated();
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="fixed inset-0 bg-ink/30 z-50 flex items-center justify-center p-4" onClick={onClose}>
      <div className="card p-6 w-full max-w-lg" onClick={(e) => e.stopPropagation()}>
        <h3 className="text-lg font-semibold text-ink mb-4">New prospect</h3>
        <div className="space-y-3">
          <div><label className="label">Name</label>
            <input className="input" value={form.prospect_name} onChange={(e) => setForm({ ...form, prospect_name: e.target.value })} placeholder="e.g. Daniel Okonkwo or Sokolov Family Trust" />
          </div>
          <div className="grid grid-cols-2 gap-3">
            <div><label className="label">Registration type</label>
              <select className="input" value={form.registration_type} onChange={(e) => setForm({ ...form, registration_type: e.target.value })}>
                {REGISTRATION_OPTIONS.map((o) => <option key={o.value} value={o.value}>{o.label}</option>)}
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
          <div className="grid grid-cols-2 gap-3">
            <div><label className="label">Risk profile</label>
              <select className="input" value={form.risk_profile} onChange={(e) => setForm({ ...form, risk_profile: e.target.value })}>
                <option value="conservative">Conservative</option>
                <option value="balanced">Balanced</option>
                <option value="growth">Growth</option>
              </select>
            </div>
            <div><label className="label">Mandate</label>
              <select className="input" value={form.mandate_preference} onChange={(e) => setForm({ ...form, mandate_preference: e.target.value })}>
                <option value="advisory">Advisory</option>
                <option value="discretionary">Discretionary</option>
              </select>
            </div>
          </div>
          <div><label className="label">Source of wealth{isRollover ? " (required for PTE 2020-02)" : ""}</label>
            <input className="input" value={form.source_of_wealth} onChange={(e) => setForm({ ...form, source_of_wealth: e.target.value })} placeholder="e.g. Business sale proceeds" />
          </div>
          {isRollover && (
            <div className="rounded-lg bg-caution/8 border border-caution/20 px-3 py-2 text-xs text-caution">
              Rollover account — DOL PTE 2020-02 rationale memo will be required. Run the Rollover PTE Documenter agent after creation.
            </div>
          )}
          <div className="text-xs text-ink-muted flex items-center gap-1"><FileText size={12} /> Add identity documents on the next screen, then run the agent.</div>
        </div>
        <div className="flex justify-end gap-2 mt-5">
          <button className="btn-ghost" onClick={onClose}>Cancel</button>
          <button className="btn-primary" disabled={busy} onClick={create}>{busy ? "Creating…" : "Create case"}</button>
        </div>
      </div>
    </div>
  );
}
