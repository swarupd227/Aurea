"use client";
import { useEffect, useState } from "react";
import { Check, Pencil, X, FileText, ShieldAlert, ChevronDown, ChevronUp, Undo2, Bot,
  Scissors, Scale, Landmark, Target, TrendingDown, Wand2, Mail, Copy } from "lucide-react";
import { api } from "@/lib/api";
import { money, titleCase } from "@/lib/format";
import { ConfidenceBar, StatusBadge, TierBadge } from "./ui";
import { DriftBars } from "./Charts";

const pctf = (v: number) => `${(v * 100).toFixed(1)}%`;

/** The institutional-grade analysis that makes an adviser lean in — surfaces the tax,
 *  drift and mandate reasoning the engine already did. Only rendered for drift recs. */
function DriftAnalysis({ payload, ev, orders }: { payload: any; ev: any; orders: any[] }) {
  const s = payload.summary || {};
  const band = s.drift_band ?? payload.drift_band ?? 0.05;
  const maxDrift = s.max_drift ?? 0;
  const realised = payload.estimated_realised_gain ?? 0;
  const harvested = payload.harvested_losses ?? 0;
  const budget = payload.cgt_budget ?? ev.cgt_budget ?? null;
  const lossHarvestSells = orders.filter((o) => o.side === "sell" && o.est_realised_gain < 0).length;
  const exclusionsHonoured = orders.filter((o) => /values?-?excluded|exclusion|esg/i.test(o.reason || ""));
  const mandateType: string = payload.mandate?.type || "";
  const drifts: Record<string, number> = payload.drifts || {};
  const targets: Record<string, number> = payload.target_weights || {};
  const breaching = Object.keys(targets)
    .filter((c) => c !== "cash" && Math.abs(drifts[c] ?? 0) > band)
    .sort((a, b) => Math.abs(drifts[b] ?? 0) - Math.abs(drifts[a] ?? 0));
  const budgetUse = budget && budget > 0 ? Math.min(Math.max(realised / budget, 0), 1) : 0;
  const overBudget = budget != null && realised > budget;

  return (
    <div className="rounded-xl border border-navy-100 bg-surface p-4 space-y-4">
      <div className="tile-label flex items-center gap-1.5"><Scale size={13} /> Tax & rebalancing analysis</div>

      <div className="grid sm:grid-cols-3 gap-3">
        {/* Drift vs band */}
        <div className="rounded-lg bg-navy-50/60 border border-navy-100 p-3">
          <div className="flex items-center gap-1.5 text-xs text-ink-muted mb-1"><Target size={13} /> Why now</div>
          <div className="text-lg font-semibold text-ink tabular-nums">{pctf(maxDrift)}</div>
          <div className="text-xs text-ink-muted">max drift vs {pctf(band)} band</div>
          {breaching.length > 0 && (
            <div className="mt-1.5 text-xs text-ink-soft">
              {breaching.slice(0, 3).map((c) => (
                <div key={c} className="flex justify-between">
                  <span>{titleCase(c)}</span>
                  <span className={`tabular-nums ${(drifts[c] ?? 0) > 0 ? "text-critical" : "text-navy-600"}`}>
                    {(drifts[c] ?? 0) > 0 ? "+" : ""}{pctf(drifts[c] ?? 0)}
                  </span>
                </div>
              ))}
            </div>
          )}
        </div>

        {/* Tax — the hero */}
        <div className="rounded-lg bg-gold-soft/20 border border-gold/30 p-3">
          <div className="flex items-center gap-1.5 text-xs text-ink-muted mb-1"><Landmark size={13} /> Tax managed</div>
          <div className={`text-lg font-semibold tabular-nums ${overBudget ? "text-critical" : "text-ink"}`}>
            {money(realised)}
          </div>
          <div className="text-xs text-ink-muted">
            estimated realised gain{budget != null && <> · budget {money(budget)}</>}
          </div>
          {budget != null && budget > 0 && (
            <div className="mt-1.5 h-1.5 rounded-full bg-navy-100 overflow-hidden">
              <div className={`h-full ${overBudget ? "bg-critical" : "bg-positive"}`} style={{ width: `${budgetUse * 100}%` }} />
            </div>
          )}
          {harvested > 0 && (
            <div className="mt-1.5 text-xs text-positive flex items-center gap-1">
              <TrendingDown size={12} /> {money(harvested)} losses harvested
              {lossHarvestSells > 0 && <span className="text-ink-muted">· {lossHarvestSells} lot{lossHarvestSells > 1 ? "s" : ""}</span>}
            </div>
          )}
        </div>

        {/* Mandate & constraints */}
        <div className="rounded-lg bg-navy-50/60 border border-navy-100 p-3">
          <div className="flex items-center gap-1.5 text-xs text-ink-muted mb-1"><ShieldAlert size={13} /> Within mandate</div>
          {mandateType && (
            <div className="text-sm font-semibold text-ink capitalize">{mandateType} mandate</div>
          )}
          <div className="text-xs text-ink-muted">
            {mandateType === "discretionary"
              ? "Discretionary — still gated to you at Tier 2 for this trade size."
              : "Advisory — requires your explicit approval before any order."}
          </div>
          {exclusionsHonoured.length > 0 && (
            <div className="mt-1.5 text-xs text-ink-soft flex items-center gap-1">
              <Scissors size={12} className="text-navy-500" /> {exclusionsHonoured.length} values-excluded holding{exclusionsHonoured.length > 1 ? "s" : ""} honoured
            </div>
          )}
          {ev.guardrail_breaches?.length > 0 ? (
            <div className="mt-1.5 text-xs text-critical">⚠ {ev.guardrail_breaches.length} guardrail flag</div>
          ) : (
            <div className="mt-1.5 text-xs text-positive">✓ No guardrail breaches</div>
          )}
        </div>
      </div>

      <div className="text-xs text-ink-muted">
        Lots were selected <span className="text-ink-soft font-medium">losses-first, then smallest gains</span>, stopping at the
        capital-gains budget — restoring the target allocation the plan is funded on while minimising the tax cost.
      </div>
    </div>
  );
}

function CompliancePanel({ recId }: { recId: string }) {
  const [a, setA] = useState<any>(null);
  useEffect(() => { api(`/api/studio/recommendations/${recId}/compliance`).then(setA).catch(() => {}); }, [recId]);
  if (!a || a.available === false || !a.results?.length) return null;
  const tone = a.status === "blocked" ? "bg-critical/10 text-critical"
    : a.status === "flags" ? "bg-caution/10 text-caution" : "bg-positive/10 text-positive";
  return (
    <div className="rounded-xl border border-navy-100 bg-surface p-3">
      <div className="flex items-center gap-2 mb-2">
        <div className="tile-label flex items-center gap-1.5"><ShieldAlert size={13} /> Regulatory compliance</div>
        <span className="chip bg-navy-50 text-ink-muted">{a.regime} · {a.version}</span>
        <span className={`chip ${tone} ml-auto capitalize`}>{a.status === "clear" ? "Clear" : a.status}</span>
      </div>
      <div className="space-y-1.5">
        {a.results.map((r: any) => (
          <div key={r.rule_id} className="flex items-start gap-2 text-xs" title={r.citation}>
            {r.status === "pass" ? <Check size={13} className="text-positive shrink-0 mt-0.5" />
              : r.status === "fail" ? <ShieldAlert size={13} className={`shrink-0 mt-0.5 ${r.severity === "high" ? "text-critical" : "text-caution"}`} />
              : <span className="text-ink-muted shrink-0 w-3 text-center">—</span>}
            <span className="font-medium text-navy-700 shrink-0">{r.code}</span>
            <span className="text-ink-soft flex-1">
              {r.title}{r.status === "fail" && <span className="text-caution"> · {r.finding}</span>}
            </span>
          </div>
        ))}
      </div>
      <div className="text-[11px] text-ink-muted mt-2">Checked against the firm's configured framework and written to the ledger. Hover a line for the full citation.</div>
    </div>
  );
}

function CheckRow({ ok, label, warn }: { ok: boolean; label: string; warn?: string }) {
  return (
    <div className="flex items-center gap-1.5 text-xs">
      {ok ? <Check size={13} className="text-positive shrink-0" /> : <ShieldAlert size={13} className="text-caution shrink-0" />}
      <span className="text-ink-soft">{label}</span>
      {warn && <span className="text-caution">· {warn}</span>}
    </div>
  );
}

export default function RecommendationCard({
  rec,
  onDecided,
  defaultOpen = false,
}: {
  rec: any;
  onDecided?: (r: any) => void;
  defaultOpen?: boolean;
}) {
  const [open, setOpen] = useState(defaultOpen || rec.status === "proposed");
  const [mode, setMode] = useState<null | "approve" | "modify" | "dismiss" | "revise">(null);
  const [note, setNote] = useState("");
  const [excluded, setExcluded] = useState<Set<number>>(new Set());
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState<string | null>(null);
  const [rev, setRev] = useState({ note: "", cgt: "", band: "", exclude: "" });
  const [justApproved, setJustApproved] = useState(false);
  const [confirmRollback, setConfirmRollback] = useState(false);
  const [draftOpen, setDraftOpen] = useState(false);
  const [draftTone, setDraftTone] = useState("formal");
  const [draftType, setDraftType] = useState("email");
  const [draft, setDraft] = useState<{ subject?: string; body: string } | null>(null);
  const [draftBusy, setDraftBusy] = useState(false);
  const [copied, setCopied] = useState(false);

  const payload = rec.payload || {};
  const orders: any[] = payload.order_set || [];
  const ev = rec.evidence || {};
  const isDrift = !!(payload.drifts && payload.target_weights && orders.length);
  const harvested = payload.harvested_losses ?? 0;
  const realised = payload.estimated_realised_gain ?? 0;
  const budget = payload.cgt_budget ?? ev.cgt_budget ?? null;
  const exclusionsHonoured = isDrift
    ? orders.filter((o) => /values?-?excluded|exclusion|esg/i.test(o.reason || "")).length : 0;
  const decided = rec.status !== "proposed";
  const reversible = ["approved", "modified", "executed"].includes(rec.status);

  async function rollback() {
    setBusy(true);
    setConfirmRollback(false);
    setErr(null);
    try {
      const updated = await api(`/api/studio/recommendations/${rec.id}/rollback`, { body: {} });
      onDecided?.(updated);
    } catch (e: any) {
      setErr(e.message);
    } finally {
      setBusy(false);
    }
  }

  async function draftCommunication() {
    setDraftBusy(true); setDraft(null);
    try {
      const result = await api(`/api/studio/recommendations/${rec.id}/draft-communication`, {
        body: { tone: draftTone, output_type: draftType },
      });
      setDraft(result);
    } catch (e: any) {
      setErr(e.message);
    } finally { setDraftBusy(false); }
  }

  function copyDraft() {
    const text = [draft?.subject ? `Subject: ${draft.subject}` : "", draft?.body || ""].filter(Boolean).join("\n\n");
    navigator.clipboard.writeText(text);
    setCopied(true);
    setTimeout(() => setCopied(false), 1500);
  }

  async function revise() {
    setBusy(true);
    setErr(null);
    try {
      const body: any = { note: rev.note };
      if (rev.cgt.trim()) body.cgt_budget = Number(rev.cgt.replace(/[^0-9.]/g, ""));
      if (rev.band.trim()) body.drift_band = Number(rev.band) / 100;
      if (rev.exclude.trim()) body.protect = rev.exclude.split(",").map((s) => s.trim().toUpperCase()).filter(Boolean);
      const res = await api(`/api/studio/recommendations/${rec.id}/revise`, { body });
      if (res.new_recommendation) onDecided?.(res.new_recommendation);
      else setErr(res.message || "Nothing to rebalance after your changes.");
    } catch (e: any) {
      setErr(e.message);
    } finally {
      setBusy(false);
    }
  }

  async function submit(action: "approve" | "modify" | "dismiss") {
    setBusy(true);
    setErr(null);
    try {
      let modified_payload = undefined;
      if (action === "modify" && orders.length) {
        modified_payload = {
          ...payload,
          order_set: orders.filter((_, i) => !excluded.has(i)),
        };
      }
      const updated = await api(`/api/studio/recommendations/${rec.id}/decide`, {
        body: { action, note: note || null, modified_payload },
      });
      if (action === "approve" || action === "modify") {
        setJustApproved(true);
        setTimeout(() => setJustApproved(false), 3000);
      }
      onDecided?.(updated);
    } catch (e: any) {
      setErr(e.message);
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="card overflow-hidden">
      {/* Header */}
      <div className="p-4 flex items-start gap-3">
        <div className="flex-1 min-w-0">
          <div className="flex items-center gap-2 mb-1.5">
            <span className="h-6 w-6 rounded-full bg-navy-800 text-white flex items-center justify-center shrink-0"><Bot size={12} /></span>
            <span className="text-xs font-semibold text-ink">{titleCase(rec.agent_key)}</span>
            <span className="text-xs text-ink-muted">· {rec.status === "proposed" ? "I'd recommend" : "proposed"}</span>
          </div>
          <div className="flex items-center gap-2 flex-wrap">
            <TierBadge tier={rec.tier} />
            <StatusBadge status={rec.status} />
            {ev.guardrail_breaches?.length > 0 && (
              <span className="chip bg-critical/10 text-critical">
                <ShieldAlert size={12} /> {ev.guardrail_breaches.length} guardrail flag
              </span>
            )}
          </div>
          <h3 className="font-semibold text-ink mt-2">{rec.title}</h3>
          <p className="text-sm text-ink-soft mt-0.5">{rec.summary}</p>
          {rec.subject_label && (
            <p className="text-xs text-ink-muted mt-1">Subject · {rec.subject_label}</p>
          )}
          {payload.revision_note && (
            <div className="mt-2 text-xs rounded-md bg-gold-soft/30 border border-gold/30 text-gold-dark px-2 py-1 flex items-center gap-1.5">
              <Wand2 size={12} /> Revised on your instruction: “{payload.revision_note}”
            </div>
          )}
          {isDrift && (
            <div className="flex flex-wrap items-center gap-1.5 mt-2">
              {harvested > 0 && (
                <span className="chip bg-positive/10 text-positive"><TrendingDown size={11} /> {money(harvested)} losses harvested</span>
              )}
              <span className={`chip ${budget != null && realised > budget ? "bg-critical/10 text-critical" : "bg-gold-soft/40 text-gold-dark"}`}>
                <Landmark size={11} /> Realised {money(realised)}{budget != null && <> / {money(budget)} budget</>}
              </span>
              {exclusionsHonoured > 0 && (
                <span className="chip bg-navy-50 text-navy-700"><Scissors size={11} /> {exclusionsHonoured} exclusion{exclusionsHonoured > 1 ? "s" : ""} honoured</span>
              )}
              {payload.mandate?.type && (
                <span className="chip bg-navy-50 text-navy-700 capitalize">{payload.mandate.type}</span>
              )}
            </div>
          )}
        </div>
        <div className="flex flex-col items-end gap-2 shrink-0">
          <ConfidenceBar value={rec.confidence} />
          <button onClick={() => setOpen(!open)} className="btn-ghost px-2 py-1 text-xs" title={open ? "Collapse" : "View rationale, orders, compliance and evidence"}>
            {open ? <>Hide <ChevronUp size={14} /></> : <>Rationale & orders <ChevronDown size={14} /></>}
          </button>
        </div>
      </div>

      {/* Approval success flash */}
      {justApproved && (
        <div className="mx-4 mb-0 mt-0 rounded-lg bg-positive/10 border border-positive/30 px-3 py-2 flex items-center gap-2 text-sm text-positive font-medium fade-in">
          <Check size={16} /> Decision recorded — written to the ledger.
        </div>
      )}

      {open && (
        <div className="border-t border-navy-100/70 p-4 space-y-4 bg-navy-50/30">
          {/* Rationale */}
          {rec.rationale && (
            <div>
              <div className="tile-label mb-1">Rationale</div>
              <p className="text-sm text-ink-soft whitespace-pre-line leading-relaxed">{rec.rationale}</p>
            </div>
          )}

          {/* Deep analysis — the institutional reasoning, surfaced */}
          {isDrift && <DriftAnalysis payload={payload} ev={ev} orders={orders} />}

          {/* Order set */}
          {orders.length > 0 && (
            <div>
              <div className="flex items-center justify-between mb-1">
                <div className="tile-label">Draft order set · multi-custodian</div>
                {payload.summary && (
                  <div className="text-xs text-ink-muted">
                    Turnover {money(payload.summary.turnover)} · realised gain{" "}
                    {money(payload.estimated_realised_gain)}
                    {payload.harvested_losses > 0 && <> · harvested {money(payload.harvested_losses)}</>}
                  </div>
                )}
              </div>
              <div className="overflow-x-auto rounded-lg border border-navy-100 bg-surface">
                <table className="w-full text-sm">
                  <thead className="bg-navy-50 text-ink-muted text-xs uppercase tracking-wide">
                    <tr>
                      {mode === "modify" && <th className="px-3 py-2 w-8"></th>}
                      <th className="px-3 py-2 text-left">Side</th>
                      <th className="px-3 py-2 text-left">Instrument</th>
                      <th className="px-3 py-2 text-right">Qty</th>
                      <th className="px-3 py-2 text-right">Value</th>
                      <th className="px-3 py-2 text-right">Est. gain</th>
                      <th className="px-3 py-2 text-left">Custodian</th>
                      <th className="px-3 py-2 text-left">Reason</th>
                    </tr>
                  </thead>
                  <tbody>
                    {orders.map((o, i) => (
                      <tr key={i} className={`border-t border-navy-50 ${excluded.has(i) ? "opacity-40" : ""}`}>
                        {mode === "modify" && (
                          <td className="px-3 py-2">
                            <input
                              type="checkbox"
                              checked={!excluded.has(i)}
                              onChange={() => {
                                const n = new Set(excluded);
                                n.has(i) ? n.delete(i) : n.add(i);
                                setExcluded(n);
                              }}
                            />
                          </td>
                        )}
                        <td className="px-3 py-2">
                          <span className={`chip ${o.side === "sell" ? "bg-critical/10 text-critical" : "bg-positive/10 text-positive"}`}>
                            {o.side.toUpperCase()}
                          </span>
                        </td>
                        <td className="px-3 py-2 font-medium text-ink">{o.symbol}</td>
                        <td className="px-3 py-2 text-right tabular-nums">{o.quantity.toLocaleString()}</td>
                        <td className="px-3 py-2 text-right tabular-nums">{money(o.est_value)}</td>
                        <td className={`px-3 py-2 text-right tabular-nums ${o.est_realised_gain < 0 ? "text-positive" : "text-ink-soft"}`}>
                          {money(o.est_realised_gain)}
                        </td>
                        <td className="px-3 py-2 text-ink-muted text-xs">{o.custodian}</td>
                        <td className="px-3 py-2 text-ink-muted text-xs">{o.reason}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
              {payload.drifts && payload.target_weights && (
                <div className="mt-3">
                  <div className="tile-label mb-1">Allocation drift vs target</div>
                  <DriftBars current={payload.current_weights} target={payload.target_weights} />
                </div>
              )}
            </div>
          )}

          {/* Meeting brief — render structured, not raw JSON */}
          {orders.length === 0 && payload.brief && <MeetingBriefView brief={payload.brief} />}

          {/* Generic payload (other non-order agents) */}
          {orders.length === 0 && !payload.brief && Object.keys(payload).length > 0 && (
            <PayloadView payload={payload} />
          )}

          {/* Regulatory compliance — the cited, versioned assessment */}
          <CompliancePanel recId={rec.id} />

          {/* Evidence & lineage */}
          <div className="grid sm:grid-cols-2 gap-3">
            <div className="rounded-lg border border-navy-100 bg-surface p-3">
              <div className="tile-label mb-1.5">Data & lineage</div>
              <dl className="text-xs space-y-1">
                {evidenceRows(ev).map((r) => <Row key={r.k} k={r.label} v={r.val} />)}
              </dl>
              {evidenceRows(ev).length === 0 && !ev.guardrail_breaches?.length && (
                <div className="text-xs text-ink-muted">Sourced from the governed client brain.</div>
              )}
              {ev.guardrail_breaches?.length > 0 && (
                <div className="mt-2 text-xs text-critical">
                  {ev.guardrail_breaches.map((b: string, i: number) => (
                    <div key={i}>⚠ {b}</div>
                  ))}
                </div>
              )}
            </div>
            <div className="rounded-lg border border-navy-100 bg-surface p-3">
              <div className="tile-label mb-1.5 flex items-center gap-1">
                <FileText size={12} /> Firm research cited
              </div>
              {rec.citations?.length ? (
                <ul className="text-xs space-y-1.5">
                  {rec.citations.map((c: any, i: number) => (
                    <li key={i} className="text-ink-soft">
                      <span className="font-medium text-ink">{c.title}</span>
                      {c.author && <span className="text-ink-muted"> · {c.author}</span>}
                    </li>
                  ))}
                </ul>
              ) : (
                <div className="text-xs text-ink-muted">No firm research cited.</div>
              )}
            </div>
          </div>
        </div>
      )}

      {/* Decision footer (HITL gate) */}
      {!decided && (
        <div className="border-t border-navy-100/70 p-3">
          {!mode ? (
            <div className="flex flex-wrap items-center gap-2">
              <span className="text-xs text-ink-muted mr-auto">Adviser decision required · Tier {rec.tier.slice(-1)}</span>
              <button className="btn-outline" onClick={() => { setMode("dismiss"); setOpen(true); }}>
                <X size={15} /> Dismiss
              </button>
              {isDrift && (
                <button className="btn-outline" onClick={() => { setMode("revise"); setOpen(true); }}>
                  <Wand2 size={15} /> Revise
                </button>
              )}
              {orders.length > 0 && (
                <button className="btn-outline" onClick={() => { setMode("modify"); setOpen(true); }}>
                  <Pencil size={15} /> Modify
                </button>
              )}
              <button className="btn-gold" onClick={() => { setMode("approve"); setOpen(true); }}>
                <Check size={15} /> Approve
              </button>
            </div>
          ) : mode === "revise" ? (
            <div className="space-y-2">
              <div className="text-xs text-ink-soft">Tell the agent what to change — it will re-run and propose a revised order set.</div>
              <textarea className="input text-sm" rows={2}
                placeholder="e.g. Keep realised gains under $10k and don't sell AAPL — harvest losses elsewhere."
                value={rev.note} onChange={(e) => setRev({ ...rev, note: e.target.value })} />
              <div className="grid grid-cols-3 gap-2">
                <div>
                  <label className="label text-[11px]">CGT cap <span className="text-ink-muted font-normal">(optional $)</span></label>
                  <input className="input text-sm" placeholder="e.g. 15000" value={rev.cgt} onChange={(e) => setRev({ ...rev, cgt: e.target.value })} />
                </div>
                <div>
                  <label className="label text-[11px] flex items-center gap-1">
                    Drift band %
                    <span title="The max % deviation from target allocation before a holding triggers a rebalance. Default 5%. Lower = more trades, higher = fewer." className="cursor-help text-navy-400">ⓘ</span>
                    <span className="text-ink-muted font-normal">(optional)</span>
                  </label>
                  <input className="input text-sm" placeholder="e.g. 3" value={rev.band} onChange={(e) => setRev({ ...rev, band: e.target.value })} />
                </div>
                <div>
                  <label className="label text-[11px]">Protect holdings <span className="text-ink-muted font-normal">(don't sell)</span></label>
                  <input className="input text-sm" placeholder="e.g. AAPL, MSFT" value={rev.exclude} onChange={(e) => setRev({ ...rev, exclude: e.target.value })} />
                </div>
              </div>
              {err && <div className="text-xs text-critical">{err}</div>}
              <div className="flex items-center gap-2 justify-end">
                <button className="btn-ghost" onClick={() => setMode(null)} disabled={busy}>Cancel</button>
                <button className="btn-primary" disabled={busy || !rev.note.trim()} onClick={revise}>
                  <Wand2 size={15} /> {busy ? "Re-running…" : "Revise & re-run"}
                </button>
              </div>
            </div>
          ) : (
            <div className="space-y-2">
              <textarea
                className="input text-sm"
                rows={2}
                placeholder={
                  mode === "dismiss" ? "Reason for dismissing (optional)…"
                  : mode === "modify" ? "Note — uncheck any orders above to exclude them…"
                  : "Approval note (optional)…"
                }
                value={note}
                onChange={(e) => setNote(e.target.value)}
              />
              {err && <div className="text-xs text-critical">{err}</div>}
              <div className="flex items-center gap-2 justify-end">
                <button className="btn-ghost" onClick={() => setMode(null)} disabled={busy}>
                  Cancel
                </button>
                <button
                  className={mode === "dismiss" ? "btn-outline" : "btn-primary"}
                  disabled={busy}
                  onClick={() => submit(mode)}
                >
                  {busy ? "Recording…" : `Confirm ${titleCase(mode)}`}
                </button>
              </div>
            </div>
          )}
        </div>
      )}

      {decided && rec.decision_note && (
        <div className="border-t border-navy-100/70 px-4 py-2 text-xs text-ink-muted">
          Note: {rec.decision_note}
        </div>
      )}

      {reversible && onDecided && (
        <>
          <div className="border-t border-navy-100/70 px-4 py-2 flex items-center justify-between gap-3">
            <span className="text-xs text-ink-muted">
              {rec.status === "executed" ? "Executed" : "Approved"} · reversible · written to ledger
            </span>
            <div className="flex items-center gap-2">
              <button className="btn-ghost text-xs" onClick={() => { setDraftOpen(!draftOpen); setDraft(null); }}>
                <Mail size={13} /> Draft client letter
              </button>
              {!confirmRollback ? (
                <button className="btn-ghost text-xs text-critical" disabled={busy} onClick={() => setConfirmRollback(true)}>
                  <Undo2 size={13} /> Roll back
                </button>
              ) : (
                <span className="flex items-center gap-2">
                  <span className="text-xs text-critical font-medium">Roll back — effects will be reversed. Confirm?</span>
                  <button className="btn-ghost text-xs" onClick={() => setConfirmRollback(false)}>Cancel</button>
                  <button className="btn-ghost text-xs border border-critical/40 text-critical px-2 py-0.5 rounded" disabled={busy} onClick={rollback}>
                    {busy ? "Rolling back…" : "Confirm"}
                  </button>
                </span>
              )}
            </div>
          </div>
          {draftOpen && (
            <div className="border-t border-navy-100/70 px-4 py-3 bg-navy-50/30 space-y-3">
              <div className="flex items-center justify-between">
                <div className="text-xs font-semibold text-ink flex items-center gap-1.5"><Mail size={13} /> Draft client communication</div>
                <button className="btn-ghost text-xs py-0.5 px-1" onClick={() => setDraftOpen(false)}><X size={13} /></button>
              </div>
              <div className="flex items-center gap-3">
                <div>
                  <label className="label text-[11px]">Tone</label>
                  <select className="input text-xs h-7 py-0" value={draftTone} onChange={(e) => setDraftTone(e.target.value)}>
                    <option value="formal">Formal</option>
                    <option value="warm">Warm</option>
                  </select>
                </div>
                <div>
                  <label className="label text-[11px]">Type</label>
                  <select className="input text-xs h-7 py-0" value={draftType} onChange={(e) => setDraftType(e.target.value)}>
                    <option value="email">Email</option>
                    <option value="letter">Letter</option>
                    <option value="sms_summary">SMS summary</option>
                  </select>
                </div>
                <div className="self-end">
                  <button className="btn-primary text-xs py-1 px-3" disabled={draftBusy} onClick={draftCommunication}>
                    {draftBusy ? "Drafting…" : "Generate draft"}
                  </button>
                </div>
              </div>
              {draft && (
                <div className="space-y-2">
                  {draft.subject && (
                    <div className="rounded-lg border border-navy-100 bg-surface px-3 py-2 text-sm font-medium text-ink">
                      Subject: {draft.subject}
                    </div>
                  )}
                  <div className="rounded-lg border border-navy-100 bg-surface px-3 py-2 text-sm text-ink-soft whitespace-pre-line leading-relaxed">
                    {draft.body}
                  </div>
                  <button className="btn-outline text-xs" onClick={copyDraft}>
                    {copied ? <><Check size={13} /> Copied</> : <><Copy size={13} /> Copy to clipboard</>}
                  </button>
                </div>
              )}
            </div>
          )}
        </>
      )}
      {rec.status === "rolled_back" && (
        <div className="border-t border-navy-100/70 px-4 py-2 text-xs text-ink-muted flex items-center gap-1.5">
          <Undo2 size={13} /> Rolled back · {(rec.payload?.rollback_result?.note) || "effects reversed"}
        </div>
      )}
      {err && <div className="px-4 py-2 text-xs text-critical">{err}</div>}
    </div>
  );
}

const EV_LABELS: Record<string, string> = {
  data_confidence: "Data confidence", cgt_budget: "CGT budget", price_source: "Price source",
  n_positions: "Positions", source: "Source", scope: "Scope", signal: "Signal",
  household: "Household", instrument: "Instrument", weight: "Weight", total_loss: "Loss available",
  cash: "Idle cash", probability: "Probability", goal: "Goal",
};
const EV_TITLECASE = new Set(["source", "scope", "signal"]);

/** Render any agent's evidence as readable lineage rows (drift, NBA, client-care, …). */
function evidenceRows(ev: any): { k: string; label: string; val: string }[] {
  return Object.entries(ev || {})
    .filter(([k, v]) => k !== "guardrail_breaches" && v !== null && v !== undefined && typeof v !== "object")
    .map(([k, v]) => {
      let val: string;
      if (k === "data_confidence" || (k === "probability" && Number(v) <= 1)) val = `${Math.round(Number(v) * 100)}%`;
      else if (k === "weight" && Number(v) <= 1) val = `${Math.round(Number(v) * 100)}%`;
      else if (["cgt_budget", "total_loss", "cash"].includes(k)) val = money(Number(v));
      else if (typeof v === "number" && Math.abs(v) >= 1000) val = Number(v).toLocaleString();
      else if (typeof v === "string") val = EV_TITLECASE.has(k) ? titleCase(v) : v;
      else val = String(v);
      return { k, label: EV_LABELS[k] || titleCase(k), val };
    });
}

function Row({ k, v }: { k: string; v: any }) {
  return (
    <div className="flex justify-between gap-2">
      <dt className="text-ink-muted">{k}</dt>
      <dd className="text-ink-soft font-medium text-right">{v}</dd>
    </div>
  );
}

function MeetingBriefView({ brief }: { brief: any }) {
  const goals = brief.goals || [];
  const onTrack = goals.filter((g: any) => g.on_track).length;
  return (
    <div className="rounded-lg border border-navy-100 bg-surface p-3 space-y-3">
      <div className="grid grid-cols-2 gap-3">
        <div className="rounded-lg bg-navy-50 p-2.5"><div className="tile-label">Portfolio</div><div className="font-semibold text-ink">{money(brief.portfolio?.total_value || 0)}</div></div>
        <div className="rounded-lg bg-navy-50 p-2.5"><div className="tile-label">Goals on track</div><div className="font-semibold text-ink">{onTrack}/{goals.length}</div></div>
      </div>
      {brief.agenda?.length > 0 && (
        <div><div className="tile-label mb-1">Suggested agenda</div>
          <ul className="space-y-1 text-sm">{brief.agenda.map((a: string, i: number) => <li key={i} className="text-ink-soft flex gap-2"><span className="text-gold">•</span> {a}</li>)}</ul></div>
      )}
      {brief.watch_items?.length > 0 && (
        <div><div className="tile-label mb-1">Watch items</div>
          <ul className="space-y-1">{brief.watch_items.map((w: any, i: number) => <li key={i} className="text-ink-soft text-xs flex gap-1.5"><span className="text-caution">⚠</span> {w.title}</li>)}</ul></div>
      )}
      {brief.life_events?.length > 0 && (
        <div><div className="tile-label mb-1">Life events</div>
          <div className="flex flex-wrap gap-1.5">{brief.life_events.map((e: string, i: number) => <span key={i} className="chip bg-navy-50 text-ink-soft">{e}</span>)}</div></div>
      )}
      {brief.house_views?.length > 0 && (
        <div><div className="tile-label mb-1 flex items-center gap-1"><FileText size={12} /> House views</div>
          {brief.house_views.map((h: any, i: number) => <div key={i} className="text-xs text-ink-soft"><span className="font-medium text-ink">{h.title}</span></div>)}</div>
      )}
    </div>
  );
}

const NOISY_KEYS = ["execution_result", "rollback_result", "meeting_id", "signal", "brief"];

function renderVal(v: any): string {
  if (v === null || v === undefined) return "—";
  if (Array.isArray(v)) {
    if (v.length === 0) return "—";
    if (typeof v[0] === "object")
      return v.map((x: any) => x.title || x.name || x.label || x.goal || x.instrument).filter(Boolean).join(", ") || `${v.length} item(s)`;
    return v.join(", ");
  }
  if (typeof v === "object")
    return Object.entries(v).map(([k, val]) => `${titleCase(k)} ${typeof val === "object" ? "…" : val}`).join(" · ");
  if (typeof v === "number" && Math.abs(v) >= 1000) return v.toLocaleString();
  return String(v);
}

function PayloadView({ payload }: { payload: any }) {
  const entries = Object.entries(payload).filter(([k]) => !NOISY_KEYS.includes(k));
  if (entries.length === 0) return null;
  return (
    <div className="rounded-lg border border-navy-100 bg-surface p-3">
      <div className="tile-label mb-1.5">Proposed details</div>
      <div className="text-sm text-ink-soft space-y-1">
        {entries.map(([k, v]) => (
          <div key={k} className="flex gap-2">
            <span className="text-ink-muted min-w-[140px]">{titleCase(k)}</span>
            <span className="flex-1">{renderVal(v)}</span>
          </div>
        ))}
      </div>
    </div>
  );
}
