"use client";
import { useState } from "react";
import { useParams } from "next/navigation";
import Link from "next/link";
import { FileText, ShieldCheck, ShieldAlert, Play, Check, X, Plus, ArrowRight, CheckCircle2, Users, AlertTriangle, Activity, ClipboardList } from "lucide-react";
import PageHeader from "@/components/PageHeader";
import { Card, Spinner, Segment, ConfidenceBar, SeverityBadge } from "@/components/ui";
import { useApi } from "@/lib/hooks";
import { api } from "@/lib/api";
import { titleCase, money } from "@/lib/format";

const REGISTRATION_LABELS: Record<string, string> = {
  individual: "Individual", joint_jtwros: "Joint JTWROS", joint_tic: "Joint TIC",
  traditional_ira: "Traditional IRA", roth_ira: "Roth IRA",
  employer_rollover: "Employer Rollover", trust: "Trust",
  entity_llc: "LLC", entity_corp: "Corp", entity_partnership: "Partnership",
  custodial_utma: "Custodial UTMA", custodial_ugma: "Custodial UGMA",
  estate_inherited: "Estate/Inherited",
};

const NIGO_LABELS: Record<string, string> = {
  missing_signature: "Missing signature", title_mismatch: "Title mismatch",
  stale_form: "Stale form", wrong_tenancy: "Wrong tenancy",
  missing_beneficiary: "Missing beneficiary", missing_id: "Missing ID",
  beneficial_owner_incomplete: "BO incomplete",
};

const AML_TIER_STYLE: Record<string, string> = {
  low: "bg-positive/10 text-positive border-positive/20",
  medium: "bg-caution/10 text-caution border-caution/20",
  high: "bg-critical/10 text-critical border-critical/20",
};

const EDD_LABELS: Record<string, { label: string; cls: string }> = {
  none: { label: "No CDD/EDD required", cls: "text-ink-muted" },
  cdd: { label: "CDD — standard", cls: "text-positive" },
  edd_pending: { label: "EDD — pending review", cls: "text-caution" },
  edd_complete: { label: "EDD — complete", cls: "text-positive" },
};

export default function OnboardingCase() {
  const { id } = useParams<{ id: string }>();
  const { data: c, loading, refetch } = useApi<any>(`/api/onboarding/cases/${id}`, [id]);
  const [busy, setBusy] = useState<string | null>(null);
  const [err, setErr] = useState<string | null>(null);

  if (loading || !c) return <Spinner />;

  async function addDoc(doc_type: string) {
    setBusy("doc");
    try { await api(`/api/onboarding/cases/${id}/documents`, { body: { doc_type } }); await refetch(); }
    finally { setBusy(null); }
  }
  async function run() {
    setBusy("run"); setErr(null);
    try { await api(`/api/onboarding/cases/${id}/run`, { body: {} }); await refetch(); }
    catch (e: any) { setErr(e.message); } finally { setBusy(null); }
  }
  async function decide(action: string, note?: string) {
    setBusy(action); setErr(null);
    try { await api(`/api/studio/recommendations/${c.recommendation_id}/decide`, { body: { action, note: note || null } }); await refetch(); }
    catch (e: any) { setErr(e.message); } finally { setBusy(null); }
  }

  const screening = c.screening || {};
  const proposal = c.proposal || {};
  const allHits = (screening.parties || []).flatMap((p: any) => p.hits.map((h: any) => ({ ...h, party: p.subject })));
  const canDecide = c.recommendation_id && c.status === "review";

  return (
    <div>
      <PageHeader
        title={c.prospect_name}
        sub={`${titleCase(c.segment)} · ${c.registration_type ? (REGISTRATION_LABELS[c.registration_type] || titleCase(c.registration_type)) : (c.is_entity ? titleCase(c.entity_type || "entity") : "Individual")} · status ${titleCase(c.status)}`}
        actions={
          c.status !== "approved" ? (
            <button className="btn-primary" disabled={busy === "run"} onClick={run}>
              <Play size={16} /> {busy === "run" ? "Running…" : "Run onboarding agent"}
            </button>
          ) : (
            c.materialized?.household_id && (
              <Link href={`/studio/clients/${c.materialized.household_id}`} className="btn-gold">
                View client <ArrowRight size={15} />
              </Link>
            )
          )
        }
      />

      {err && <div className="card p-3 mb-4 text-sm text-critical border-critical/30">{err}</div>}

      {c.status === "approved" && (
        <div className="card p-4 mb-5 border-positive/30 flex items-center gap-3">
          <CheckCircle2 className="text-positive" />
          <div className="text-sm">
            <span className="font-semibold text-ink">Materialised into the client brain as golden records.</span>
            <span className="text-ink-muted"> Person/entity, mandate and account created, KYC verified.</span>
          </div>
        </div>
      )}

      {/* Readiness / NIGO banner */}
      {(c.nigo_flag || c.readiness_score != null) && (
        <div className={`card p-3 mb-4 flex items-center gap-3 ${c.nigo_flag ? "border-caution/30" : "border-positive/20"}`}>
          {c.nigo_flag ? <AlertTriangle className="text-caution flex-shrink-0" size={18} /> : <CheckCircle2 className="text-positive flex-shrink-0" size={18} />}
          <div className="flex-1 min-w-0">
            <div className="text-sm font-semibold text-ink">
              Pre-submission readiness: {c.readiness_score ?? "—"}/100
              {c.nigo_flag && <span className="ml-2 chip bg-caution/10 text-caution">NIGO</span>}
              {c.nigo_root_cause && <span className="ml-1 chip bg-navy-50 text-ink-muted">{NIGO_LABELS[c.nigo_root_cause] || titleCase(c.nigo_root_cause)}</span>}
            </div>
            {c.nigo_reason && <div className="text-xs text-ink-muted mt-0.5 truncate">{c.nigo_reason}</div>}
          </div>
        </div>
      )}

      <div className="grid lg:grid-cols-3 gap-5">
        <div className="lg:col-span-2 space-y-5">
          {/* Documents */}
          <Card>
            <div className="flex items-center justify-between mb-3">
              <div className="font-semibold text-ink flex items-center gap-2"><FileText size={17} /> Documents · intelligence-extracted</div>
              <DocAdder onAdd={addDoc} busy={busy === "doc"} />
            </div>
            {c.documents?.length ? (
              <div className="space-y-3">
                {c.documents.map((d: any) => (
                  <div key={d.id} className="rounded-xl border border-navy-100 p-3">
                    <div className="flex items-center justify-between">
                      <div className="text-sm font-medium text-ink">{titleCase(d.doc_type)}</div>
                      <div className="flex items-center gap-2">
                        {d.verified ? <span className="chip bg-positive/10 text-positive">Verified</span> : <span className="chip bg-caution/10 text-caution">Needs check</span>}
                        <ConfidenceBar value={d.confidence} />
                      </div>
                    </div>
                    {Object.keys(d.extracted || {}).length > 0 && (
                      <div className="grid grid-cols-2 gap-x-4 gap-y-1 mt-2 text-xs">
                        {Object.entries(d.extracted).map(([k, v]: any) => (
                          <div key={k} className="flex justify-between gap-2">
                            <span className="text-ink-muted">{titleCase(k)}</span>
                            <span className="text-ink-soft text-right">{Array.isArray(v) ? v.join(", ") : String(v)}
                              <span className="text-ink-muted ml-1">({Math.round((d.field_confidence?.[k] || 0) * 100)}%)</span>
                            </span>
                          </div>
                        ))}
                      </div>
                    )}
                  </div>
                ))}
              </div>
            ) : (
              <div className="text-sm text-ink-muted">No documents yet — add identity/trust documents, then run the agent.</div>
            )}
          </Card>

          {/* IPS Proposal */}
          {proposal.mandate && (
            <Card>
              <div className="font-semibold text-ink mb-1">Investment Policy Statement (IPS) — setup proposal</div>
              <div className="text-[11px] text-ink-muted mb-4">
                Structured using the RRTTLLU framework — Return · Risk · Time horizon · Tax · Liquidity · Legal · Unique preferences
              </div>
              <div className="grid sm:grid-cols-2 gap-4 text-sm">
                <div>
                  <div className="tile-label mb-1">R · R · T — Return, Risk & Time horizon</div>
                  <Row k="Risk tolerance (psychological)" v={titleCase(proposal.suitability?.risk_profile || "—")} />
                  <Row k="Risk capacity (financial)" v={titleCase(proposal.suitability?.capacity_for_loss || "—")} />
                  <Row k="Time horizon" v={proposal.suitability?.time_horizon_years ? `${proposal.suitability.time_horizon_years}y` : "—"} />
                </div>
                <div>
                  <div className="tile-label mb-1">Mandate (operational constraints)</div>
                  <Row k="Mandate type" v={titleCase(proposal.mandate?.mandate_type || "—")} />
                  <Row k="Model portfolio" v={proposal.mandate?.model_portfolio || "—"} />
                  <Row k="CGT budget" v={money(proposal.mandate?.constraints?.cgt_budget)} />
                  <div className="mt-2 text-[10px] text-ink-faint">The mandate is a sub-component of the full IPS — generated by the IPS Drafter agent on completion of full discovery.</div>
                </div>
              </div>
            </Card>
          )}

          {/* PTE 2020-02 memo (rollover accounts) */}
          {proposal.pte_2020_02_memo && (
            <Card>
              <div className="font-semibold text-ink mb-2 flex items-center gap-2">
                <ClipboardList size={16} className="text-navy-400" />
                DOL PTE 2020-02 — Rollover rationale memo
                <span className="chip bg-positive/10 text-positive ml-auto">Generated</span>
              </div>
              {proposal.pte_fee_comparison && (
                <div className="flex gap-6 text-xs mb-3">
                  <div><div className="text-ink-muted">Leaving plan (bps)</div><div className="font-semibold text-ink">{proposal.pte_fee_comparison.plan_expense_ratio_bps ?? "—"}</div></div>
                  <div><div className="text-ink-muted">Proposed IRA advisory (bps)</div><div className="font-semibold text-ink">{proposal.pte_fee_comparison.advisory_fee_bps ?? "—"}</div></div>
                  <div><div className="text-ink-muted">Net change</div><div className={`font-semibold ${(proposal.pte_fee_comparison.net_difference_bps ?? 0) <= 0 ? "text-positive" : "text-caution"}`}>{(proposal.pte_fee_comparison.net_difference_bps ?? 0) > 0 ? "+" : ""}{proposal.pte_fee_comparison.net_difference_bps ?? "—"} bps</div></div>
                </div>
              )}
              <p className="text-xs text-ink-muted line-clamp-4">{proposal.pte_2020_02_memo}</p>
            </Card>
          )}

          {/* EDD memo */}
          {proposal.edd_memo && (
            <Card>
              <div className="font-semibold text-ink mb-2 flex items-center gap-2">
                <Activity size={16} className="text-caution" />
                EDD Source-of-Wealth memorandum
                <span className="chip bg-caution/10 text-caution ml-auto">{titleCase(c.edd_status || "edd_pending").replace("_", " ")}</span>
              </div>
              {proposal.edd_gaps?.length > 0 && (
                <div className="mb-2">
                  <div className="text-xs font-medium text-ink mb-1">Corroboration gaps ({proposal.edd_gaps.length})</div>
                  <ul className="space-y-0.5">
                    {proposal.edd_gaps.map((g: string, i: number) => (
                      <li key={i} className="text-xs text-caution flex gap-1.5"><span>•</span>{g}</li>
                    ))}
                  </ul>
                </div>
              )}
              <p className="text-xs text-ink-muted line-clamp-4">{proposal.edd_memo}</p>
            </Card>
          )}

          {/* Beneficial owners (entity accounts) */}
          <BeneficialOwners caseId={id} registrationType={c.registration_type} status={c.status} />
        </div>

        {/* Right column */}
        <div className="space-y-5">
          {/* AML risk tier */}
          {c.aml_risk_tier && (
            <Card className={`border ${AML_TIER_STYLE[c.aml_risk_tier] || ""}`}>
              <div className="font-semibold text-ink mb-2 flex items-center gap-2">
                <Activity size={16} /> AML customer risk rating
              </div>
              <div className="flex items-center gap-3">
                <span className={`text-lg font-bold uppercase tracking-wide ${c.aml_risk_tier === "high" ? "text-critical" : c.aml_risk_tier === "medium" ? "text-caution" : "text-positive"}`}>
                  {c.aml_risk_tier}
                </span>
                {c.aml_risk_score != null && <span className="text-xs text-ink-muted">Score: {c.aml_risk_score}/100</span>}
              </div>
              {c.edd_status && (
                <div className={`mt-2 text-xs font-medium ${EDD_LABELS[c.edd_status]?.cls || "text-ink-muted"}`}>
                  {EDD_LABELS[c.edd_status]?.label || titleCase(c.edd_status)}
                </div>
              )}
            </Card>
          )}

          {/* AML / CFT screening — disposition log */}
          <Card>
            <div className="font-semibold text-ink mb-3 flex items-center gap-2">
              {screening.status === "clear" ? <ShieldCheck size={17} className="text-positive" /> : <ShieldAlert size={17} className="text-caution" />}
              AML / CFT screening
            </div>
            {screening.status ? (
              <>
                <div className="text-sm mb-2">
                  Status: <span className={screening.status === "clear" ? "text-positive font-medium" : screening.status === "blocked" ? "text-critical font-medium" : "text-caution font-medium"}>{titleCase(screening.status)}</span>
                  <span className="text-ink-muted"> · {screening.provider}</span>
                </div>
                {allHits.length ? (
                  <div className="space-y-2">
                    {allHits.map((h: any, i: number) => (
                      <div key={i} className="rounded-lg border border-navy-100 p-2.5">
                        <div className="flex items-center justify-between">
                          <span className="text-sm font-medium text-ink">{h.matched_name}</span>
                          <SeverityBadge severity={h.severity} />
                        </div>
                        <div className="text-xs text-ink-muted mt-0.5">{titleCase(h.category)} · {h.country} · match {Math.round(h.score * 100)}%</div>
                        <div className="text-xs text-ink-soft mt-1">{h.note}</div>
                      </div>
                    ))}
                  </div>
                ) : (
                  <div className="text-sm text-positive">No watchlist hits.</div>
                )}
                {/* Disposition log (from Adverse Media & PEP Screener agent) */}
                {c.screening_log?.length > 0 && (
                  <div className="mt-3 pt-3 border-t border-navy-100">
                    <div className="text-xs font-semibold text-ink-muted mb-2 uppercase tracking-wide">Per-party disposition log</div>
                    <div className="space-y-2">
                      {c.screening_log.map((entry: any, i: number) => (
                        <div key={i} className="rounded-lg bg-navy-25 p-2 text-xs">
                          <div className="flex items-center justify-between">
                            <span className="font-medium text-ink">{entry.party}</span>
                            <span className={entry.status === "clear" ? "text-positive" : entry.status === "blocked" ? "text-critical" : "text-caution"}>{titleCase(entry.status)}</span>
                          </div>
                          {entry.disposition_note && <div className="text-ink-muted mt-0.5">{entry.disposition_note}</div>}
                          {entry.screened_at && <div className="text-ink-faint mt-0.5">{new Date(entry.screened_at).toLocaleDateString()}</div>}
                        </div>
                      ))}
                    </div>
                  </div>
                )}
              </>
            ) : (
              <div className="text-sm text-ink-muted">Run the agent to screen the applicant and associated parties.</div>
            )}
          </Card>

          {/* Exceptions */}
          {c.exceptions?.length > 0 && (
            <Card className="border-caution/30">
              <div className="font-semibold text-ink mb-2">Exceptions for compliance</div>
              <ul className="space-y-1.5">
                {c.exceptions.map((e: string, i: number) => (
                  <li key={i} className="text-sm text-ink-soft flex gap-2"><span className="text-caution">⚠</span> {e}</li>
                ))}
              </ul>
            </Card>
          )}

          {/* Disclosure delivery */}
          <DisclosureTracker caseId={id} status={c.status} />

          {/* Decision */}
          {canDecide && (
            <Card>
              <div className="font-semibold text-ink mb-1">Compliance decision</div>
              <p className="text-xs text-ink-muted mb-3">Tier 2 — sign off to materialise the client, or escalate / dismiss.</p>
              <div className="flex flex-col gap-2">
                <button className="btn-gold" disabled={!!busy} onClick={() => decide("approve")}><Check size={15} /> Approve & materialise</button>
                <button className="btn-outline" disabled={!!busy} onClick={() => decide("dismiss", "Escalated / not proceeding")}><X size={15} /> Dismiss</button>
              </div>
            </Card>
          )}
        </div>
      </div>
    </div>
  );
}

function Row({ k, v }: { k: string; v: any }) {
  return <div className="flex justify-between gap-2 py-0.5"><span className="text-ink-muted">{k}</span><span className="text-ink-soft font-medium">{v}</span></div>;
}

const BO_ENTITY_TYPES = new Set(["trust", "entity_llc", "entity_corp", "entity_partnership"]);

function BeneficialOwners({ caseId, registrationType, status }: { caseId: string; registrationType?: string; status: string }) {
  const isEntity = registrationType && BO_ENTITY_TYPES.has(registrationType);
  const { data: bos, refetch } = useApi<any[]>(`/api/onboarding/cases/${caseId}/beneficial-owners`, [caseId]);
  const [form, setForm] = useState({ legal_name: "", ownership_pct: "", is_control_person: false });
  const [busy, setBusy] = useState(false);
  const isClosed = status === "approved" || status === "rejected";

  if (!isEntity) return null;

  const totalPct = (bos || []).reduce((s: number, b: any) => s + (b.ownership_pct || 0), 0);
  const hasControl = (bos || []).some((b: any) => b.is_control_person);

  async function addBO() {
    if (!form.legal_name) return;
    setBusy(true);
    try {
      await api(`/api/onboarding/cases/${caseId}/beneficial-owners`, {
        body: { legal_name: form.legal_name, ownership_pct: parseFloat(form.ownership_pct) || null, is_control_person: form.is_control_person },
      });
      setForm({ legal_name: "", ownership_pct: "", is_control_person: false });
      refetch();
    } finally { setBusy(false); }
  }

  async function removeBO(boId: string) {
    await api(`/api/onboarding/cases/${caseId}/beneficial-owners/${boId}`, { method: "DELETE" });
    refetch();
  }

  return (
    <Card>
      <div className="font-semibold text-ink mb-2 flex items-center gap-2">
        <Users size={16} className="text-navy-400" /> Beneficial owners
        <span className="text-[10px] text-ink-muted font-normal ml-1">CDD Rule — 25%+ owners + control person</span>
        {totalPct > 0 && (
          <span className={`chip ml-auto ${Math.abs(totalPct - 100) < 1 ? "bg-positive/10 text-positive" : "bg-caution/10 text-caution"}`}>
            {totalPct.toFixed(0)}% total
          </span>
        )}
      </div>
      {!hasControl && (bos?.length ?? 0) > 0 && (
        <div className="text-xs text-caution mb-2 flex items-center gap-1"><AlertTriangle size={11} /> No control person designated yet.</div>
      )}
      <div className="space-y-2 mb-3">
        {(bos || []).map((b: any) => (
          <div key={b.id} className="rounded-lg border border-navy-100 p-2.5 flex items-start justify-between gap-2">
            <div>
              <div className="text-sm font-medium text-ink">{b.legal_name}</div>
              <div className="text-xs text-ink-muted mt-0.5">
                {b.ownership_pct != null ? `${b.ownership_pct}% ownership` : "Ownership % not set"}
                {b.is_control_person && <span className="ml-2 chip bg-navy-50 text-ink-muted">Control person</span>}
              </div>
            </div>
            {!isClosed && (
              <button className="text-ink-faint hover:text-critical text-xs" onClick={() => removeBO(b.id)}>×</button>
            )}
          </div>
        ))}
        {(bos?.length === 0) && <div className="text-xs text-ink-muted">No beneficial owners added yet.</div>}
      </div>
      {!isClosed && (
        <div className="flex gap-2 items-end">
          <div className="flex-1"><label className="label text-[10px]">Name</label>
            <input className="input text-xs" value={form.legal_name} onChange={(e) => setForm({ ...form, legal_name: e.target.value })} placeholder="Full legal name" />
          </div>
          <div className="w-20"><label className="label text-[10px]">Own%</label>
            <input className="input text-xs" value={form.ownership_pct} onChange={(e) => setForm({ ...form, ownership_pct: e.target.value })} placeholder="25" />
          </div>
          <div className="flex items-center gap-1 pb-1">
            <input type="checkbox" id="ctrl" className="h-3 w-3" checked={form.is_control_person} onChange={(e) => setForm({ ...form, is_control_person: e.target.checked })} />
            <label htmlFor="ctrl" className="text-[10px] text-ink-muted whitespace-nowrap">Control</label>
          </div>
          <button className="btn-outline text-xs h-8" disabled={busy} onClick={addBO}><Plus size={12} /></button>
        </div>
      )}
    </Card>
  );
}

const DISCLOSURE_TYPES = [
  { key: "form_adv", label: "Form ADV (Part 2A/2B)", regime: "US SEC" },
  { key: "form_crs", label: "Form CRS (Client Relationship Summary)", regime: "US SEC" },
  { key: "soa", label: "Statement of Advice (SOA)", regime: "AU/NZ" },
  { key: "kid", label: "Key Information Document (KID)", regime: "UK/EU" },
];

function DisclosureTracker({ caseId, status }: { caseId: string; status: string }) {
  const [delivered, setDelivered] = useState<Record<string, boolean>>({});
  const isClosed = status === "approved" || status === "rejected";
  const deliveredCount = Object.values(delivered).filter(Boolean).length;
  return (
    <Card>
      <div className="font-semibold text-ink mb-1 flex items-center gap-2">
        <FileText size={16} className="text-navy-400" /> Disclosure delivery
      </div>
      <p className="text-xs text-ink-muted mb-3">
        Track required disclosure documents delivered to the prospect at onboarding. Regulators expect evidence of delivery.
      </p>
      <div className="space-y-2">
        {DISCLOSURE_TYPES.map((d) => (
          <label key={d.key} className="flex items-center gap-2.5 cursor-pointer group">
            <input
              type="checkbox"
              className="h-3.5 w-3.5 rounded accent-navy-700"
              checked={!!delivered[d.key]}
              disabled={isClosed}
              onChange={(e) => setDelivered((prev) => ({ ...prev, [d.key]: e.target.checked }))}
            />
            <span className="text-sm text-ink-soft group-hover:text-ink transition">{d.label}</span>
            <span className="text-[10px] text-ink-faint ml-auto">{d.regime}</span>
          </label>
        ))}
      </div>
      {deliveredCount > 0 && (
        <div className="mt-2 text-[11px] text-positive flex items-center gap-1">
          <CheckCircle2 size={11} /> {deliveredCount} disclosure{deliveredCount !== 1 ? "s" : ""} delivered
        </div>
      )}
    </Card>
  );
}

function DocAdder({ onAdd, busy }: { onAdd: (t: string) => void; busy: boolean }) {
  const { data } = useApi<any[]>("/api/onboarding/document-templates");
  const [open, setOpen] = useState(false);
  return (
    <div className="relative">
      <button className="btn-outline text-xs" onClick={() => setOpen(!open)} disabled={busy}>
        <Plus size={13} /> {busy ? "Adding…" : "Add document"}
      </button>
      {open && (
        <div className="absolute right-0 mt-1 card p-1.5 z-10 w-52">
          {data?.map((t) => (
            <button key={t.doc_type} className="w-full text-left text-sm px-2.5 py-1.5 rounded-lg hover:bg-navy-50" onClick={() => { setOpen(false); onAdd(t.doc_type); }}>
              {t.label}
            </button>
          ))}
        </div>
      )}
    </div>
  );
}
