"use client";
import { useState } from "react";
import { useParams } from "next/navigation";
import Link from "next/link";
import { FileText, ShieldCheck, ShieldAlert, Play, Check, X, Pencil, Plus, ArrowRight, CheckCircle2 } from "lucide-react";
import PageHeader from "@/components/PageHeader";
import { Card, Spinner, Segment, ConfidenceBar, SeverityBadge } from "@/components/ui";
import { useApi } from "@/lib/hooks";
import { api } from "@/lib/api";
import { titleCase, money } from "@/lib/format";

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
        sub={`${titleCase(c.segment)} · ${c.is_entity ? titleCase(c.entity_type || "entity") : "Individual"} · status ${titleCase(c.status)}`}
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

          {/* Proposal */}
          {proposal.mandate && (
            <Card>
              <div className="font-semibold text-ink mb-3">Proposed suitability & mandate</div>
              <div className="grid sm:grid-cols-2 gap-4 text-sm">
                <div>
                  <div className="tile-label mb-1">Suitability</div>
                  <Row k="Risk profile" v={titleCase(proposal.suitability?.risk_profile || "—")} />
                  <Row k="Capacity for loss" v={titleCase(proposal.suitability?.capacity_for_loss || "—")} />
                  <Row k="Horizon" v={proposal.suitability?.time_horizon_years ? `${proposal.suitability.time_horizon_years}y` : "—"} />
                </div>
                <div>
                  <div className="tile-label mb-1">Mandate</div>
                  <Row k="Type" v={titleCase(proposal.mandate?.mandate_type || "—")} />
                  <Row k="Model" v={proposal.mandate?.model_portfolio || "—"} />
                  <Row k="CGT budget" v={money(proposal.mandate?.constraints?.cgt_budget)} />
                </div>
              </div>
            </Card>
          )}
        </div>

        {/* Right column */}
        <div className="space-y-5">
          {/* AML screening */}
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
