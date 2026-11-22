import { money } from "@/lib/format";

const STATE_TONE: Record<string, string> = { pass: "text-ok", fail: "text-warn", "n/a": "text-ink-muted" };
const STATE_TEXT: Record<string, string> = { pass: "on file", fail: "missing", "n/a": "not needed" };
const CAL_TONE: Record<string, string> = { overdue: "text-crit", due_soon: "text-warn", upcoming: "text-ink-muted", filed: "text-ok" };
const CAL_TEXT: Record<string, string> = { overdue: "Overdue", due_soon: "Due soon", upcoming: "Upcoming", filed: "Filed" };
const SHOWN = 6;

/** One household's items before advice, each with its state; the detail is the evidence or what is missing. */
export function PreAdviceChecks({ gate }: { gate: any }) {
  return (
    <div className="flex flex-wrap gap-1.5">
      {gate.checks.map((c: any) => (
        <span key={c.key} className={`chip ${STATE_TONE[c.state]}`} title={`${c.detail} · ${c.source}`} data-testid={`pre-advice-${c.key}`}>
          {c.label} · {STATE_TEXT[c.state]}
        </span>
      ))}
    </div>
  );
}

/** What is on file before an Indian adviser advises or bills: one household, or every household on the track. */
export function PreAdviceCard({ result }: { result: any }) {
  const gates: any[] = result.households ?? (result.applies ? [result] : []);
  const open = gates.filter((g) => !g.met).length;
  return (
    <div className="overflow-hidden rounded-lg border border-border bg-surface" data-testid="pre-advice-card">
      <div className="flex flex-wrap items-center justify-between gap-2 border-b border-border-soft px-3 py-2">
        <div className="text-sm font-medium text-ink">Before advice</div>
        <span className={`chip ${open ? "text-warn" : "text-ok"}`}>{open ? `${open} with paperwork missing` : "Nothing missing"}</span>
      </div>
      {gates.length === 0 && (
        <div className="px-3 py-2"><span className="chip text-ink-muted">Not on an adviser's track</span></div>
      )}
      <div className="divide-y divide-border-soft">
        {gates.slice(0, SHOWN).map((g) => (
          <div key={g.household_id} className="space-y-1.5 px-3 py-2">
            <div className="flex flex-wrap items-center gap-1.5">
              <span className="text-sm text-ink mr-auto">{g.household}</span>
              <span className={`chip ${g.met ? "text-ok" : "text-warn"}`}>{g.met ? "May advise" : "No advice yet"}</span>
            </div>
            <PreAdviceChecks gate={g} />
          </div>
        ))}
        {gates.length > SHOWN && <div className="px-3 py-2"><span className="chip text-ink-muted">+{gates.length - SHOWN} more</span></div>}
      </div>
    </div>
  );
}

/** A family's fee for the year against SEBI's cap. */
export function FeeCapChips({ cap }: { cap: any }) {
  const charged = Math.max((cap.billed_this_year ?? 0) + (cap.this_bill ?? 0), cap.yearly_charge ?? 0);
  return (
    <>
      <span className={`chip ${cap.over ? "text-crit" : "text-ok"}`} title={cap.basis}>
        {money(charged, "INR")} of {money(cap.cap, "INR")}
      </span>
      <span className="chip text-ink-muted">{cap.year}</span>
      {cap.over && <span className="chip text-crit">Bill held</span>}
    </>
  );
}

/** The firm's SEBI filings by period, and its registered officers. */
export function ComplianceCalendarCard({ result }: { result: any }) {
  if (!result.applies) {
    return (
      <div className="rounded-lg border border-border bg-surface px-3 py-2" data-testid="calendar-card">
        <span className="chip text-ink-muted">No Indian licence</span>
      </div>
    );
  }
  const items: any[] = result.items.filter((i: any) => i.state !== "filed");
  const c = result.counts;
  return (
    <div className="overflow-hidden rounded-lg border border-border bg-surface" data-testid="calendar-card">
      <div className="flex flex-wrap items-center justify-between gap-2 border-b border-border-soft px-3 py-2">
        <div className="text-sm font-medium text-ink">Compliance calendar</div>
        <div className="flex flex-wrap gap-1.5">
          {c.overdue > 0 && <span className="chip text-crit">{c.overdue} overdue</span>}
          {c.due_soon > 0 && <span className="chip text-warn">{c.due_soon} due soon</span>}
          <span className="chip text-ok">{c.filed} filed</span>
        </div>
      </div>
      <div className="divide-y divide-border-soft">
        {items.slice(0, SHOWN).map((i) => (
          <div key={`${i.obligation}-${i.period}`} className="flex flex-wrap items-center gap-1.5 px-3 py-1.5" title={i.citation}>
            <span className="text-sm text-ink-soft mr-auto">{i.label}</span>
            <span className="chip text-ink-muted">{i.period}</span>
            <span className="chip text-ink-muted tabular-nums">{i.due_on}</span>
            <span className={`chip ${CAL_TONE[i.state]}`}>{CAL_TEXT[i.state]}</span>
          </div>
        ))}
        {items.length > SHOWN && <div className="px-3 py-2"><span className="chip text-ink-muted">+{items.length - SHOWN} more</span></div>}
      </div>
      {result.officers_missing?.length > 0 && (
        <div className="flex flex-wrap gap-1.5 border-t border-border-soft px-3 py-2">
          {result.officers_missing.map((o: string) => <span key={o} className="chip text-warn">{o}: not recorded</span>)}
        </div>
      )}
    </div>
  );
}
