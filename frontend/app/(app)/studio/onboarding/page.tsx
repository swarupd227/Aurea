"use client";
import { useState } from "react";
import Link from "next/link";
import { UserPlus, ShieldCheck, ShieldAlert, ChevronRight, FileText } from "lucide-react";
import PageHeader from "@/components/PageHeader";
import { Card, Spinner, Segment } from "@/components/ui";
import { useApi } from "@/lib/hooks";
import { api } from "@/lib/api";
import { titleCase } from "@/lib/format";

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
                        {c.is_entity && <span className="chip bg-navy-50 text-ink-muted">{titleCase(c.entity_type || "entity")}</span>}
                      </div>
                      <div className="flex items-center gap-1.5 mt-2 flex-wrap">
                        {amlChip(c.screening?.status)}
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

function NewProspect({ onClose, onCreated }: { onClose: () => void; onCreated: () => void }) {
  const [form, setForm] = useState({
    prospect_name: "", is_entity: false, entity_type: "trust", segment: "private_wealth",
    risk_profile: "balanced", mandate_preference: "advisory", source_of_wealth: "",
  });
  const [busy, setBusy] = useState(false);

  async function create() {
    if (!form.prospect_name) return;
    setBusy(true);
    try {
      await api("/api/onboarding/cases", {
        body: {
          prospect_name: form.prospect_name, is_entity: form.is_entity,
          entity_type: form.is_entity ? form.entity_type : null, segment: form.segment,
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
            <div><label className="label">Type</label>
              <select className="input" value={form.is_entity ? "entity" : "individual"} onChange={(e) => setForm({ ...form, is_entity: e.target.value === "entity" })}>
                <option value="individual">Individual</option>
                <option value="entity">Entity / Trust</option>
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
          <div><label className="label">Source of wealth</label>
            <input className="input" value={form.source_of_wealth} onChange={(e) => setForm({ ...form, source_of_wealth: e.target.value })} placeholder="e.g. Business sale proceeds" />
          </div>
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
