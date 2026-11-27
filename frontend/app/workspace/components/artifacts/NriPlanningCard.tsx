import { reviewDay } from "./ReviewsCard";

/** B-398: clients abroad, moving or returned: certificates, move checklists and the not-ordinarily-resident outlook. */

const TRC: Record<string, [string, string]> = {
  covers: ["certificate covers today", "text-ok"], expired: ["certificate expired", "text-warn"], not_on_file: ["no certificate on file", "text-warn"],
};
const ITEM: Record<string, [string, string]> = {
  open: ["open", "text-warn"], done: ["done", "text-ok"], cancelled: ["cancelled", "text-ink-muted"],
  nothing_on_file: ["nothing on file", "text-ink-muted"], task_removed: ["task removed", "text-ink-muted"],
};
const YEAR: Record<string, [string, string]> = {
  rnor: ["not ordinarily resident", "text-ok"], ordinary: ["ordinarily resident", "text-ink"], depends: ["depends on days", "text-warn"],
};
const range = (r: number[]) => (r[0] === r[1] ? `${r[0]}` : `${r[0]}–${r[1]}`);

export function Outlook({ outlook }: { outlook: any }) {
  if (!outlook) return null;
  if (outlook.state !== "laid_out") {
    return <span className="chip text-ink-muted" data-testid="rnor-outlook" title={outlook.why ?? undefined}>outlook {outlook.state.replace("_", " ")}</span>;
  }
  return (
    <div className="overflow-x-auto" data-testid="rnor-outlook">
      <table className="w-full text-xs">
        <thead><tr className="text-left text-[10px] uppercase tracking-wide text-ink-muted">
          <th className="py-1 pr-3 font-medium">Tax year</th>
          <th className="py-1 pr-3 text-right font-medium">Non-resident of 10 (≥{outlook.nonresident_line})</th>
          <th className="py-1 pr-3 text-right font-medium">Days in 7 (≤{outlook.days_line})</th><th className="py-1 font-medium">Status</th>
        </tr></thead>
        <tbody className="divide-y divide-border-soft">
          {outlook.years.map((y: any) => (
            <tr key={y.year}>
              <td className="py-1 pr-3 font-mono text-ink">{y.year}</td>
              <td className="py-1 pr-3 text-right font-mono tabular-nums text-ink-muted">{range(y.nonresident_of_ten)}</td>
              <td className="py-1 pr-3 text-right font-mono tabular-nums text-ink-muted">{range(y.days_in_seven)}</td>
              <td className="py-1"><span className={`chip ${YEAR[y.state][1]}`} title={y.basis}>{YEAR[y.state][0]}</span></td>
            </tr>
          ))}
        </tbody>
      </table>
      <div className="mt-1 flex flex-wrap gap-1.5">
        <span className="chip text-warn" title={outlook.assumed.join("; ")}>{outlook.assumed.length} assumptions</span>
      </div>
    </div>
  );
}

export function NriPerson({ p }: { p: any }) {
  const trc = TRC[p.trc];
  return (
    <div className="space-y-1.5 px-3 py-2" data-testid="nri-person">
      <div className="flex flex-wrap items-center gap-1.5">
        <span className="mr-auto text-sm text-ink">{p.name}</span>
        {p.tax_status && <span className="chip text-ink-muted">{p.tax_status} · {p.tax_year}</span>}
        {p.fema_status && <span className="chip text-ink-muted">{p.fema_status}</span>}
        {trc && <span className={`chip ${trc[1]}`}>{trc[0]}</span>}
      </div>
      {p.certificates.length > 0 && (
        <div className="flex flex-wrap gap-1.5">
          {p.certificates.map((c: any) => <span key={c.id} className="chip text-ink-muted" title={`${c.reference} · ${c.document}`}>{c.kind === "trc" ? "TRC" : "10F"} {c.country} to {reviewDay(c.valid_to)}</span>)}
        </div>
      )}
      {p.moves.map((m: any) => (
        <div key={m.id} className="space-y-1" data-testid="nri-move">
          <div className="flex flex-wrap items-center gap-1.5">
            <span className="chip text-ink">{m.label} · {m.country}</span>
            <span className="chip text-ink-muted">{m.planned ? "planned " : ""}{reviewDay(m.moved_on)}</span>
            <span className={`chip tabular-nums ${m.open ? "text-warn" : "text-ok"}`}>{m.open}/{m.tasks} tasks open</span>
            <span className="chip text-ink-muted">rules assumed</span>
          </div>
          <div className="divide-y divide-border-soft">
            {m.items.map((i: any) => (
              <div key={i.key} className="flex flex-wrap items-center gap-1.5 py-1">
                <span className="mr-auto text-xs text-ink" title={i.applies_to.join("; ")}>{i.label}</span>
                <span className={`chip ${(ITEM[i.state] ?? ["", "text-ink-muted"])[1]}`}>{(ITEM[i.state] ?? [i.state])[0]}</span>
              </div>
            ))}
          </div>
        </div>
      ))}
      <Outlook outlook={p.outlook} />
    </div>
  );
}

export function NriPlanningCard({ result }: { result: any }) {
  if (!result?.applies) return null;
  const households: any[] = result.households ?? [result];
  const people = households.flatMap((h) => (h.people ?? []).map((p: any) => ({ ...p, household: h.household })));
  return (
    <div className="overflow-hidden rounded-lg border border-border bg-surface" data-testid="nri-card">
      <div className="flex flex-wrap items-center justify-between gap-2 border-b border-border-soft px-3 py-2">
        <span className="text-sm font-medium text-ink">NRI planning{result.household ? ` · ${result.household}` : ""}</span>
        <span className="chip text-ink-muted">treaty relief not modelled</span>
      </div>
      {people.length === 0 ? <div className="px-3 py-2"><span className="chip text-ink-muted">nothing on file</span></div> : (
        <div className="divide-y divide-border-soft">
          {people.slice(0, 4).map((p: any) => <NriPerson key={p.person_id} p={p} />)}
          {people.length > 4 && <div className="px-3 py-2"><span className="chip text-ink-muted">+{people.length - 4} more</span></div>}
        </div>
      )}
    </div>
  );
}
