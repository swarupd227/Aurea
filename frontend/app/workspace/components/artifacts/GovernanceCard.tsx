"use client";

import { useState } from "react";
import { api } from "@/lib/api";

// India row 14: the governance register, the licence map and counsel's review of each law figure. Figures and chips only: the
// agent's message carries the sentences, and every source is a chip's title.

const POLICY_TONE: Record<string, string> = { current: "text-ok", due_soon: "text-warn", overdue: "text-crit", not_recorded: "text-ink-muted" };
const POLICY_TEXT: Record<string, string> = { current: "Current", due_soon: "Review due soon", overdue: "Review overdue", not_recorded: "Not recorded" };
const COUNSEL_TONE: Record<string, string> = { not_reviewed: "text-ink-muted", confirmed: "text-ok", corrected: "text-crit", not_confirmed: "text-warn", changed_since: "text-warn", awaiting_budget: "text-warn" };
const TIER_TEXT: Record<string, string> = { tier_1: "Tier 1", tier_2: "Tier 2", tier_3: "Tier 3" };

/** What counsel said about a law figure, beside the figure. "Not reviewed" is the default and says so. */
export function CounselChip({ counsel }: { counsel?: any }) {
  if (!counsel) return null;
  const detail = counsel.state === "awaiting_budget" ? `Awaiting the Budget since ${counsel.marked_on}: ${counsel.watch_reason}` : counsel.reviewer ? `${counsel.reviewer}, ${counsel.reviewed_on}${counsel.note ? `: ${counsel.note}` : ""}` : "Counsel has not read this figure against its source";
  return <span className={`chip ${COUNSEL_TONE[counsel.state] ?? "text-ink-muted"}`} title={detail} data-testid="counsel-chip">{counsel.label}</span>;
}

/** Each policy the firm recorded, and where its review stands. */
export function PolicyRows({ policies }: { policies: any[] }) {
  return (
    <div className="divide-y divide-border-soft" data-testid="governance-policies">
      {policies.map((p) => (
        <div key={p.kind} className="flex flex-wrap items-center gap-1.5 px-3 py-2" data-testid="governance-policy">
          <span className="mr-auto text-sm text-ink" title={p.citation}>{p.label}</span>
          <span className={`chip ${POLICY_TONE[p.state]}`} data-testid="policy-state">{POLICY_TEXT[p.state]}</span>
          {p.version && <span className="chip text-ink-muted">v{p.version}</span>}
          {p.owner && <span className="chip text-ink-muted">{p.owner}</span>}
          {p.approved_on && <span className="chip tabular-nums text-ink-muted" title="Approved">{p.approved_on}</span>}
          {p.next_review_on && (
            <span className={`chip tabular-nums ${p.days < 0 ? "text-crit" : "text-ink-muted"}`} title={p.review_set_by_document ? "Review day set by the document" : "A year after approval"}>
              {p.days < 0 ? `${-p.days} days over` : `review ${p.next_review_on}`}
            </span>
          )}
          {p.earlier_versions?.length > 0 && <span className="chip text-ink-muted">{p.earlier_versions.length} earlier</span>}
        </div>
      ))}
    </div>
  );
}

/** Which licences the firm holds, what each track lets an agent do, and which agents the licences cap. */
export function LicenceMapTable({ map }: { map: any }) {
  const capped = (map.agents ?? []).filter((a: any) => a.capped);
  return (
    <div data-testid="licence-map">
      <div className="overflow-x-auto px-3 py-2">
        <table className="w-full text-sm">
          <thead>
            <tr className="text-left"><th className="tile-label pr-3">Licence</th><th className="tile-label pr-3">Held</th><th className="tile-label pr-3">A track</th>
              <th className="tile-label pr-3 text-right">Mandates</th><th className="tile-label pr-3 text-right">Filings</th><th className="tile-label">Ceiling</th></tr>
          </thead>
          <tbody>
            {map.licences.map((l: any) => (
              <tr key={l.key} className={l.held ? "" : "text-ink-muted"} data-testid="licence-map-row">
                <td className="pr-3" title={l.citation}>{l.name}</td>
                <td className="pr-3"><span className={`chip ${l.held ? "text-ok" : "text-ink-muted"}`}>{l.held ? "Held" : "Not held"}</span></td>
                <td className="pr-3">
                  <span className="flex flex-wrap gap-1" title={`${l.allows}; ${l.plans}`}>
                    <span className="chip text-ink-muted">{l.may_execute ? "Places orders" : "No orders"}</span>
                    {l.may_execute && <span className="chip text-ink-muted">{l.per_trade_consent ? "Client consents each trade" : "Inside the mandate"}</span>}
                    <span className="chip text-ink-muted">{l.fund_plans.join(" and ")} plans</span>
                  </span>
                </td>
                <td className="pr-3 text-right tabular-nums">{l.mandates}</td>
                <td className="pr-3 text-right tabular-nums">{l.obligations}</td>
                <td>{TIER_TEXT[l.max_tier] ?? l.max_tier}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
      <div className="flex flex-wrap items-center gap-1.5 border-t border-border-soft px-3 py-2" data-testid="licence-map-agents">
        <span className="tile-label mr-auto">Agents the licences cap</span>
        <span className="chip text-ink-muted">ceiling {TIER_TEXT[map.ceiling] ?? map.ceiling ?? "none"}</span>
        <span className={`chip ${capped.length ? "text-warn" : "text-ink-muted"}`}>{capped.length} of {map.total_agents} capped</span>
      </div>
      {capped.length > 0 && (
        <div className="flex flex-wrap gap-1.5 px-3 pb-2">
          {capped.map((a: any) => (
            <span key={a.agent_key} className="chip text-warn" title={`Configured from ${a.source}`}>{a.agent}: {TIER_TEXT[a.configured]} to {TIER_TEXT[a.effective]}</span>
          ))}
        </div>
      )}
    </div>
  );
}

/** Each Indian law figure with whether counsel has read it. */
export function CounselRows({ figures, limit = 40 }: { figures: any[]; limit?: number }) {
  const [all, setAll] = useState(false);
  const shown = all ? figures : figures.slice(0, limit);
  return (
    <div className="divide-y divide-border-soft" data-testid="counsel-figures">
      {shown.map((f) => (
        <div key={f.key} className="flex flex-wrap items-center gap-1.5 px-3 py-1.5" data-testid="counsel-figure">
          <span className="mr-auto min-w-0 flex-1 text-xs text-ink-soft" title={f.source}>{f.label}</span>
          <span className="chip tabular-nums text-ink-muted" title="The figure's own as-of day">as of {f.as_of}</span>
          <CounselChip counsel={f.counsel} />
        </div>
      ))}
      {!all && figures.length > limit && (
        <button type="button" className="chip m-2 text-ink-muted" onClick={() => setAll(true)}>+{figures.length - limit} more</button>
      )}
    </div>
  );
}

/** Record a policy from its document. Compliance only. */
export function PolicyForm({ onDone }: { onDone: () => void }) {
  const [kind, setKind] = useState("grievance");
  const [version, setVersion] = useState("");
  const [owner, setOwner] = useState("");
  const [approvedOn, setApprovedOn] = useState("");
  const [nextReview, setNextReview] = useState("");
  const [document, setDocument] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const submit = async () => {
    setBusy(true);
    setError(null);
    try {
      await api("/api/licences/governance/policies", { body: {
        kind, version, owner, approved_on: approvedOn, document, next_review_on: nextReview || null } });
      setVersion(""); setOwner(""); setApprovedOn(""); setNextReview(""); setDocument("");
      onDone();
    } catch (e: any) {
      setError(e?.message ?? "Could not record it");
    } finally {
      setBusy(false);
    }
  };
  return (
    <div className="flex flex-wrap items-end gap-2 px-3 py-2" data-testid="policy-form">
      <select className="input w-44" value={kind} onChange={(e) => setKind(e.target.value)} aria-label="Policy">
        <option value="grievance">Grievance redressal</option><option value="cyber">Cybersecurity</option>
        <option value="business_continuity">Business continuity</option><option value="outsourcing">Outsourcing</option>
        <option value="conflict_of_interest">Conflict of interest</option>
      </select>
      <input className="input w-20" placeholder="Version" value={version} onChange={(e) => setVersion(e.target.value)} aria-label="Version" />
      <input className="input w-48" placeholder="Owner" value={owner} onChange={(e) => setOwner(e.target.value)} aria-label="Owner" />
      <input className="input w-36" type="date" value={approvedOn} onChange={(e) => setApprovedOn(e.target.value)} aria-label="Approved on" />
      <input className="input w-36" type="date" value={nextReview} onChange={(e) => setNextReview(e.target.value)} aria-label="Next review, if the document sets one" />
      <input className="input min-w-[12rem] flex-1" placeholder="The approved policy, and where it is filed" value={document} onChange={(e) => setDocument(e.target.value)} aria-label="Document" />
      <button type="button" className="btn-outline" data-testid="policy-record" disabled={busy || !version || !owner || !approvedOn || !document} onClick={submit}>Record</button>
      {error && <span className="chip text-crit">{error}</span>}
    </div>
  );
}

/** The governance register, the licence map or counsel's review: a tool's result, or what recording one did. */
export function GovernanceCard({ result }: { result: any }) {
  if (result.applies === false) {
    return <div className="rounded-lg border border-border bg-surface px-3 py-2" data-testid="governance-card"><span className="chip text-ink-muted">Not a firm with an Indian licence</span></div>;
  }
  const policies = result.policies?.policies ?? (Array.isArray(result.policies) ? result.policies : null);
  const counts = result.policies?.counts ?? result.counts ?? {};
  const figures = result.counsel?.figures ?? result.figures;
  return (
    <div className="overflow-hidden rounded-lg border border-border bg-surface" data-testid="governance-card">
      <div className="flex flex-wrap items-center justify-between gap-2 border-b border-border-soft px-3 py-2">
        <div className="text-sm font-medium text-ink">Governance</div>
        <div className="flex flex-wrap gap-1.5">
          {counts.overdue > 0 && <span className="chip text-crit">{counts.overdue} overdue</span>}
          {counts.due_soon > 0 && <span className="chip text-warn">{counts.due_soon} due soon</span>}
          {counts.not_recorded > 0 && <span className="chip text-ink-muted">{counts.not_recorded} not recorded</span>}
          {figures && <span className="chip text-ink-muted">{(figures as any[]).filter((f) => f.counsel?.state === "not_reviewed").length} of {(figures as any[]).length} figures not reviewed</span>}
        </div>
      </div>
      {result.map && <LicenceMapTable map={result.map} />}
      {policies && <PolicyRows policies={policies} />}
      {figures && <CounselRows figures={figures} limit={12} />}
    </div>
  );
}
