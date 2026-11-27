import { reviewDay } from "./ReviewsCard";
import { AdviceHoldChips, HeldHouseholdChips, RetentionChip, RiskBasisChips } from "./AdvicePracticeCard";

/** B-175: the advice file. Figures and chips: what was recommended, where it stands, and the implementation rate. */

export const ADVICE_CATEGORY: Record<string, string> = {
  protection: "Protection", investment: "Investment", retirement: "Retirement", estate: "Estate", tax: "Tax",
  cash_debt: "Cash and debt", other: "Other",
};

const STATUS_TONE: Record<string, string> = {
  implemented: "text-ok", accepted: "text-info", declined: "text-warn", open: "text-ink-muted",
};

export function AdviceStatusChip({ status, on }: { status: string; on?: string | null }) {
  return <span className={`chip ${STATUS_TONE[status] ?? "text-ink-muted"}`} data-testid="advice-status">{status}{on ? ` ${reviewDay(on)}` : ""}</span>;
}

/** Implemented over made, as a percentage; none made is no rate, never 0%. */
export function adviceRate(rate: number | null | undefined): string {
  return rate === null || rate === undefined ? "no rate" : `${Math.round(rate * 100)}%`;
}

/** The overall figures and each type's, as chips. */
export function ImplementationChips({ implementation }: { implementation: any }) {
  const o = implementation?.overall ?? {};
  const by: any[] = implementation?.by_category ?? [];
  return (
    <div className="space-y-1.5" data-testid="advice-rate">
      <div className="flex flex-wrap gap-1.5">
        <span className="chip text-ink">{adviceRate(o.rate)} implemented</span>
        <span className="chip text-ink-muted">{o.made ?? 0} made</span>
        <span className="chip text-ok">{o.implemented ?? 0} implemented</span>
        <span className="chip text-info">{o.accepted ?? 0} accepted</span>
        <span className="chip text-warn">{o.declined ?? 0} declined</span>
        <span className="chip text-ink-muted">{o.open ?? 0} open</span>
      </div>
      {by.length > 0 && (
        <div className="flex flex-wrap gap-1.5">
          {by.map((c) => <span key={c.category} className="chip text-ink-muted">{ADVICE_CATEGORY[c.category] ?? c.category} {c.implemented}/{c.made} · {adviceRate(c.rate)}</span>)}
        </div>
      )}
    </div>
  );
}

function One({ r }: { r: any }) {
  return (
    <div className="space-y-1.5" data-testid="advice-row">
      <div className="flex flex-wrap items-center gap-1.5">
        <span className="chip text-ink">{ADVICE_CATEGORY[r.category] ?? r.category}</span>
        <AdviceStatusChip status={r.status} on={r.status_on} />
        {r.overdue && <span className="chip text-crit">overdue {reviewDay(r.due_on)}</span>}
        {r.revisit_due && <span className="chip text-warn">revisit due {reviewDay(r.revisit_on)}</span>}
      </div>
      <div className="text-sm text-ink">{r.recommendation}</div>
      <div className="flex flex-wrap gap-1.5">
        {r.person && <span className="chip text-ink-muted">{r.person}</span>}
        <span className="chip text-ink-muted">owner {r.owner ?? "none"}</span>
        <span className="chip text-ink-muted">made {reviewDay(r.recommended_on)}</span>
        {r.due_on && !r.overdue && <span className="chip text-ink-muted">due {reviewDay(r.due_on)}</span>}
        {r.revisit_on && !r.revisit_due && <span className="chip text-ink-muted">revisit {reviewDay(r.revisit_on)}</span>}
        <span className="chip text-ink-muted" title="The firm's assumption set when it was recorded">assumptions {r.assumption_version}</span>
        <span className="chip text-ink-muted">{(r.alternatives ?? []).length} alternative{(r.alternatives ?? []).length === 1 ? "" : "s"}</span>
        {r.evidence_kind && <span className="chip text-ink-muted" title={r.evidence ?? undefined}>{r.evidence_kind}</span>}
        <RiskBasisChips r={r} />
      </div>
      {(r.history ?? []).length > 1 && (
        <div className="flex flex-wrap gap-1.5" data-testid="advice-history">
          {r.history.map((h: any) => <span key={h.seq} className="chip text-ink-muted" title={h.evidence ?? undefined}>{h.seq}. {h.status} {reviewDay(h.happened_on)}</span>)}
        </div>
      )}
    </div>
  );
}

export function AdviceFileCard({ result }: { result: any }) {
  if (!Array.isArray(result?.recommendations)) {
    return (
      <div className="overflow-hidden rounded-lg border border-border bg-surface" data-testid="advice-card">
        <div className="border-b border-border-soft px-3 py-2 text-sm font-medium text-ink">Advice file · {result?.household}</div>
        <div className="px-3 py-2"><One r={result} /></div>
      </div>
    );
  }
  const recs: any[] = result.recommendations;
  const imp = result.implementation;
  return (
    <div className="overflow-hidden rounded-lg border border-border bg-surface" data-testid="advice-card">
      <div className="flex flex-wrap items-center justify-between gap-2 border-b border-border-soft px-3 py-2">
        <span className="text-sm font-medium text-ink">Advice file{result.household ? ` · ${result.household}` : ""}</span>
        {imp?.since && <span className="font-mono text-xs text-ink-muted">{reviewDay(imp.since)} – {reviewDay(imp.until)}</span>}
      </div>
      <div className="space-y-2 px-3 py-2">
        <ImplementationChips implementation={imp} />
        {(result.pre_advice || result.retention) && (
          <div className="flex flex-wrap gap-1.5"><AdviceHoldChips gate={result.pre_advice} /><RetentionChip retention={result.retention} /></div>
        )}
        <HeldHouseholdChips held={result.held} />
        {recs.length === 0 ? <span className="chip text-ink-muted">none on file</span> : recs.slice(0, 3).map((r) => <One key={r.id} r={r} />)}
        {recs.length > 3 && <span className="chip text-ink-muted">+{recs.length - 3} more</span>}
      </div>
    </div>
  );
}
