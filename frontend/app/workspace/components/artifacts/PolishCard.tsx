"use client";

import { money } from "@/lib/format";

// India polish row (B-247, B-259, B-261, B-262): reminders raised as tasks, a PMS performance fee line, what an onboarding case holds, and a
// conversion at majority. Figures and chips only; the agent's message carries the sentences and each source is a chip's title.

const STATE_TONE: Record<string, string> = { overdue: "text-crit", due_soon: "text-warn", upcoming: "text-ink-muted" };
const STATE_TEXT: Record<string, string> = { overdue: "Overdue", due_soon: "Due soon", upcoming: "Upcoming" };

/** Each date that is overdue or within 14 days, its owner, and whether its task exists. */
export function ReminderRows({ reminders }: { reminders: any[] }) {
  return (
    <div className="divide-y divide-border-soft" data-testid="india-reminders">
      {reminders.map((r, i) => (
        <div key={i} className="flex flex-wrap items-center gap-1.5 px-3 py-1.5" title={r.source} data-testid="india-reminder">
          <span className="mr-auto text-sm text-ink">{r.label}</span>
          {r.household && <span className="chip text-ink-muted">{r.household}</span>}
          <span className={`chip tabular-nums ${STATE_TONE[r.state] ?? "text-ink-muted"}`}>{STATE_TEXT[r.state] ?? r.state} {r.due_on}</span>
          <span className="chip text-ink-muted">{r.owner ?? "Unassigned"}</span>
          <span className={`chip ${r.task_exists ? "text-ok" : "text-warn"}`}>{r.task_exists ? "Task raised" : "No task yet"}</span>
        </div>
      ))}
    </div>
  );
}

/** A PMS performance fee as its own line on a bill. */
export function FeeLine({ line }: { line: any }) {
  return (
    <div className="flex flex-wrap items-center gap-1.5 px-3 py-2" data-testid="pms-fee-line">
      <span className="mr-auto text-sm text-ink">{line.mandate}</span>
      <span className="chip text-ink-muted tabular-nums">{line.period_start} to {line.period_end}</span>
      <span className="chip text-ok tabular-nums" title={`Confirmed by ${line.confirmed_by}`}>{money(line.amount, line.currency)}</span>
      <span className="chip text-ink-muted">Outside the fee cap</span>
      <span className="chip text-ink-muted">Not debited here</span>
    </div>
  );
}

/** Reminders, a fee line, an onboarding case's residence and guardian, or a conversion: whichever the result carries. */
export function PolishCard({ result }: { result: any }) {
  if (result.applies === false) {
    return <div className="rounded-lg border border-border bg-surface px-3 py-2" data-testid="polish-card"><span className="chip text-ink-muted">Not an Indian firm</span></div>;
  }
  const reminders: any[] | null = Array.isArray(result.reminders) ? result.reminders : Array.isArray(result.tasks) ? result.tasks.map((t: any) => ({
    label: t.title, household: t.household, due_on: t.due_on, state: "upcoming", owner: t.to, task_exists: true })) : null;
  return (
    <div className="overflow-hidden rounded-lg border border-border bg-surface" data-testid="polish-card">
      <div className="flex flex-wrap items-center justify-between gap-2 border-b border-border-soft px-3 py-2">
        <div className="text-sm font-medium text-ink">
          {reminders ? "Dates that need a task" : result.line ? "Performance fee line" : result.recorded ? "Recorded from the case" : result.case ? "Onboarding case" : "Minors and their guardians"}
        </div>
        <div className="flex flex-wrap gap-1.5">
          {result.to_raise != null && <span className="chip text-warn">{result.to_raise} without a task</span>}
          {result.raised != null && <span className="chip text-ok">{result.raised} raised</span>}
          {result.already != null && typeof result.already === "number" && <span className="chip text-ink-muted">{result.already} already had one</span>}
        </div>
      </div>
      {reminders && <ReminderRows reminders={reminders} />}
      {result.line && <FeeLine line={result.line} />}
      {result.case && !result.recorded && (
        <div className="flex flex-wrap items-center gap-1.5 px-3 py-2" data-testid="case-facts">
          <span className="mr-auto text-sm text-ink">{result.case}{result.person ? ` · ${result.person}` : ""}</span>
          {result.residence && <span className="chip text-ink-muted" title={result.residence.evidence ?? ""}>
            Residence: {result.residence.facts.country_of_residence ?? "not stated"}{(result.residence.facts.accounts ?? []).length ? ` · ${result.residence.facts.accounts.join(", ")}` : ""}</span>}
          {result.guardian && <span className="chip text-ink-muted" title={result.guardian.evidence ?? ""}>Guardian: {result.guardian.guardian_name} · {String(result.guardian.relationship).replace("_", " ")}</span>}
          {(result.unread ?? []).map((u: string) => <span key={u} className="chip text-warn">{u}</span>)}
        </div>
      )}
      {result.recorded && (
        <div className="flex flex-wrap items-center gap-1.5 px-3 py-2" data-testid="case-facts-recorded">
          <span className="mr-auto text-sm text-ink">{result.case}{result.person ? ` · ${result.person}` : ""}</span>
          {result.recorded.map((k: string) => <span key={k} className="chip text-ok">{k} recorded</span>)}
          {(result.already ?? []).map((a: string) => <span key={a} className="chip text-ink-muted">{a}</span>)}
        </div>
      )}
      {Array.isArray(result.minors) && (
        <div className="flex flex-wrap items-center gap-1.5 px-3 py-2" data-testid="conversion-state">
          {result.minors.map((m: any) => (
            <span key={m.person_id} className={`chip ${m.state === "converted" ? "text-ok" : m.state === "attained_majority" ? "text-crit" : "text-ink-muted"}`}>
              {m.name} · {m.state === "converted" ? `converted ${m.converted_on}` : m.state === "attained_majority" ? "now an adult: convert" : m.state.replace("_", " ")}
            </span>
          ))}
        </div>
      )}
    </div>
  );
}
