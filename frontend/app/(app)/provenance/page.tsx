"use client";
import { useState } from "react";
import { ShieldCheck, Link2, CheckCircle2, AlertTriangle, Activity, TrendingDown, Download, ArrowUpRight } from "lucide-react";
import PageHeader from "@/components/PageHeader";
import { Card, Spinner, SeverityBadge, Empty, TierBadge } from "@/components/ui";
import { useApi } from "@/lib/hooks";
import { api } from "@/lib/api";
import { formatDateFull, timeAgo, titleCase } from "@/lib/format";

const GRADE_STYLE: Record<string, string> = {
  healthy: "bg-positive/10 text-positive",
  watch: "bg-caution/10 text-caution",
  regressed: "bg-critical/10 text-critical",
  unrated: "bg-navy-50 text-ink-muted",
};

export default function Provenance() {
  const { data: ledger, loading, refetch: refetchLedger } = useApi<any[]>("/api/provenance/ledger");
  const { data: flags, refetch: refetchFlags } = useApi<any[]>("/api/provenance/surveillance");
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
    setEvaluating(true);
    try {
      await api("/api/provenance/evaluate", { body: {} });
      refetchEvals();
      refetchChanges();
      refetchLedger();
    } finally {
      setEvaluating(false);
    }
  }
  async function resolveFlag(id: string) {
    setFlagBusy(true);
    try {
      await api(`/api/provenance/surveillance/${id}/resolve`, {
        method: "PATCH", body: { resolution_note: resolveNote || null, resolved: true },
      });
      setResolving(null); setResolveNote("");
      refetchFlags();
    } finally { setFlagBusy(false); }
  }
  async function escalateFlag(id: string) {
    if (!escalateTo.trim()) return;
    setFlagBusy(true);
    try {
      await api(`/api/provenance/surveillance/${id}/escalate`, {
        body: { escalated_to: escalateTo, escalation_note: escalateNote || null },
      });
      setEscalating(null); setEscalateTo(""); setEscalateNote("");
      refetchFlags();
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

  return (
    <div>
      <PageHeader
        title="Provenance"
        sub="Decision ledger, data quality, agent performance and conduct flags."
        actions={
          <div className="flex items-center gap-2">
            <div className="relative group">
              <button className="btn-outline">
                <Download size={16} /> Export ledger
              </button>
              <div className="absolute right-0 top-full mt-1 z-10 hidden group-hover:flex flex-col bg-surface border border-navy-200 rounded-xl shadow-lg overflow-hidden min-w-[140px]">
                <button className="px-4 py-2.5 text-sm text-left hover:bg-navy-50 text-ink" onClick={() => exportLedger("csv")}>CSV (spreadsheet)</button>
                <button className="px-4 py-2.5 text-sm text-left hover:bg-navy-50 text-ink" onClick={() => exportLedger("jsonl")}>JSONL (machine)</button>
              </div>
            </div>
            <button className="btn-outline" onClick={() => setShowReport((v) => !v)}>
              <ArrowUpRight size={16} /> Compliance report
            </button>
            <button className="btn-outline" onClick={runVerify}>
              <ShieldCheck size={16} /> Verify chain
            </button>
          </div>
        }
      />

      {verify && (
        <div className={`card p-4 mb-5 flex items-center gap-3 ${verify.valid ? "border-positive/30" : "border-critical/30"}`}>
          {verify.valid ? <CheckCircle2 className="text-positive" /> : <AlertTriangle className="text-critical" />}
          <div className="text-sm">
            <span className="font-semibold text-ink">
              {verify.valid ? "Ledger integrity verified" : `Chain broken at entry #${verify.broken_at_seq}`}
            </span>
            <span className="text-ink-muted"> · {verify.count} entries · SHA-256 hash chain</span>
          </div>
        </div>
      )}

      {showReport && (
        <Card className="mb-5 max-w-xl">
          <div className="font-semibold text-ink mb-3 flex items-center gap-2"><ArrowUpRight size={15} /> Generate compliance report</div>
          <div className="grid sm:grid-cols-2 gap-3 mb-3">
            <div><label className="label">From date</label>
              <input type="date" className="input" value={reportFrom} onChange={(e) => setReportFrom(e.target.value)} /></div>
            <div><label className="label">To date</label>
              <input type="date" className="input" value={reportTo} onChange={(e) => setReportTo(e.target.value)} /></div>
          </div>
          <div className="flex gap-2">
            <button className="btn-primary" onClick={generateReport} disabled={reportBusy || !reportFrom || !reportTo}>
              {reportBusy ? "Generating…" : "Download CSV report"}
            </button>
            <button className="btn-outline" onClick={() => setShowReport(false)}>Cancel</button>
          </div>
        </Card>
      )}

      {/* Regulatory compliance — the cited framework + recent assessments */}
      {comp?.framework && (
        <Card className="mb-5">
          <div className="flex items-center gap-2 mb-3">
            <ShieldCheck size={17} className="text-navy-600" />
            <span className="font-semibold text-ink">Regulatory compliance</span>
            <span className="chip bg-navy-50 text-ink-soft">{comp.framework.regime} · {comp.framework.name} · v{comp.framework.version}</span>
            <span className="text-xs text-ink-muted ml-auto">{comp.framework.authority} · {comp.framework.rule_count} rules</span>
          </div>
          <div className="grid grid-cols-3 gap-3 mb-3">
            {[["Clear", comp.by_status?.clear || 0, "text-positive"], ["Flagged", comp.by_status?.flags || 0, "text-caution"],
              ["Blocked", comp.by_status?.blocked || 0, "text-critical"]].map(([l, v, c]: any) => (
              <div key={l} className="rounded-lg border border-navy-100 p-2.5">
                <div className="text-[11px] uppercase tracking-wide text-ink-muted">{l}</div>
                <div className={`text-lg font-semibold tabular-nums ${c}`}>{v}</div>
              </div>
            ))}
          </div>
          <div className="divide-y divide-navy-50 max-h-[260px] overflow-y-auto">
            {(comp.assessments || []).slice(0, 12).map((a: any) => {
              const fails = (a.results || []).filter((r: any) => r.status === "fail");
              return (
                <div key={a.id} className="py-2 flex items-start gap-2 text-sm">
                  <span className={`chip shrink-0 ${a.status === "clear" ? "bg-positive/10 text-positive" : a.status === "blocked" ? "bg-critical/10 text-critical" : "bg-caution/10 text-caution"}`}>{a.status === "clear" ? "Clear" : titleCase(a.status)}</span>
                  <div className="min-w-0 flex-1">
                    <span className="text-ink-soft">{titleCase(a.agent_key)}</span>
                    {a.subject && <span className="text-ink-muted"> · {a.subject}</span>}
                    {fails.length > 0 && <div className="text-xs text-caution mt-0.5">{fails.map((r: any) => r.code).join(" · ")}</div>}
                  </div>
                </div>
              );
            })}
            {!comp.assessments?.length && <div className="py-3 text-sm text-ink-muted">No assessments yet — run an agent to generate one.</div>}
          </div>
        </Card>
      )}

      {/* Data quality (Analytics §2.5) */}
      {dq?.data_quality && (
        <Card className="mb-6">
          <div className="font-semibold text-ink mb-3 flex items-center gap-2"><Activity size={17} className="text-navy-600" /> Data quality · the brain's health</div>
          <div className="grid grid-cols-2 sm:grid-cols-5 gap-3">
            {[["Score", dq.data_quality.score], ["Completeness", dq.data_quality.completeness],
              ["Timeliness", dq.data_quality.timeliness], ["Accuracy", dq.data_quality.accuracy],
              ["Avg confidence", dq.data_quality.avg_confidence]].map(([label, v]: any) => (
              <div key={label} className="rounded-xl border border-navy-100 p-3">
                <div className="tile-label">{label}</div>
                <div className={`text-xl font-semibold mt-1 ${v >= 0.9 ? "text-positive" : v >= 0.7 ? "text-caution" : "text-critical"}`}>{Math.round(v * 100)}%</div>
              </div>
            ))}
          </div>
          <div className="text-xs text-ink-muted mt-2">
            AML: {dq.aml.by_status.review || 0} review · {dq.aml.by_status.blocked || 0} blocked · {dq.aml.total_hits} watchlist hit(s).
            Keeps agents from acting on bad numbers — analytics quality is downstream of data quality.
          </div>
        </Card>
      )}

      {/* Agent quality & adaptive autonomy */}
      <Card className="mb-6">
        <div className="flex items-center justify-between mb-3">
          <div className="font-semibold text-ink flex items-center gap-2"><Activity size={17} className="text-navy-600" /> Agent quality & autonomy</div>
          <button className="btn-outline text-xs" disabled={evaluating} onClick={runEval}>
            {evaluating ? "Evaluating…" : "Run evaluation"}
          </button>
        </div>
        {!evals?.length ? (
          <p className="text-sm text-ink-muted">Run the evaluation harness to score agents against outcomes. A quality regression narrows autonomy automatically.</p>
        ) : (
          <div className="grid sm:grid-cols-2 lg:grid-cols-3 gap-2">
            {evals.map((e) => (
              <div key={e.agent_key} className="rounded-xl border border-navy-100 p-3">
                <div className="flex items-center justify-between">
                  <span className="text-sm font-medium text-ink">{titleCase(e.agent_key)}</span>
                  <span className={`chip ${GRADE_STYLE[e.grade]}`}>{titleCase(e.grade)}</span>
                </div>
                <div className="mt-2 flex items-center gap-2">
                  <div className="h-1.5 flex-1 rounded-full bg-navy-100 overflow-hidden">
                    <div className={`h-full ${e.quality_score >= 0.75 ? "bg-positive" : e.quality_score >= 0.5 ? "bg-caution" : "bg-critical"}`} style={{ width: `${e.quality_score * 100}%` }} />
                  </div>
                  <span className="text-xs text-ink-muted tabular-nums">{Math.round(e.quality_score * 100)}</span>
                </div>
                <div className="text-[11px] text-ink-muted mt-1.5">
                  {e.sample_size} decided · {Math.round((e.metrics?.dismiss_rate || 0) * 100)}% dismissed
                  {e.metrics?.high_flags ? ` · ${e.metrics.high_flags} high flag(s)` : ""}
                </div>
              </div>
            ))}
          </div>
        )}
        {changes?.length > 0 && (
          <div className="mt-4 pt-3 border-t border-navy-100">
            <div className="tile-label mb-2 flex items-center gap-1"><TrendingDown size={12} /> Adaptive autonomy changes</div>
            <div className="space-y-1.5">
              {changes.slice(0, 5).map((c, i) => (
                <div key={i} className="text-xs text-ink-soft flex items-start gap-2">
                  <span className="chip bg-critical/10 text-critical">{c.automatic ? "Auto" : "Manual"}</span>
                  <span>{titleCase(c.agent_key)}: {titleCase(c.from_tier || "")} → {c.paused ? "Paused" : titleCase(c.to_tier || "")} · {c.reason}</span>
                </div>
              ))}
            </div>
          </div>
        )}
      </Card>

      <div className="grid lg:grid-cols-3 gap-5">
        <div className="lg:col-span-2">
          <div className="text-sm font-semibold text-ink mb-3">Decision ledger</div>
          {loading ? (
            <Spinner />
          ) : !ledger?.length ? (
            <Card><Empty>No ledger entries yet.</Empty></Card>
          ) : (
            <div className="space-y-2">
              {ledger.map((e) => (
                <Card key={e.seq} className="p-0 overflow-hidden">
                  <button onClick={() => setExpanded(expanded === e.seq ? null : e.seq)} className="w-full text-left p-4 flex items-center gap-3">
                    <span className="h-8 w-8 rounded-lg bg-navy-50 text-navy-700 flex items-center justify-center text-xs font-semibold tabular-nums">
                      #{e.seq}
                    </span>
                    <div className="flex-1 min-w-0">
                      <div className="flex items-center gap-2">
                        <span className="chip bg-navy-100 text-navy-800">{titleCase(e.event_type)}</span>
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
                    <div className="border-t border-navy-100 p-4 bg-navy-50/40 text-xs space-y-3">
                      <div className="flex items-center gap-2 text-ink-muted font-mono">
                        <Link2 size={12} /> hash chain: prev {e.prev_hash?.slice(0, 20)}…
                      </div>
                      {/* Plain-language summary for compliance readers */}
                      {(e.content?.recommendation || e.content?.action || e.content?.finding) && (
                        <div className="rounded-lg bg-white border border-navy-100 p-3 text-ink-soft">
                          {e.content.recommendation && <div><span className="font-medium text-ink">Proposed: </span>{e.content.recommendation}</div>}
                          {e.content.action && <div><span className="font-medium text-ink">Decision: </span>{titleCase(e.content.action)}{e.content.note ? ` — "${e.content.note}"` : ""}</div>}
                          {e.content.finding && <div><span className="font-medium text-ink">Finding: </span>{e.content.finding}</div>}
                          {e.content.subject && <div className="text-ink-muted mt-1">Client: {e.content.subject}</div>}
                          {e.content.tier && <div className="text-ink-muted">Autonomy: {titleCase(e.content.tier)}</div>}
                        </div>
                      )}
                      <details className="text-ink-muted">
                        <summary className="cursor-pointer text-[11px] hover:text-ink">Raw technical record (for audit)</summary>
                        <pre className="mt-2 bg-surface rounded-lg p-3 overflow-x-auto text-ink-soft border border-navy-100 whitespace-pre-wrap">
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

        <div>
          <div className="text-sm font-semibold text-ink mb-3">Conduct surveillance</div>
          {!flags?.length ? (
            <Card>
              <div className="flex items-center gap-2 text-sm text-positive">
                <CheckCircle2 size={16} /> No open flags.
              </div>
              <p className="text-xs text-ink-muted mt-1">
                The surveillance agent reviews every recommendation for suitability, conduct and
                fair-treatment risk, and can auto-pause an agent on a high-severity outlier.
              </p>
              <p className="text-[11px] text-ink-muted mt-2 flex items-center gap-1">
                Last reviewed: on every recommendation decision · <span className="font-medium">automated, continuous</span>
              </p>
            </Card>
          ) : (
            <div className="space-y-2">
              {flags.map((f) => (
                <Card key={f.id}>
                  <div className="flex items-center justify-between mb-1">
                    <div className="flex items-center gap-2">
                      <SeverityBadge severity={f.severity} />
                      {f.escalated && (
                        <span className="chip bg-caution/10 text-caution text-[11px]">
                          <ArrowUpRight size={10} /> Escalated → {f.escalated_to}
                        </span>
                      )}
                      {f.resolved && (
                        <span className="chip bg-positive/10 text-positive text-[11px]">
                          <CheckCircle2 size={10} /> Resolved by {f.resolved_by}
                        </span>
                      )}
                    </div>
                    <span className="text-xs text-ink-muted">{titleCase(f.category)}</span>
                  </div>
                  <p className="text-sm text-ink-soft">{f.finding}</p>
                  {f.auto_paused_agent && (
                    <div className="mt-2 text-xs text-critical flex items-center gap-1">
                      <AlertTriangle size={12} /> Auto-paused {titleCase(f.target_agent_key || "")}
                    </div>
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
                            <button className="btn-primary text-xs py-1 px-2" disabled={flagBusy} onClick={() => resolveFlag(f.id)}>
                              {flagBusy ? "…" : "Mark resolved"}
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
                            <button className="btn-primary text-xs py-1 px-2" disabled={flagBusy || !escalateTo.trim()} onClick={() => escalateFlag(f.id)}>
                              {flagBusy ? "…" : "Escalate"}
                            </button>
                            <button className="btn-ghost text-xs py-1 px-2" onClick={() => { setEscalating(null); setEscalateTo(""); setEscalateNote(""); }}>Cancel</button>
                          </div>
                        </div>
                      ) : (
                        <div className="mt-2 flex items-center gap-2">
                          <button className="btn-outline text-xs py-0.5 px-2" onClick={() => { setResolving(f.id); setEscalating(null); }}>
                            <CheckCircle2 size={12} /> Resolve
                          </button>
                          {f.severity === "high" && !f.escalated && (
                            <button className="btn-outline text-xs py-0.5 px-2 text-caution border-caution/40" onClick={() => { setEscalating(f.id); setResolving(null); }}>
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
