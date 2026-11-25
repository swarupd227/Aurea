import { money } from "@/lib/format";

const STATE_TONE: Record<string, string> = { active: "text-ok", expired: "text-warn", revoked: "text-crit", not_started: "text-ink-muted" };
const STATE_TEXT: Record<string, string> = { active: "Active", expired: "Lapsed", revoked: "Withdrawn", not_started: "Not started" };
const SHOWN = 6;

/** Account Aggregator consents, one row each: what it covers, its range and where it stands. Anything simulated says so. */
export function AggregatorConsents({ consents }: { consents: any[] }) {
  return (
    <div className="divide-y divide-border-soft" data-testid="aggregator-list">
      {consents.slice(0, SHOWN).map((c) => (
        <div key={c.id} className="space-y-1.5 px-3 py-2" data-testid="aggregator-consent">
          <div className="flex flex-wrap items-center gap-1.5">
            <span className="text-sm text-ink mr-auto">{c.household}{c.person ? `, ${c.person}` : ""}</span>
            <span className={`chip ${STATE_TONE[c.state]}`} data-testid="aggregator-state">{STATE_TEXT[c.state]}</span>
            {c.simulated && <span className="chip text-warn" title="An invented consent from a simulated aggregator" data-testid="aggregator-simulated">simulated</span>}
            {c.days_left != null && c.state === "active" && <span className="chip text-ink-muted tabular-nums">{c.days_left} days left</span>}
            <span className="chip text-ink-muted tabular-nums">{c.lines ?? 0} lines</span>
          </div>
          <div className="flex flex-wrap gap-1.5">
            {c.fi_labels.map((l: string) => <span key={l} className="chip text-ink-muted">{l}</span>)}
            <span className="chip text-ink-muted" title={`Purpose ${c.purpose_code}`}>{c.purpose}</span>
            <span className="chip text-ink-muted tabular-nums" title="The data range">{c.data_from} to {c.data_to}</span>
            <span className="chip text-ink-muted tabular-nums" title="Lapses">{c.expires_on}</span>
            <span className="chip text-ink-muted" title={c.artefact_id}>{c.aggregator}</span>
            {c.revoked_on && <span className="chip text-crit" title={c.revoked_reason}>Withdrawn {c.revoked_on}</span>}
          </div>
        </div>
      ))}
      {consents.length > SHOWN && <div className="px-3 py-2"><span className="chip text-ink-muted">+{consents.length - SHOWN} more</span></div>}
    </div>
  );
}

/** Consolidated statements a person supplied. */
export function StatementChips({ statements }: { statements: any[] }) {
  return (
    <div className="flex flex-wrap gap-1.5 px-3 py-2" data-testid="aggregator-statements">
      {statements.slice(0, SHOWN).map((s) => (
        <span key={s.id} className="chip text-ink-muted" title={s.document}>{s.household} · {s.kind_label} · {s.statement_date} · {s.lines} lines</span>
      ))}
    </div>
  );
}

/** The Account Aggregator consents and the statements supplied, or what a record or a fetch did. */
export function AggregatorCard({ result }: { result: any }) {
  if (result.applies === false) {
    return <div className="rounded-lg border border-border bg-surface px-3 py-2" data-testid="aggregator-card"><span className="chip text-ink-muted">Not an Indian firm</span></div>;
  }
  const c = result.counts ?? {};
  return (
    <div className="overflow-hidden rounded-lg border border-border bg-surface" data-testid="aggregator-card">
      <div className="flex flex-wrap items-center justify-between gap-2 border-b border-border-soft px-3 py-2">
        <div className="text-sm font-medium text-ink">Account Aggregator</div>
        <div className="flex flex-wrap gap-1.5">
          {"active" in c && <span className="chip text-ok">{c.active} active</span>}
          {c.expired > 0 && <span className="chip text-warn">{c.expired} lapsed</span>}
          {c.revoked > 0 && <span className="chip text-crit">{c.revoked} withdrawn</span>}
          {result.added != null && (
            <span className="chip text-ink-muted tabular-nums">{result.added} added · {result.superseded ?? 0} replaced · {result.ended ?? 0} ended</span>
          )}
          {result.simulated && <span className="chip text-warn">simulated</span>}
        </div>
      </div>
      {Array.isArray(result.consents) && result.consents.length > 0 && <AggregatorConsents consents={result.consents} />}
      {Array.isArray(result.statements) && result.statements.length > 0 && <StatementChips statements={result.statements} />}
    </div>
  );
}

/** A statement just recorded: what was read, replaced, refused and left to the aggregator. */
export function EcasCard({ result }: { result: any }) {
  return (
    <div className="overflow-hidden rounded-lg border border-border bg-surface" data-testid="ecas-card">
      <div className="flex flex-wrap items-center justify-between gap-2 border-b border-border-soft px-3 py-2">
        <div className="text-sm font-medium text-ink">{result.household}, {result.kind_label}</div>
        <div className="flex flex-wrap gap-1.5">
          <span className="chip text-ink-muted tabular-nums">{result.statement_date}</span>
          <span className="chip text-ok tabular-nums">{result.added} new</span>
          <span className="chip text-ink-muted tabular-nums">{result.replaced} replaced</span>
          {result.refused?.length > 0 && <span className="chip text-warn tabular-nums">{result.refused.length} refused</span>}
          {result.skipped?.length > 0 && <span className="chip text-ink-muted tabular-nums">{result.skipped.length} left to the aggregator</span>}
        </div>
      </div>
      <div className="flex flex-wrap gap-1.5 px-3 py-2">
        <span className="chip text-ink-muted tabular-nums">Stated {money(result.total, result.currency ?? "INR")}</span>
        {(result.refused ?? []).slice(0, 4).map((r: any) => <span key={r.row} className="chip text-warn" title={r.reason}>Row {r.row}</span>)}
      </div>
    </div>
  );
}
