import { reviewDay } from "./ReviewsCard";

/** B-399: India's adviser operating model. Figures, tables and chips: who is held before advice, the risk profile a
 * recommendation rests on, retention, the registration line and compliance's sample audit of advice files. */

/** What holds new advice, a delivery and a plan summary for a household on the adviser's track. */
export function AdviceHoldChips({ gate }: { gate: any }) {
  if (!gate) return null;
  if (gate.met) return <span className="chip text-ok" data-testid="advice-gate" title={gate.citation}>paperwork before advice complete</span>;
  return (
    <span className="flex flex-wrap gap-1.5" data-testid="advice-gate">
      <span className="chip text-warn" title={gate.citation}>new advice held</span>
      {gate.missing.map((m: string) => <span key={m} className="chip text-warn">missing {m}</span>)}
    </span>
  );
}

/** The firm view's held households. */
export function HeldHouseholdChips({ held }: { held?: any[] }) {
  if (!held?.length) return null;
  return (
    <div className="flex flex-wrap gap-1.5" data-testid="advice-held">
      {held.map((h) => <span key={h.household_id} className="chip text-warn" title={h.missing.join("; ")}>{h.household} · new advice held</span>)}
    </div>
  );
}

export function RetentionChip({ retention }: { retention: any }) {
  if (!retention) return null;
  return (
    <span className="chip text-ink-muted" data-testid="advice-retention" title={retention.figure?.source}>
      kept at least {retention.years} years · assumed, not reviewed · nothing deleted
    </span>
  );
}

/** The risk profile a recommendation rests on, and how long its record is kept. */
export function RiskBasisChips({ r }: { r: any }) {
  const b = r.risk_basis;
  return (
    <>
      {!b ? <span className="chip text-ink-muted" data-testid="advice-risk-basis">risk profile not kept</span>
        : b.status !== "on_file" ? <span className="chip text-warn" data-testid="advice-risk-basis">no risk profile on file by {reviewDay(b.as_of)}</span>
        : <>
            <span className="chip text-ink" data-testid="advice-risk-basis" title={`${b.of}: tolerance ${b.tolerance}, capacity ${b.capacity}`}>
              risk {b.indicated} · {reviewDay(b.answered_on)}
            </span>
            {r.keep_until && (b.consent
              ? <span className="chip text-ok">consented {reviewDay(b.consent.given_on)}</span>
              : <span className="chip text-warn">profile not consented by then</span>)}
          </>}
      {r.keep_until && <span className="chip text-ink-muted">keep to {reviewDay(r.keep_until)}</span>}
    </>
  );
}

const LINE_TONE: Record<string, string> = { within: "text-ok", near: "text-warn", above: "text-crit", not_applicable: "text-ink-muted" };

export function CapacityChips({ c }: { c: any }) {
  if (!c) return null;
  return (
    <div className="flex flex-wrap gap-1.5" data-testid="advice-capacity">
      <span className={`chip tabular-nums ${LINE_TONE[c.state]}`} title={`${c.counted} · ${c.figure?.source}`}>{c.clients} of {c.line} clients</span>
      <span className="chip text-ink-muted">{c.households} households</span>
      <span className="chip text-ink-muted">line assumed, not reviewed</span>
      <span className="chip text-ink-muted">registration {c.registration ? c.registration.replace("_", "-") : "not recorded"}</span>
    </div>
  );
}

export function ResultChip({ result }: { result: any }) {
  if (!result) return <span className="chip text-ink-muted" data-testid="audit-result">not reviewed</span>;
  if (result.outcome === "met") return <span className="chip text-ok" data-testid="audit-result">no finding</span>;
  return <span className="chip text-warn" data-testid="audit-result">{result.findings.length} finding{result.findings.length === 1 ? "" : "s"}</span>;
}

export function CheckChips({ checks, items, result }: { checks: any; items: Record<string, string>; result?: any }) {
  const bad = "text-warn", fine = "text-ink-muted";
  return (
    <div className="flex flex-wrap gap-1.5">
      <span className={`chip ${["not_on_file", "not_kept"].includes(checks.risk_profile) ? bad : fine}`}>risk {checks.risk_profile.replace(/_/g, " ")}</span>
      <span className={`chip ${checks.consented_when_advised ? fine : bad}`}>{checks.consented_when_advised ? "consented" : "not consented"} when advised</span>
      <span className={`chip tabular-nums ${checks.alternatives ? fine : bad}`}>{checks.alternatives} alternative{checks.alternatives === 1 ? "" : "s"}</span>
      <span className={`chip ${fine}`}>{checks.decision}{checks.evidence_kind ? ` · ${checks.evidence_kind}` : ""}</span>
      <span className={`chip ${checks.paperwork_today === "incomplete" ? bad : fine}`}>paperwork {checks.paperwork_today.replace(/_/g, " ")}</span>
      {(result?.findings ?? []).map((f: any, i: number) => <span key={i} className="chip text-warn" title={f.words}>{items[f.item] ?? f.item}</span>)}
    </div>
  );
}

export function AuditMetricChips({ m, items }: { m: any; items: Record<string, string> }) {
  return (
    <div className="flex flex-wrap gap-1.5" data-testid="audit-metrics">
      <span className="chip tabular-nums text-ink">{m.reviewed}/{m.drawn} reviewed</span>
      <span className={`chip tabular-nums ${m.with_findings ? "text-warn" : "text-ink-muted"}`}>{m.with_findings} with findings{m.share === null ? "" : ` · ${Math.round(m.share * 100)}%`}</span>
      {m.awaiting > 0 && <span className="chip tabular-nums text-warn">{m.awaiting} awaiting</span>}
      {Object.entries(m.by_item ?? {}).map(([k, n]) => <span key={k} className="chip tabular-nums text-ink-muted">{items[k] ?? k} {n as number}</span>)}
    </div>
  );
}

export function AdviceAuditCard({ result }: { result: any }) {
  if (!result?.applies) {
    return (
      <div className="overflow-hidden rounded-lg border border-border bg-surface px-3 py-2" data-testid="advice-audit-card">
        <span className="chip text-ink-muted">Not registered as an Investment Adviser</span>
      </div>
    );
  }
  const latest = result.audits?.[0];
  return (
    <div className="overflow-hidden rounded-lg border border-border bg-surface" data-testid="advice-audit-card">
      <div className="flex flex-wrap items-center justify-between gap-2 border-b border-border-soft px-3 py-2">
        <span className="text-sm font-medium text-ink">Advice file audit</span>
        {latest && <span className="font-mono text-xs text-ink-muted">sample {latest.seq} · {reviewDay(latest.drawn_on)}</span>}
      </div>
      <div className="space-y-2 px-3 py-2">
        <CapacityChips c={result.capacity} />
        <div className="flex flex-wrap gap-1.5"><RetentionChip retention={result.retention} /></div>
        <HeldHouseholdChips held={result.pre_advice?.held} />
        {result.audits.length === 0 ? <span className="chip text-ink-muted">no sample drawn</span> : <AuditMetricChips m={result.metrics} items={result.items} />}
      </div>
      {latest && (
        <div className="divide-y divide-border-soft border-t border-border-soft">
          {latest.files.slice(0, 5).map((f: any) => (
            <div key={f.recommendation_id} className="space-y-1.5 px-3 py-2" data-testid="audit-file">
              <div className="flex flex-wrap items-center gap-1.5">
                <span className="mr-auto text-sm text-ink">{f.household}</span>
                <span className="chip text-ink-muted">{f.category.replace("_", " ")} · {reviewDay(f.recommended_on)}</span>
                <ResultChip result={f.result} />
              </div>
              <CheckChips checks={f.checks} items={result.items} result={f.result} />
            </div>
          ))}
          {latest.files.length > 5 && <div className="px-3 py-2"><span className="chip text-ink-muted">+{latest.files.length - 5} more</span></div>}
        </div>
      )}
    </div>
  );
}
