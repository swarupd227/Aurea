"use client";

import { useState } from "react";
import { api } from "@/lib/api";

const STATUS_TONE: Record<string, string> = {
  overdue: "text-crit", at_risk: "text-warn", open: "text-ink", resolved: "text-ok", withdrawn: "text-ink-muted",
};
const STATUS_TEXT: Record<string, string> = {
  overdue: "Overdue", at_risk: "At risk", open: "Open", resolved: "Resolved", withdrawn: "Withdrawn",
};
const ACK_TONE: Record<string, string> = { acknowledged: "text-ok", ack_overdue: "text-warn", ack_due: "text-ink-muted" };
const ACK_TEXT: Record<string, string> = { acknowledged: "Acknowledged", ack_overdue: "Not acknowledged", ack_due: "Acknowledgement due" };
const SEVERITY_TONE: Record<string, string> = { block: "text-crit", flag: "text-warn", notice: "text-ink-muted" };
const SEVERITY_TEXT: Record<string, string> = { block: "Fix", flag: "Weigh", notice: "Notice" };
const SHOWN = 8;

/** The register, one complaint a row: its clock, its acknowledgement and where it stands. */
export function ComplaintRows({ complaints }: { complaints: any[] }) {
  return (
    <div className="divide-y divide-border-soft" data-testid="complaints-list">
      {complaints.slice(0, SHOWN).map((c) => (
        <div key={c.id} className="space-y-1.5 px-3 py-2" data-testid="complaint-row">
          <div className="flex flex-wrap items-center gap-1.5">
            <span className="text-sm text-ink mr-auto">{c.reference} · {c.complainant}</span>
            <span className={`chip ${STATUS_TONE[c.status]}`} data-testid="complaint-status">{STATUS_TEXT[c.status]}</span>
            {c.days_left != null && <span className={`chip tabular-nums ${c.days_left < 0 ? "text-crit" : "text-ink-muted"}`}>{c.days_left < 0 ? `${-c.days_left} days over` : `${c.days_left} days left`}</span>}
            {c.days_taken != null && <span className={`chip tabular-nums ${c.resolved_late ? "text-warn" : "text-ink-muted"}`}>{c.days_taken} days{c.resolved_late ? ", late" : ""}</span>}
            {c.outcome && <span className="chip text-ink-muted">{c.outcome.replace("_", " ")}</span>}
          </div>
          <div className="flex flex-wrap gap-1.5">
            <span className="chip text-ink-muted">{c.category_label}</span>
            <span className="chip text-ink-muted">{c.source_label}</span>
            {c.household && <span className="chip text-ink-muted">{c.household}</span>}
            <span className="chip text-ink-muted tabular-nums" title="Received">{c.received_on}</span>
            <span className="chip text-ink-muted tabular-nums" title="Due">{c.due_on}</span>
            {c.status !== "withdrawn" && <span className={`chip ${ACK_TONE[c.ack_state]}`}>{ACK_TEXT[c.ack_state]}</span>}
            {c.scores_reference && <span className="chip text-ink-muted" title="SCORES registration number">{c.scores_reference}</span>}
            {c.atr_filed && <span className="chip text-ok">Report filed</span>}
            {c.escalated_scores && <span className="chip text-warn">SCORES</span>}
            {c.escalated_odr && <span className="chip text-warn">ODR</span>}
            {c.about_advice && <span className="chip text-warn">{c.told_compliance ? "About advice, told to compliance" : "About advice"}</span>}
            {c.long_pending && <span className="chip text-crit">Over three months</span>}
          </div>
        </div>
      ))}
      {complaints.length > SHOWN && <div className="px-3 py-2"><span className="chip text-ink-muted">+{complaints.length - SHOWN} more</span></div>}
    </div>
  );
}

/** The month's Annexure C table from the register. */
export function AnnexureTable({ table }: { table: any }) {
  if (!table) return <div className="px-3 py-2"><span className="chip text-ink-muted">No register</span></div>;
  const cols: [string, string][] = [["pending_at_start", "Pending at start"], ["received", "Received"], ["resolved", "Resolved"],
    ["pending_at_end", "Pending at end"], ["pending_over_months", `Over ${table.pending_months} months`]];
  return (
    <div className="overflow-x-auto px-3 py-2" data-testid="annexure-c">
      <table className="w-full text-sm">
        <thead>
          <tr className="text-left"><th className="tile-label pr-3">{table.period}</th>
            {cols.map(([k, l]) => <th key={k} className="tile-label pr-3 text-right">{l}</th>)}
            <th className="tile-label text-right">Average days</th></tr>
        </thead>
        <tbody className="tabular-nums">
          {table.rows.map((r: any) => (
            <tr key={r.group}><td className="pr-3 text-ink">{r.label}</td>
              {cols.map(([k]) => <td key={k} className="pr-3 text-right text-ink">{r[k]}</td>)}
              <td className="text-right text-ink">{r.average_days ?? "-"}</td></tr>
          ))}
          <tr className="border-t border-border-soft"><td className="pr-3 text-ink-soft">Total</td>
            {cols.map(([k]) => <td key={k} className="pr-3 text-right text-ink-soft">{table.total[k]}</td>)}
            <td className="text-right text-ink-soft">{table.average_days ?? "-"}</td></tr>
        </tbody>
      </table>
    </div>
  );
}

/** The complaints register with its counts, or what recording a complaint or a step did. */
export function ComplaintsCard({ result }: { result: any }) {
  if (result.applies === false) {
    return <div className="rounded-lg border border-border bg-surface px-3 py-2" data-testid="complaints-card"><span className="chip text-ink-muted">Not an Indian firm</span></div>;
  }
  const c = result.counts ?? {};
  return (
    <div className="overflow-hidden rounded-lg border border-border bg-surface" data-testid="complaints-card">
      <div className="flex flex-wrap items-center justify-between gap-2 border-b border-border-soft px-3 py-2">
        <div className="text-sm font-medium text-ink">Complaints register</div>
        <div className="flex flex-wrap gap-1.5">
          {c.overdue > 0 && <span className="chip text-crit">{c.overdue} overdue</span>}
          {c.at_risk > 0 && <span className="chip text-warn">{c.at_risk} at risk</span>}
          {"open" in c && <span className="chip text-ink-muted">{c.open} open</span>}
          {"resolved" in c && <span className="chip text-ok">{c.resolved} resolved</span>}
          {c.about_advice_open > 0 && <span className="chip text-warn">{c.about_advice_open} about advice</span>}
          {result.clocks && <span className="chip text-ink-muted" title={result.clocks.ack_assumed ? "The acknowledgement period is assumed" : "The firm's own"}>
            {result.clocks.resolve_days} days{result.clocks.ack_assumed ? ", ack assumed" : ""}</span>}
        </div>
      </div>
      {Array.isArray(result.complaints) && result.complaints.length > 0 && <ComplaintRows complaints={result.complaints} />}
      {result.annexure_c && <div className="border-t border-border-soft"><AnnexureTable table={result.annexure_c} /></div>}
    </div>
  );
}

/** The advertising check's findings: each with its rule and where it is read from. It approves nothing. */
export function AdvertisingFindings({ result }: { result: any }) {
  if (result.applies === false) return <span className="chip text-ink-muted">Not an Indian firm</span>;
  return (
    <div className="space-y-1.5" data-testid="advertising-findings">
      <div className="flex flex-wrap gap-1.5">
        <span className="chip text-ink-muted">Not exhaustive, approves nothing</span>
        {(result.findings ?? []).length === 0 && <span className="chip text-ink-muted">No pattern found</span>}
      </div>
      {(result.findings ?? []).map((f: any, i: number) => (
        <div key={i} className="flex flex-wrap items-center gap-1.5">
          <span className={`chip ${SEVERITY_TONE[f.severity]}`}>{SEVERITY_TEXT[f.severity]}</span>
          <span className="text-sm text-ink" title={`${f.why} (${f.citation})`}>{f.label}</span>
          {f.matched && <span className="chip text-ink-muted">{f.matched}</span>}
        </div>
      ))}
    </div>
  );
}

export function AdvertisingCard({ result }: { result: any }) {
  return (
    <div className="overflow-hidden rounded-lg border border-border bg-surface px-3 py-2" data-testid="advertising-card">
      <div className="mb-1.5 flex flex-wrap items-center gap-1.5">
        <div className="text-sm font-medium text-ink mr-auto">Wording check</div>
        {result.kind_label && <span className="chip text-ink-muted">{result.kind_label}</span>}
        <span className="chip text-ink-muted tabular-nums">{result.length ?? result.text_length ?? 0} characters</span>
      </div>
      <AdvertisingFindings result={result} />
    </div>
  );
}

/** A box on the Licences page to read words against the check. Read-only: it writes and sends nothing. */
export function AdvertisingChecker() {
  const [text, setText] = useState("");
  const [kind, setKind] = useState("message");
  const [result, setResult] = useState<any>(null);
  const [error, setError] = useState<string | null>(null);

  async function run() {
    setError(null);
    try {
      setResult(await api("/api/licences/advertising/check", { body: { text, kind } }));
    } catch (e: any) {
      setError(e?.message ?? "Not checked");
    }
  }

  return (
    <div className="space-y-2" data-testid="advertising-checker">
      <textarea className="input w-full text-sm" rows={3} value={text} aria-label="Words to check" placeholder="Words to check"
        data-testid="advertising-text" onChange={(e) => setText(e.target.value)} />
      <div className="flex flex-wrap items-center gap-2">
        <select className="input text-sm" value={kind} aria-label="What it is" data-testid="advertising-kind" onChange={(e) => setKind(e.target.value)}>
          <option value="message">Message</option>
          <option value="report">Report</option>
          <option value="marketing">Marketing</option>
        </select>
        <button className="btn-outline" data-testid="advertising-run" disabled={text.trim().length < 3} onClick={run}>Check</button>
        {error && <span className="chip text-crit">{error}</span>}
      </div>
      {result && <AdvertisingFindings result={result} />}
    </div>
  );
}
