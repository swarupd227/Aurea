"use client";
import { useState } from "react";
import { useParams } from "next/navigation";
import Link from "next/link";
import { FileText, ShieldCheck, ShieldAlert, Play, Check, X, Plus, ArrowRight, CheckCircle2, Users, AlertTriangle, Activity, ClipboardList, Fingerprint, Building2, ArrowDownUp, ChevronRight } from "lucide-react";
import PageHeader from "@/components/PageHeader";
import { Card, Spinner, Segment, ConfidenceBar, SeverityBadge, ErrorState } from "@/components/ui";
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
  const [pending, setPending] = useState<{ id: string; title: string }[]>([]);

  if (loading || !c) return <Spinner />;

  /**
   * Run an onboarding-stage agent against this case.
   *
   * These agents are Tier 1/2, so they stop at a human checkpoint — `act()` does not run
   * and the case is unchanged on return. Previously the caller refetched immediately and
   * re-rendered identical state, so the button looked broken. We now surface the pending
   * recommendation the run produced so there is something to approve.
   */
  async function runCaseAgent(slug: string) {
    setBusy(slug); setErr(null);
    try {
      const res = await api<any>(`/api/onboarding/cases/${id}/run-agent/${slug}`, { body: {} });
      const awaiting: { id: string; title: string }[] = res?.awaiting_approval || [];
      if (awaiting.length) {
        setPending((prev) => [...awaiting.filter((a) => !prev.some((p) => p.id === a.id)), ...prev]);
      }
      await refetch();
    } catch (e: any) { setErr(e.message); } finally { setBusy(null); }
  }

  async function approvePending(recId: string) {
    setBusy(recId); setErr(null);
    try {
      await api(`/api/studio/recommendations/${recId}/decide`, { body: { action: "approve" } });
      setPending((prev) => prev.filter((p) => p.id !== recId));
      await refetch();
    } catch (e: any) { setErr(e.message); } finally { setBusy(null); }
  }

  const runCip = () => runCaseAgent("cip_identity_verifier");
  const pushToCustodian = () => runCaseAgent("custodian_account_opener");
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

      {err && <div className="card p-3 mb-4 text-sm text-critical border-critical/30" role="alert">{err}</div>}

      {/* Tier 1/2 agents stop at a checkpoint. Without this the run looks like it did nothing. */}
      {pending.length > 0 && (
        <div className="card p-4 mb-4 border-gold/40 bg-gold-soft/10">
          <div className="flex items-center gap-2 mb-2">
            <ClipboardList size={16} className="text-gold-dark" />
            <span className="text-sm font-semibold text-ink">
              {pending.length} agent {pending.length === 1 ? "result" : "results"} awaiting your approval
            </span>
          </div>
          <p className="text-xs text-ink-muted mb-3">
            These agents run at Tier&nbsp;2 — they propose, you decide. Nothing is written to the
            case until you approve.
          </p>
          <div className="space-y-2">
            {pending.map((p) => (
              <div key={p.id} className="flex items-center gap-3 rounded-lg border border-navy-100 bg-surface px-3 py-2">
                <span className="text-sm text-ink-soft flex-1 min-w-0 truncate">{p.title}</span>
                <button
                  className="btn-gold text-xs shrink-0"
                  disabled={busy === p.id}
                  onClick={() => approvePending(p.id)}
                >
                  <Check size={13} /> {busy === p.id ? "Applying…" : "Approve"}
                </button>
              </div>
            ))}
          </div>
        </div>
      )}

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

          {/* Onboarding-stage agents */}
          <CaseAgents
            c={c}
            busy={busy}
            onRun={runCaseAgent}
          />

          {/* Beneficial owners (entity accounts) */}
          <BeneficialOwners caseId={id} registrationType={c.registration_type} status={c.status} />

          {/* Transfer tracking */}
          <TransfersPanel caseId={id} />
        </div>

        {/* Right column */}
        <div className="space-y-5">
          {/* CIP identity verification */}
          <CipCard cip={c} onRun={runCip} busy={busy === "cip"} />

          {/* Custodian single-keying */}
          <CustodianCard c={c} onPush={pushToCustodian} busy={busy === "custodian"} />

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
            {screening.status && (
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
              </>
            )}

            {/* Per-party log from the PEP screener. Rendered independently of the
                case-level `screening` blob, which only the main onboarding agent writes —
                nesting it there hid the log whenever the screener ran on its own. */}
            {c.screening_log?.length > 0 && (
              <div className={screening.status ? "mt-3 pt-3 border-t border-navy-100" : ""}>
                <div className="text-xs font-semibold text-ink-muted mb-2 uppercase tracking-wide">
                  Per-party disposition log
                </div>
                <div className="space-y-2">
                  {c.screening_log.map((entry: any, i: number) => (
                    <div key={i} className="rounded-lg bg-navy-25 p-2 text-xs">
                      <div className="flex items-center justify-between">
                        <span className="font-medium text-ink">{entry.party}</span>
                        <span className={entry.status === "clear" ? "text-positive" : entry.status === "blocked" ? "text-critical" : "text-caution"}>{titleCase(entry.status)}</span>
                      </div>
                      {entry.hits?.length > 0 && (
                        <div className="text-caution mt-0.5">
                          {entry.hits.length} {entry.hits.length === 1 ? "hit" : "hits"} · {entry.hits.map((h: any) => titleCase(h.category)).join(", ")}
                        </div>
                      )}
                      {entry.disposition_note && <div className="text-ink-muted mt-0.5">{entry.disposition_note}</div>}
                      {entry.screened_at && <div className="text-ink-faint mt-0.5">{new Date(entry.screened_at).toLocaleDateString()}</div>}
                    </div>
                  ))}
                </div>
              </div>
            )}

            {!screening.status && !c.screening_log?.length && (
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

const ROLLOVER_TYPES = new Set(["employer_rollover", "traditional_ira", "roth_ira"]);

/**
 * The onboarding-stage agents, runnable per case.
 *
 * These exist in the agent registry but had no endpoint and no trigger, so the fields they
 * populate — readiness_score, screening_log, aml_risk_tier, edd_status, pte_status — were
 * null on every case and the panels that render them never appeared.
 */
function CaseAgents({
  c, busy, onRun,
}: {
  c: any;
  busy: string | null;
  onRun: (slug: string) => void;
}) {
  const isClosed = c.status === "approved" || c.status === "rejected";

  const agents = [
    {
      slug: "nigo_prevention",
      name: "NIGO Prevention",
      blurb: "Pre-submission check — scores readiness and itemises what would cause a reject.",
      done: c.readiness_score != null,
      doneLabel: c.readiness_score != null ? `Readiness ${c.readiness_score}/100` : null,
    },
    {
      slug: "adverse_media_pep",
      name: "Adverse Media & PEP Screener",
      blurb: "Screens every party — applicant, trustees, beneficial owners — with a disposition log.",
      done: (c.screening_log?.length ?? 0) > 0,
      doneLabel: c.screening_log?.length ? `${c.screening_log.length} parties screened` : null,
    },
    {
      slug: "edd_sow_narrator",
      name: "EDD Source-of-Wealth Narrator",
      blurb: "Drafts the source-of-wealth memo required for medium and high-risk customers.",
      done: !!c.proposal?.edd_memo,
      doneLabel: c.proposal?.edd_memo ? "Memo drafted" : null,
      // The agent itself bails unless the tier is medium/high.
      note: !c.aml_risk_tier ? "Needs an AML risk tier first" : null,
    },
    {
      slug: "rollover_pte_documenter",
      name: "Rollover PTE 2020-02 Documenter",
      blurb: "Generates the DOL best-interest rationale memo for rollover registrations.",
      done: !!c.proposal?.pte_2020_02_memo,
      doneLabel: c.pte_status ? titleCase(c.pte_status) : null,
      hidden: !ROLLOVER_TYPES.has(c.registration_type),
    },
  ].filter((a) => !a.hidden);

  return (
    <Card>
      <div className="font-semibold text-ink mb-1 flex items-center gap-2">
        <Activity size={16} className="text-navy-400" /> Onboarding agents
      </div>
      <p className="text-xs text-ink-muted mb-3">
        Each proposes a result for your approval — nothing is written to the case until you accept it.
      </p>
      <div className="space-y-2">
        {agents.map((a) => (
          <div key={a.slug} className="rounded-xl border border-navy-100 p-3">
            <div className="flex items-start justify-between gap-3">
              <div className="min-w-0">
                <div className="text-sm font-medium text-ink flex items-center gap-2 flex-wrap">
                  {a.name}
                  {a.done && a.doneLabel && (
                    <span className="chip bg-positive/10 text-positive">{a.doneLabel}</span>
                  )}
                </div>
                <div className="text-xs text-ink-muted mt-0.5">{a.blurb}</div>
                {a.note && (
                  <div className="text-xs text-caution mt-1 flex items-center gap-1">
                    <AlertTriangle size={10} /> {a.note}
                  </div>
                )}
              </div>
              <button
                className="btn-outline text-xs shrink-0"
                disabled={busy === a.slug || isClosed}
                aria-label={`Run ${a.name}`}
                onClick={() => onRun(a.slug)}
              >
                <Play size={12} />
                {busy === a.slug ? "Running…" : a.done ? "Re-run" : "Run"}
              </button>
            </div>
          </div>
        ))}
      </div>
    </Card>
  );
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

const DELIVERY_METHODS = [
  { value: "email", label: "Email" },
  { value: "portal", label: "Client portal" },
  { value: "in_person", label: "In person" },
  { value: "post", label: "Post" },
];

/**
 * Disclosure delivery — the evidence, not a tick.
 *
 * This previously held its state in `useState` and never called an API, so the ticks were
 * lost on navigation. L200 §5 names this as the control for its first failure mode
 * ("disclosure not delivered/evidenced -> exam deficiency; rescission risk"), so what a
 * regulator needs is *when*, *how* and *by whom* — recorded per document.
 */
function DisclosureTracker({ caseId, status }: { caseId: string; status: string }) {
  const { data, loading, error, refetch } = useApi<any>(`/api/onboarding/cases/${caseId}/disclosures`, [caseId]);
  const [openFor, setOpenFor] = useState<string | null>(null);
  const [form, setForm] = useState({ method: "email", evidence_ref: "" });
  const [busy, setBusy] = useState<string | null>(null);
  const [err, setErr] = useState<string | null>(null);
  const isClosed = status === "approved" || status === "rejected";

  async function record(docType: string) {
    setBusy(docType); setErr(null);
    try {
      await api(`/api/onboarding/cases/${caseId}/disclosures`, {
        body: { doc_type: docType, method: form.method, evidence_ref: form.evidence_ref || null },
      });
      setOpenFor(null);
      setForm({ method: "email", evidence_ref: "" });
      await refetch();
    } catch (e: any) { setErr(e.message); } finally { setBusy(null); }
  }

  async function undo(deliveryId: string) {
    setBusy(deliveryId); setErr(null);
    try {
      await api(`/api/onboarding/cases/${caseId}/disclosures/${deliveryId}`, { method: "DELETE" });
      await refetch();
    } catch (e: any) { setErr(e.message); } finally { setBusy(null); }
  }

  return (
    <Card className={data?.blocks_activation ? "border-caution/30" : undefined}>
      <div className="font-semibold text-ink mb-1 flex items-center gap-2">
        <FileText size={16} className="text-navy-400" /> Disclosure delivery
        {data && (
          <span className={`chip ml-auto ${data.blocks_activation ? "bg-caution/10 text-caution" : "bg-positive/10 text-positive"}`}>
            {data.delivered_count}/{data.required_count}
          </span>
        )}
      </div>
      <p className="text-xs text-ink-muted mb-3">
        Delivery must be evidenced before the account is activated
        {data?.jurisdiction ? ` — ${data.jurisdiction} requirements` : ""}.
      </p>

      {err && <div className="text-xs text-critical bg-critical/5 rounded-lg px-2.5 py-1.5 mb-2" role="alert">{err}</div>}

      {loading ? (
        <Spinner label="Loading disclosures…" />
      ) : error ? (
        <ErrorState what="disclosures" message={error} onRetry={refetch} />
      ) : (
        <div className="space-y-1.5">
          {(data?.items || []).map((d: any) => (
            <div key={d.doc_type} className="rounded-lg border border-navy-100 p-2.5">
              <div className="flex items-start gap-2">
                {d.delivered
                  ? <CheckCircle2 size={14} className="text-positive mt-0.5 shrink-0" aria-hidden="true" />
                  : <AlertTriangle size={14} className="text-caution mt-0.5 shrink-0" aria-hidden="true" />}
                <div className="min-w-0 flex-1">
                  <div className="text-sm text-ink leading-snug">
                    {d.label}
                    {!d.required && <span className="chip bg-navy-50 text-ink-muted ml-1.5 text-[10px]">extra</span>}
                  </div>
                  <div className="text-[10px] text-ink-faint mt-0.5">{d.regime}</div>
                  {d.delivered ? (
                    <div className="text-[11px] text-ink-muted mt-1">
                      {new Date(d.delivered_at).toLocaleDateString()} ·{" "}
                      {DELIVERY_METHODS.find((m) => m.value === d.method)?.label || d.method}
                      {d.delivered_by ? ` · ${d.delivered_by}` : ""}
                      {d.evidence_ref && <div className="font-mono text-ink-faint truncate">Ref: {d.evidence_ref}</div>}
                    </div>
                  ) : (
                    <div className="text-[11px] text-caution mt-0.5">Not yet delivered</div>
                  )}
                </div>
                {!isClosed && (
                  d.delivered ? (
                    <button
                      className="text-[10px] text-ink-faint hover:text-critical shrink-0"
                      aria-label={`Remove delivery record for ${d.label}`}
                      disabled={busy === d.delivery_id}
                      onClick={() => undo(d.delivery_id)}
                    >
                      Undo
                    </button>
                  ) : (
                    <button
                      className="btn-outline text-[10px] py-1 px-2 shrink-0"
                      aria-label={`Record delivery of ${d.label}`}
                      onClick={() => setOpenFor(openFor === d.doc_type ? null : d.doc_type)}
                    >
                      Record
                    </button>
                  )
                )}
              </div>

              {openFor === d.doc_type && (
                <div className="mt-2 pt-2 border-t border-navy-100 space-y-2">
                  <div className="grid grid-cols-2 gap-2">
                    <div>
                      <label className="label text-[10px]" htmlFor={`m-${d.doc_type}`}>Method</label>
                      <select
                        id={`m-${d.doc_type}`}
                        className="input text-xs"
                        value={form.method}
                        onChange={(e) => setForm({ ...form, method: e.target.value })}
                      >
                        {DELIVERY_METHODS.map((m) => <option key={m.value} value={m.value}>{m.label}</option>)}
                      </select>
                    </div>
                    <div>
                      <label className="label text-[10px]" htmlFor={`r-${d.doc_type}`}>Evidence ref</label>
                      <input
                        id={`r-${d.doc_type}`}
                        className="input text-xs"
                        placeholder="envelope / message id"
                        value={form.evidence_ref}
                        onChange={(e) => setForm({ ...form, evidence_ref: e.target.value })}
                      />
                    </div>
                  </div>
                  <div className="flex justify-end gap-2">
                    <button className="btn-ghost text-xs" onClick={() => setOpenFor(null)}>Cancel</button>
                    <button className="btn-primary text-xs" disabled={busy === d.doc_type} onClick={() => record(d.doc_type)}>
                      {busy === d.doc_type ? "Recording…" : "Record delivery"}
                    </button>
                  </div>
                </div>
              )}
            </div>
          ))}
          {data?.blocks_activation && (
            <div className="text-[11px] text-caution flex items-start gap-1.5 pt-1">
              <AlertTriangle size={11} className="mt-0.5 shrink-0" />
              {data.outstanding.length} outstanding — blocks account activation.
            </div>
          )}
        </div>
      )}
    </Card>
  );
}

const CIP_STATUS_STYLE: Record<string, string> = {
  verified: "bg-positive/10 text-positive border-positive/20",
  review:   "bg-caution/10 text-caution border-caution/20",
  failed:   "bg-critical/10 text-critical border-critical/20",
};

function CipCard({ cip, onRun, busy }: { cip: any; onRun: () => void; busy: boolean }) {
  const status = cip.cip_status;
  return (
    <Card className={status ? `border ${CIP_STATUS_STYLE[status] || ""}` : ""}>
      <div className="font-semibold text-ink mb-2 flex items-center gap-2">
        <Fingerprint size={16} /> CIP identity verification
        {/* env indicator so it's obvious this is a mock */}
        <span className="text-[10px] text-ink-faint font-normal ml-auto">mock_socure</span>
      </div>
      {status ? (
        <>
          <div className="flex items-center gap-2 mb-2">
            <span className={`chip font-semibold uppercase tracking-wide ${CIP_STATUS_STYLE[status] || ""}`}>
              {status}
            </span>
            {cip.cip_score != null && (
              <span className="text-xs text-ink-muted">Score {Math.round(cip.cip_score * 100)}%</span>
            )}
          </div>
          {cip.cip_flags?.length > 0 && (
            <div className="flex flex-wrap gap-1 mb-2">
              {cip.cip_flags.map((f: string) => (
                <span key={f} className="chip bg-navy-50 text-ink-muted text-[10px]">{f.replace(/_/g, " ")}</span>
              ))}
            </div>
          )}
          {cip.cip_reference_id && (
            <div className="text-[10px] text-ink-faint font-mono">Ref: {cip.cip_reference_id}</div>
          )}
          {status !== "verified" && (
            <button className="btn-outline text-xs mt-2 w-full" disabled={busy} onClick={onRun}>
              <Fingerprint size={12} /> {busy ? "Verifying…" : "Re-run CIP check"}
            </button>
          )}
        </>
      ) : (
        <>
          <div className="text-sm text-ink-muted mb-3">Identity not yet verified. Run the CIP check to validate name, DOB, and ID number against the provider.</div>
          <button className="btn-outline text-xs w-full" disabled={busy} onClick={onRun}>
            <Fingerprint size={12} /> {busy ? "Verifying…" : "Run CIP check"}
          </button>
        </>
      )}
    </Card>
  );
}

function CustodianCard({ c, onPush, busy }: { c: any; onPush: () => void; busy: boolean }) {
  const hasCustodian = !!c.custodian_account_id;
  const canPush = c.status === "approved" && !hasCustodian;
  if (!hasCustodian && c.status !== "approved") return null;
  return (
    <Card className={hasCustodian ? "border-positive/20" : ""}>
      <div className="font-semibold text-ink mb-2 flex items-center gap-2">
        <Building2 size={16} /> Custodian account
        <span className="text-[10px] text-ink-faint font-normal ml-auto">mock_schwab</span>
      </div>
      {hasCustodian ? (
        <>
          <div className="flex items-center gap-2 mb-1">
            <span className="chip bg-positive/10 text-positive">{c.custodian_push_status || "active"}</span>
            <span className="text-xs text-ink-muted capitalize">{c.custodian_name}</span>
          </div>
          <div className="font-mono text-sm text-ink font-semibold">{c.custodian_account_id}</div>
          {c.custodian_push_at && (
            <div className="text-[10px] text-ink-faint mt-1">Pushed {new Date(c.custodian_push_at).toLocaleDateString()}</div>
          )}
        </>
      ) : (
        <>
          <div className="text-sm text-ink-muted mb-3">
            Account approved — single-key to custodian to open the account and receive an account number.
            {c.cip_status !== "verified" && (
              <span className="text-caution"> CIP must be verified first.</span>
            )}
          </div>
          <button className="btn-gold text-xs w-full" disabled={busy || c.cip_status !== "verified"} onClick={onPush}>
            <Building2 size={12} /> {busy ? "Pushing…" : "Push to custodian"}
          </button>
        </>
      )}
    </Card>
  );
}

const TRANSFER_STATUS_STYLE: Record<string, string> = {
  initiated:      "bg-navy-50 text-ink-muted",
  pending_review: "bg-caution/10 text-caution",
  in_transit:     "bg-gold-soft/40 text-gold-dark",
  settled:        "bg-positive/10 text-positive",
  failed:         "bg-critical/10 text-critical",
};
const TRANSFER_STATUS_ORDER = ["initiated", "pending_review", "in_transit", "settled"];

function TransfersPanel({ caseId }: { caseId: string }) {
  const { data: transfers, refetch } = useApi<any[]>(`/api/onboarding/cases/${caseId}/transfers`, [caseId]);
  const [showForm, setShowForm] = useState(false);
  const [form, setForm] = useState({ transfer_type: "acat", direction: "in", amount: "", asset_description: "", custodian: "" });
  const [busy, setBusy] = useState<string | null>(null);

  async function submit() {
    setBusy("add");
    try {
      await api(`/api/onboarding/cases/${caseId}/transfers`, {
        body: { ...form, amount: form.amount ? parseFloat(form.amount) : null },
      });
      setShowForm(false);
      setForm({ transfer_type: "acat", direction: "in", amount: "", asset_description: "", custodian: "" });
      refetch();
    } finally { setBusy(null); }
  }

  async function advance(transferId: string) {
    setBusy(transferId);
    try { await api(`/api/onboarding/cases/${caseId}/transfers/${transferId}/advance`, { method: "PUT", body: {} }); refetch(); }
    finally { setBusy(null); }
  }

  return (
    <Card>
      <div className="font-semibold text-ink mb-3 flex items-center gap-2">
        <ArrowDownUp size={16} className="text-navy-400" /> Transfer tracking
        <span className="text-[10px] text-ink-faint font-normal">(ACAT / wire / ACH)</span>
        <button className="btn-outline text-xs ml-auto" onClick={() => setShowForm(!showForm)}>
          <Plus size={12} /> Add
        </button>
      </div>

      {showForm && (
        <div className="rounded-xl border border-navy-100 p-3 mb-3 space-y-2 bg-navy-25">
          <div className="grid grid-cols-2 gap-2">
            <div>
              <label className="label text-[10px]">Type</label>
              <select className="input text-xs" value={form.transfer_type} onChange={(e) => setForm({ ...form, transfer_type: e.target.value })}>
                <option value="acat">ACAT (securities)</option>
                <option value="wire">Wire transfer</option>
                <option value="ach">ACH</option>
              </select>
            </div>
            <div>
              <label className="label text-[10px]">Direction</label>
              <select className="input text-xs" value={form.direction} onChange={(e) => setForm({ ...form, direction: e.target.value })}>
                <option value="in">In (incoming)</option>
                <option value="out">Out (outgoing)</option>
              </select>
            </div>
          </div>
          <div className="grid grid-cols-2 gap-2">
            <div>
              <label className="label text-[10px]">Amount ($)</label>
              <input className="input text-xs" value={form.amount} onChange={(e) => setForm({ ...form, amount: e.target.value })} placeholder="500000" />
            </div>
            <div>
              <label className="label text-[10px]">Custodian</label>
              <input className="input text-xs" value={form.custodian} onChange={(e) => setForm({ ...form, custodian: e.target.value })} placeholder="schwab" />
            </div>
          </div>
          <div>
            <label className="label text-[10px]">Asset / description</label>
            <input className="input text-xs" value={form.asset_description} onChange={(e) => setForm({ ...form, asset_description: e.target.value })} placeholder="e.g. AAPL shares, mixed mutual fund portfolio" />
          </div>
          <div className="flex gap-2 justify-end">
            <button className="btn-ghost text-xs" onClick={() => setShowForm(false)}>Cancel</button>
            <button className="btn-primary text-xs" disabled={busy === "add"} onClick={submit}>{busy === "add" ? "Submitting…" : "Submit transfer"}</button>
          </div>
        </div>
      )}

      {(transfers?.length ?? 0) === 0 && !showForm ? (
        <div className="text-sm text-ink-muted">No transfers yet. Add an ACAT, wire, or ACH transfer to track status.</div>
      ) : (
        <div className="space-y-3">
          {(transfers || []).map((t: any) => {
            const idx = TRANSFER_STATUS_ORDER.indexOf(t.status);
            return (
              <div key={t.id} className="rounded-xl border border-navy-100 p-3">
                <div className="flex items-center justify-between mb-2">
                  <div className="flex items-center gap-2">
                    <span className="text-sm font-medium text-ink uppercase tracking-wide">{t.transfer_type}</span>
                    <span className="text-xs text-ink-muted">{t.direction === "in" ? "↓ in" : "↑ out"}</span>
                    {t.amount != null && <span className="text-xs text-ink-soft">{money(t.amount)}</span>}
                  </div>
                  <span className={`chip text-[10px] ${TRANSFER_STATUS_STYLE[t.status] || "bg-navy-50 text-ink-muted"}`}>{t.status.replace(/_/g, " ")}</span>
                </div>
                {t.asset_description && <div className="text-xs text-ink-muted mb-2">{t.asset_description}</div>}
                {/* Status timeline */}
                <div className="flex items-center gap-1 mb-2">
                  {TRANSFER_STATUS_ORDER.map((s, i) => (
                    <div key={s} className="flex items-center gap-1">
                      <div className={`h-1.5 w-6 rounded-full ${i <= idx ? "bg-navy-600" : "bg-navy-100"}`} />
                      {i < TRANSFER_STATUS_ORDER.length - 1 && <div className="h-px w-2 bg-navy-100" />}
                    </div>
                  ))}
                </div>
                <div className="flex items-center justify-between">
                  <div className="text-[10px] text-ink-faint font-mono">{t.provider_ref}</div>
                  {t.status !== "settled" && t.status !== "failed" && (
                    <button className="text-[10px] text-navy-400 hover:text-navy-700 flex items-center gap-0.5" disabled={busy === t.id} onClick={() => advance(t.id)}>
                      {busy === t.id ? "…" : <><ChevronRight size={11} /> Advance status</>}
                    </button>
                  )}
                </div>
              </div>
            );
          })}
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
