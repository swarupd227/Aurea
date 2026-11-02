"use client";

import { useState } from "react";
import { ShieldCheck, Link2, CheckCircle2, AlertTriangle, Activity, TrendingDown, Download, ArrowUpRight } from "lucide-react";
import { Card, Spinner, SeverityBadge, Empty, ErrorState } from "@/components/ui";
import { useApi } from "@/lib/hooks";
import { api } from "@/lib/api";
import { formatDateFull, timeAgo, titleCase } from "@/lib/format";
import { withRef } from "@/lib/refs";
import { useViewNav } from "./ViewNav";
import { ViewHeader } from "./ViewHeader";

const GRADE_STYLE: Record<string, string> = {
  healthy: "bg-ok-bg text-ok",
  watch: "bg-warn-bg text-warn",
  regressed: "bg-crit-bg text-crit",
  unrated: "bg-border-soft text-ink-muted",
};

const STATUS_STYLE: Record<string, string> = { clear: "bg-ok-bg text-ok", blocked: "bg-crit-bg text-crit" };

/** A surveillance flag named in words, for a sentence handed to the conversation. */
function flagWords(f: any): string {
  const finding = (f.finding || "").trim();
  const short = finding.length > 90 ? `${finding.slice(0, 90)}…` : finding;
  const agent = f.target_agent_key ? ` on ${titleCase(f.target_agent_key)}` : "";
  return `the ${f.severity} ${titleCase(f.category || "conduct").toLowerCase()} surveillance flag${agent}${short ? ` ("${short}")` : ""}`;
}

/** The governance record: the decision ledger and its hash chain, regulatory assessments, data
 * quality, agent quality and autonomy, and conduct-surveillance flags. In the work pane, resolving
 * or escalating a flag and re-running the evaluation are the conversation's tools, behind their
 * Confirm card; on a page they act directly. */
export function ProvenanceView() {
  const nav = useViewNav();
  const { data: ledger, loading, refetch: refetchLedger } = useApi<any[]>("/api/provenance/ledger");
  const { data: flags, loading: flagsLoading, error: flagsError, refetch: refetchFlags } =
    useApi<any[]>("/api/provenance/surveillance");
  const { data: evals, refetch: refetchEvals } = useApi<any[]>("/api/provenance/evaluations");
  const { data: changes, refetch: refetchChanges } = useApi<any[]>("/api/provenance/autonomy-changes");
  const { data: dq } = useApi<any>("/api/analytics/risk-data");
  const { data: comp } = useApi<any>("/api/provenance/compliance");
  const [verify, setVerify] = useState<any>(null);
  const [expanded, setExpanded] = useState<number | null>(null);
  const [evaluating, setEvaluating] = useState(false);
  const [resolving, setResolving] = useState<string | null>(null);
  const [resolveNote, setResolveNote] = useState("");
  const [escalating, setEscalating] = useState<string | null>(null);
  const [escalateTo, setEscalateTo] = useState("");
  const [escalateNote, setEscalateNote] = useState("");
  const [flagBusy, setFlagBusy] = useState(false);
  const [showReport, setShowReport] = useState(false);
  const [reportFrom, setReportFrom] = useState("");
  const [reportTo, setReportTo] = useState("");
  const [reportBusy, setReportBusy] = useState(false);

  async function runVerify() {
    setVerify(await api("/api/provenance/ledger/verify"));
  }
  async function runEval() {
    if (nav.say) return nav.say("Re-run the agent quality evaluation.");
    setEvaluating(true);
    try {
      await api("/api/provenance/evaluate", { body: {} });
      refetchEvals();
      refetchChanges();
      refetchLedger();
      nav.changed();
    } finally {
      setEvaluating(false);
    }
  }
  async function resolveFlag(f: any) {
    if (nav.say) {
      const note = resolveNote.trim();
      nav.say(withRef(`Resolve ${flagWords(f)}.${note ? ` Note: ${note}` : ""}`, "surveillance_flag", f.id));
      setResolving(null); setResolveNote("");
      return;
    }
    setFlagBusy(true);
    try {
      await api(`/api/provenance/surveillance/${f.id}/resolve`, {
        method: "PATCH", body: { resolution_note: resolveNote || null, resolved: true },
      });
      setResolving(null); setResolveNote("");
      refetchFlags();
      nav.changed();
    } finally { setFlagBusy(false); }
  }
  async function escalateFlag(f: any) {
    if (!escalateTo.trim()) return;
    if (nav.say) {
      const note = escalateNote.trim();
      nav.say(withRef(`Escalate ${flagWords(f)} to ${escalateTo.trim()}.${note ? ` Note: ${note}` : ""}`, "surveillance_flag", f.id));
      setEscalating(null); setEscalateTo(""); setEscalateNote("");
      return;
    }
    setFlagBusy(true);
    try {
      await api(`/api/provenance/surveillance/${f.id}/escalate`, {
        body: { escalated_to: escalateTo, escalation_note: escalateNote || null },
      });
      setEscalating(null); setEscalateTo(""); setEscalateNote("");
      refetchFlags();
      nav.changed();
    } finally { setFlagBusy(false); }
  }
  function exportLedger(format: string) {
    window.open(`/api/provenance/ledger/export?format=${format}`, "_blank");
  }
  async function generateReport() {
    if (!reportFrom || !reportTo) return;
    setReportBusy(true);
    try {
      window.open(`/api/provenance/reports/generate?date_from=${reportFrom}&date_to=${reportTo}&format=csv`, "_blank");
      setShowReport(false);
    } finally {
      setReportBusy(false);
    }
  }

  const openFlags = flags?.filter((f) => !f.resolved).length ?? 0;

  return (
    <div className="view-container">
      <ViewHeader
        title="Provenance"
        meta={
          <>
            {ledger && <span className="chip text-ink-muted tabular-nums">{ledger.length} ledger entries</span>}
            {flags && <span className={`chip tabular-nums ${openFlags ? "text-warn" : "text-ink-muted"}`}>{openFlags} open flags</span>}
          </>
        }
        actions={
          <>
            <div className="relative group">
              <button className="btn-outline text-xs">
                <Download size={13} /> Export ledger
              </button>
              <div className="absolute right-0 top-full mt-1 z-10 hidden group-hover:flex flex-col bg-surface border border-border rounded-xl shadow-lift overflow-hidden min-w-[140px]">
                <button className="px-4 py-2.5 text-sm text-left hover:bg-border-soft text-ink" onClick={() => exportLedger("csv")}>CSV</button>
                <button className="px-4 py-2.5 text-sm text-left hover:bg-border-soft text-ink" onClick={() => exportLedger("jsonl")}>JSONL</button>
              </div>
            </div>
            <button className="btn-outline text-xs" data-testid="compliance-report" onClick={() => setShowReport((v) => !v)}>
              <ArrowUpRight size={13} /> Compliance report
            </button>
            <button className="btn-outline text-xs" data-testid="ledger-verify" onClick={runVerify}>
              <ShieldCheck size={13} /> Verify chain
            </button>
          </>
        }
      />

      {verify && (
        <div className={`card p-4 mb-5 flex flex-wrap items-center gap-2 ${verify.valid ? "border-ok" : "border-crit"}`} data-testid="ledger-verify-result">
          {verify.valid ? <CheckCircle2 size={16} className="text-ok" /> : <AlertTriangle size={16} className="text-crit" />}
          <span className={`chip ${verify.valid ? "bg-ok-bg text-ok" : "bg-crit-bg text-crit"}`}>
            {verify.valid ? "chain intact" : `broken at #${verify.broken_at_seq}`}
          </span>
          <span className="chip text-ink-muted tabular-nums">{verify.count} entries</span>
          <span className="chip text-ink-muted">SHA-256 hash chain</span>
        </div>
      )}

      {showReport && (
        <Card className="mb-5">
          <div className="tile-label mb-3 flex items-center gap-1.5"><ArrowUpRight size={12} /> Compliance report</div>
          <div className="grid gap-3 mb-3 cq-sm:grid-cols-2">
            <div><label className="label">From date</label>
              <input type="date" className="input" value={reportFrom} onChange={(e) => setReportFrom(e.target.value)} /></div>
            <div><label className="label">To date</label>
              <input type="date" className="input" value={reportTo} onChange={(e) => setReportTo(e.target.value)} /></div>
          </div>
          <div className="flex flex-wrap gap-2">
            <button className="btn-primary text-xs" onClick={generateReport} disabled={reportBusy || !reportFrom || !reportTo}>
              {reportBusy ? "Generating…" : "Download CSV"}
            </button>
            <button className="btn-outline text-xs" onClick={() => setShowReport(false)}>Cancel</button>
          </div>
        </Card>
      )}

      {/* Regulatory compliance: the cited framework and recent assessments */}
      {comp?.framework && (
        <Card className="mb-5">
          <div className="flex flex-wrap items-center gap-1.5 mb-3">
            <ShieldCheck size={15} className="text-ink-muted" />
            <span className="font-semibold text-ink mr-1">Regulatory compliance</span>
            <span className="chip bg-border-soft text-ink-soft">{comp.framework.regime} · {comp.framework.name} · v{comp.framework.version}</span>
            <span className="chip text-ink-muted">{comp.framework.authority} · {comp.framework.rule_count} rules</span>
          </div>
          <div className="grid grid-cols-3 gap-3 mb-3">
            {[["Clear", comp.by_status?.clear || 0, "text-ok"], ["Flagged", comp.by_status?.flags || 0, "text-warn"],
              ["Blocked", comp.by_status?.blocked || 0, "text-crit"]].map(([l, v, c]: any) => (
              <div key={l} className="rounded-lg border border-border-soft p-2.5">
                <div className="tile-label">{l}</div>
                <div className={`text-lg font-semibold tabular-nums ${c}`}>{v}</div>
              </div>
            ))}
          </div>
          <div className="divide-y divide-border-soft max-h-[260px] overflow-y-auto">
            {(comp.assessments || []).slice(0, 12).map((a: any) => {
              const fails = (a.results || []).filter((r: any) => r.status === "fail");
              return (
                <div key={a.id} className="py-2 flex items-start gap-2 text-sm">
                  <span className={`chip shrink-0 ${STATUS_STYLE[a.status] ?? "bg-warn-bg text-warn"}`}>{a.status === "clear" ? "Clear" : titleCase(a.status)}</span>
                  <div className="min-w-0 flex-1">
                    <span className="text-ink-soft">{titleCase(a.agent_key)}</span>
                    {a.subject && <span className="text-ink-muted"> · {a.subject}</span>}
                    {fails.length > 0 && <div className="text-xs text-warn mt-0.5 font-mono">{fails.map((r: any) => r.code).join(" · ")}</div>}
                  </div>
                </div>
              );
            })}
            {!comp.assessments?.length && <div className="py-3"><span className="chip text-ink-muted">no assessments yet</span></div>}
          </div>
        </Card>
      )}

      {/* Data quality */}
      {dq?.data_quality && (
        <Card className="mb-5">
          <div className="font-semibold text-ink mb-3 flex items-center gap-2"><Activity size={15} className="text-ink-muted" /> Data quality</div>
          <div className="grid grid-cols-2 gap-3 cq-sm:grid-cols-3 cq-md:grid-cols-5">
            {[["Score", dq.data_quality.score], ["Completeness", dq.data_quality.completeness],
              ["Timeliness", dq.data_quality.timeliness], ["Accuracy", dq.data_quality.accuracy],
              ["Avg confidence", dq.data_quality.avg_confidence]].map(([label, v]: any) => (
              <div key={label} className="rounded-xl border border-border-soft p-3">
                <div className="tile-label">{label}</div>
                <div className={`text-xl font-semibold mt-1 tabular-nums ${v >= 0.9 ? "text-ok" : v >= 0.7 ? "text-warn" : "text-crit"}`}>{Math.round(v * 100)}%</div>
              </div>
            ))}
          </div>
          <div className="mt-3 flex flex-wrap gap-1.5">
            <span className="chip text-ink-muted">AML review {dq.aml.by_status.review || 0}</span>
            <span className={`chip ${dq.aml.by_status.blocked ? "text-crit" : "text-ink-muted"}`}>blocked {dq.aml.by_status.blocked || 0}</span>
            <span className={`chip ${dq.aml.total_hits ? "text-warn" : "text-ink-muted"}`}>watchlist hits {dq.aml.total_hits}</span>
          </div>
        </Card>
      )}

      {/* Agent quality and adaptive autonomy */}
      <Card className="mb-5" data-testid="provenance-agent-quality">
        <div className="flex flex-wrap items-center justify-between gap-2 mb-3">
          <div className="font-semibold text-ink flex items-center gap-2"><Activity size={15} className="text-ink-muted" /> Agent quality & autonomy</div>
          <button className="btn-outline text-xs" data-testid="provenance-run-evaluation" disabled={evaluating} onClick={runEval}>
            {evaluating ? "Evaluating…" : nav.say ? "Ask Astra to evaluate" : "Run evaluation"}
          </button>
        </div>
        {!evals?.length ? (
          <span className="chip text-ink-muted">no agent scored yet</span>
        ) : (
          <div className="grid gap-2 cq-sm:grid-cols-2 cq-lg:grid-cols-3">
            {evals.map((e) => (
              <div key={e.agent_key} className="rounded-xl border border-border-soft p-3">
                <div className="flex items-center justify-between gap-2">
                  <span className="text-sm font-medium text-ink">{titleCase(e.agent_key)}</span>
                  <span className={`chip ${GRADE_STYLE[e.grade] ?? GRADE_STYLE.unrated}`}>{titleCase(e.grade)}</span>
                </div>
                <div className="mt-2 flex items-center gap-2">
                  <div className="h-1.5 flex-1 rounded-full bg-border-soft overflow-hidden">
                    <div className={`h-full ${e.quality_score >= 0.75 ? "bg-ok" : e.quality_score >= 0.5 ? "bg-warn" : "bg-crit"}`} style={{ width: `${e.quality_score * 100}%` }} />
                  </div>
                  <span className="text-xs text-ink-muted tabular-nums">{Math.round(e.quality_score * 100)}</span>
                </div>
                <div className="mt-2 flex flex-wrap gap-1">
                  <span className="chip text-ink-muted tabular-nums">{e.sample_size} decided</span>
                  <span className="chip text-ink-muted tabular-nums">{Math.round((e.metrics?.dismiss_rate || 0) * 100)}% dismissed</span>
                  {e.metrics?.high_flags ? <span className="chip text-crit tabular-nums">{e.metrics.high_flags} high flags</span> : null}
                </div>
              </div>
            ))}
          </div>
        )}
        {changes?.length > 0 && (
          <div className="mt-4 pt-3 border-t border-border-soft">
            <div className="tile-label mb-2 flex items-center gap-1"><TrendingDown size={12} /> Adaptive autonomy changes</div>
            <div className="space-y-1.5">
              {changes.slice(0, 5).map((c, i) => (
                <div key={i} className="text-xs text-ink-soft flex flex-wrap items-start gap-1.5" title={c.reason || undefined}>
                  <span className="chip bg-crit-bg text-crit">{c.automatic ? "Auto" : "Manual"}</span>
                  <span className="chip text-ink-soft">{titleCase(c.agent_key)}: {titleCase(c.from_tier || "")} → {c.paused ? "Paused" : titleCase(c.to_tier || "")}</span>
                  {c.reason && <span className="chip text-ink-muted">{c.reason}</span>}
                </div>
              ))}
            </div>
          </div>
        )}
      </Card>

      <div className="grid gap-5 cq-lg:grid-cols-3">
        <div className="min-w-0 cq-lg:col-span-2">
          <div className="tile-label mb-3">Decision ledger</div>
          {loading ? (
            <Spinner />
          ) : !ledger?.length ? (
            <Card><Empty>No ledger entries yet.</Empty></Card>
          ) : (
            <div className="space-y-2">
              {ledger.map((e) => (
                <Card key={e.seq} className="p-0 overflow-hidden">
                  <button onClick={() => setExpanded(expanded === e.seq ? null : e.seq)} className="w-full text-left p-4 flex items-center gap-3">
                    <span className="h-8 min-w-[2rem] px-1 rounded-lg bg-border-soft text-ink-soft flex items-center justify-center text-xs font-semibold tabular-nums">
                      #{e.seq}
                    </span>
                    <div className="flex-1 min-w-0">
                      <div className="flex flex-wrap items-center gap-2">
                        <span className="chip bg-border-soft text-ink-soft">{titleCase(e.event_type)}</span>
                        {e.agent_key && <span className="text-xs text-ink-muted">{titleCase(e.agent_key)}</span>}
                      </div>
                      <div className="text-sm text-ink mt-1 truncate">
                        {e.content?.recommendation || e.content?.reviewed_agent || e.actor || "—"}
                      </div>
                    </div>
                    <div className="text-right shrink-0">
                      <div className="text-xs text-ink-muted font-mono">{e.entry_hash.slice(0, 10)}…</div>
                      <div className="text-xs text-ink-muted" title={formatDateFull(e.created_at)}>
                        {e.actor || "agent"}{e.created_at ? ` · ${timeAgo(e.created_at)}` : ""}
                      </div>
                    </div>
                  </button>
                  {expanded === e.seq && (
                    <div className="border-t border-border-soft p-4 bg-paper text-xs space-y-3">
                      <div className="flex items-center gap-2 text-ink-muted font-mono break-all">
                        <Link2 size={12} className="shrink-0" /> prev {e.prev_hash?.slice(0, 20)}…
                      </div>
                      {(e.content?.recommendation || e.content?.action || e.content?.finding) && (
                        <dl className="rounded-lg bg-surface border border-border-soft p-3 text-ink-soft grid grid-cols-[auto_1fr] gap-x-3 gap-y-1">
                          {e.content.recommendation && <><dt className="tile-label">Proposed</dt><dd>{e.content.recommendation}</dd></>}
                          {e.content.action && <><dt className="tile-label">Decision</dt><dd>{titleCase(e.content.action)}{e.content.note ? ` · "${e.content.note}"` : ""}</dd></>}
                          {e.content.finding && <><dt className="tile-label">Finding</dt><dd>{e.content.finding}</dd></>}
                          {e.content.subject && <><dt className="tile-label">Client</dt><dd className="text-ink-muted">{e.content.subject}</dd></>}
                          {e.content.tier && <><dt className="tile-label">Autonomy</dt><dd className="text-ink-muted">{titleCase(e.content.tier)}</dd></>}
                        </dl>
                      )}
                      <details className="text-ink-muted">
                        <summary className="cursor-pointer text-[11px] hover:text-ink">Raw record</summary>
                        <pre className="mt-2 bg-surface rounded-lg p-3 overflow-x-auto text-ink-soft border border-border-soft whitespace-pre-wrap">
                          {JSON.stringify(e.content, null, 2)}
                        </pre>
                      </details>
                    </div>
                  )}
                </Card>
              ))}
            </div>
          )}
        </div>

        <div className="min-w-0">
          <div className="tile-label mb-3">Conduct surveillance</div>
          {/* An all-clear is a compliance assertion. Only ever render it from a response
              that actually arrived — never while loading, and never on failure. */}
          {flagsLoading ? (
            <Card><Spinner label="Checking surveillance flags…" /></Card>
          ) : flagsError ? (
            <Card>
              <ErrorState
                what="surveillance flags"
                message={`${flagsError} — this is not an all-clear. Re-check before relying on it.`}
                onRetry={refetchFlags}
              />
            </Card>
          ) : !flags?.length ? (
            <Card>
              <div className="flex items-center gap-2 text-sm text-ok">
                <CheckCircle2 size={16} /> No open flags.
              </div>
              <div className="mt-2"><span className="chip text-ink-muted">reviewed on every decision</span></div>
            </Card>
          ) : (
            <div className="space-y-2">
              {flags.map((f) => (
                <Card key={f.id}>
                  <div className="flex flex-wrap items-center justify-between gap-1.5 mb-1">
                    <div className="flex flex-wrap items-center gap-1.5">
                      <SeverityBadge severity={f.severity} />
                      {f.escalated && (
                        <span className="chip bg-warn-bg text-warn text-[11px]">
                          <ArrowUpRight size={10} /> Escalated → {f.escalated_to}
                        </span>
                      )}
                      {f.resolved && (
                        <span className="chip bg-ok-bg text-ok text-[11px]">
                          <CheckCircle2 size={10} /> Resolved by {f.resolved_by}
                        </span>
                      )}
                    </div>
                    <span className="text-xs text-ink-muted">{titleCase(f.category)}</span>
                  </div>
                  <div className="text-sm text-ink-soft">{f.finding}</div>
                  {f.auto_paused_agent && (
                    <div className="mt-2"><span className="chip bg-crit-bg text-crit"><AlertTriangle size={11} /> auto-paused {titleCase(f.target_agent_key || "")}</span></div>
                  )}
                  {f.resolution_note && (
                    <div className="mt-1.5 text-xs text-ink-muted italic">Note: {f.resolution_note}</div>
                  )}

                  {/* Resolve / escalate actions (only for open, unresolved flags) */}
                  {!f.resolved && (
                    <>
                      {resolving === f.id ? (
                        <div className="mt-3 space-y-2">
                          <textarea
                            className="input text-xs" rows={2}
                            placeholder="Resolution note (optional)…"
                            value={resolveNote}
                            onChange={(e) => setResolveNote(e.target.value)}
                          />
                          <div className="flex gap-2">
                            <button className="btn-primary text-xs py-1 px-2" disabled={flagBusy} onClick={() => resolveFlag(f)}>
                              {flagBusy ? "…" : nav.say ? "Ask Astra" : "Mark resolved"}
                            </button>
                            <button className="btn-ghost text-xs py-1 px-2" onClick={() => { setResolving(null); setResolveNote(""); }}>Cancel</button>
                          </div>
                        </div>
                      ) : escalating === f.id ? (
                        <div className="mt-3 space-y-2">
                          <input
                            className="input text-xs" placeholder="Escalate to (name or email)…"
                            value={escalateTo} onChange={(e) => setEscalateTo(e.target.value)}
                          />
                          <textarea
                            className="input text-xs" rows={2}
                            placeholder="Escalation note (optional)…"
                            value={escalateNote} onChange={(e) => setEscalateNote(e.target.value)}
                          />
                          <div className="flex gap-2">
                            <button className="btn-primary text-xs py-1 px-2" disabled={flagBusy || !escalateTo.trim()} onClick={() => escalateFlag(f)}>
                              {flagBusy ? "…" : nav.say ? "Ask Astra" : "Escalate"}
                            </button>
                            <button className="btn-ghost text-xs py-1 px-2" onClick={() => { setEscalating(null); setEscalateTo(""); setEscalateNote(""); }}>Cancel</button>
                          </div>
                        </div>
                      ) : (
                        <div className="mt-2 flex flex-wrap items-center gap-2">
                          <button className="btn-outline text-xs py-0.5 px-2" data-testid="surveillance-resolve" onClick={() => { setResolving(f.id); setEscalating(null); }}>
                            <CheckCircle2 size={12} /> Resolve
                          </button>
                          {f.severity === "high" && !f.escalated && (
                            <button className="btn-outline text-xs py-0.5 px-2 text-warn" data-testid="surveillance-escalate" onClick={() => { setEscalating(f.id); setResolving(null); }}>
                              <ArrowUpRight size={12} /> Escalate
                            </button>
                          )}
                        </div>
                      )}
                    </>
                  )}
                </Card>
              ))}
            </div>
          )}
        </div>
      </div>
    </div>
  );
}
