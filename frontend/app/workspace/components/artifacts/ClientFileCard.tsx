import { reviewDay } from "./ReviewsCard";

export const said = (k: string) => k.replace(/_/g, " ");

export function ClientFileCard({ result }: { result: any }) {
  if (Array.isArray(result?.households)) {
    const s = result.summary;
    return (
      <div className="rounded-lg border border-border bg-surface px-3 py-2" data-testid="file-card">
        <div className="mb-1.5 text-sm font-medium text-ink">Client file</div>
        <div className="flex flex-wrap gap-1.5">
          <span className="chip text-warn">{s.with_no_trusted_contact} with no trusted contact</span>
          <span className={`chip ${s.documents_out_of_date ? "text-warn" : "text-ink-muted"}`}>{s.documents_out_of_date} documents out of date</span>
          <span className="chip text-ink-muted">{s.recent_events} life events this year</span>
        </div>
      </div>
    );
  }
  const people: any[] = result?.people ?? [];
  const events: any[] = result?.events ?? [];
  const gaps = result?.estate_gaps;
  return (
    <div className="overflow-hidden rounded-lg border border-border bg-surface" data-testid="file-card">
      <div className="border-b border-border-soft px-3 py-2 text-sm font-medium text-ink">Client file · {result?.household}</div>
      <div className="space-y-2 px-3 py-2">
        {people.map((p) => (
          <div key={p.person_id} className="flex flex-wrap items-center gap-1.5">
            <span className="text-sm text-ink">{p.name}</span>
            <span className={`chip ${p.trusted_contact ? "text-ok" : "text-warn"}`}>{p.trusted_contact ? `contact ${p.trusted_contact.name}` : "no trusted contact"}</span>
            {p.documents.map((d: any) => <span key={d.id} className={`chip ${d.out_of_date ? "text-warn" : "text-ok"}`}>{said(d.kind)}</span>)}
            {p.not_on_file.map((k: string) => <span key={k} className="chip text-ink-muted">{said(k)} not on file</span>)}
          </div>
        ))}
        {gaps ? <EstateGaps gaps={gaps} /> : (result?.designation_issues ?? []).map((i: any, n: number) => <div key={n}><span className="chip text-crit">{i.account}: {i.issue}{i.name ? `, ${i.name}` : ""}</span></div>)}
        {result?.task && (
          <div className="flex flex-wrap gap-1.5" data-testid="file-estate-task">
            <span className="chip text-ink">task · {result.task.assigned_to_name ?? "unassigned"}</span>
            {result.task.due_date && <span className="chip text-ink-muted">due {reviewDay(result.task.due_date)}</span>}
          </div>
        )}
        {events.slice(0, 3).map((e) => <div key={e.id}><span className="chip text-ink-muted">{said(e.kind)} {reviewDay(e.happened_on)}</span></div>)}
      </div>
    </div>
  );
}

/** B-185: an unfunded trust, a minor with no will on file, and the titling-fix list. Figures and chips only. */
export function EstateGaps({ gaps }: { gaps: any }) {
  const g = gaps.guardianship;
  const fixes: any[] = gaps.titling_fixes ?? [];
  return (
    <div className="space-y-1.5" data-testid="file-estate-gaps">
      <div className="flex flex-wrap gap-1.5">
        {(gaps.trusts ?? []).map((t: any) => (
          <span key={t.person_id} className={`chip ${t.status === "may_be_unfunded" ? "text-warn" : "text-ok"}`} title={t.document}>
            {t.person} · revocable trust · {t.status === "may_be_unfunded" ? "may be unfunded" : `${t.accounts.length} registered`}
          </span>
        ))}
        {g && (
          <span className={`chip ${g.no_will_on_file ? "text-warn" : "text-ok"}`}>
            {g.minors.map((m: any) => `${m.name} ${m.age}`).join(" · ")} · {g.no_will_on_file ? "no will on file" : `will · ${g.wills_on_file.join(", ")}`}
          </span>
        )}
        {(gaps.ages_not_on_file ?? []).length > 0 && <span className="chip text-ink-muted">age not on file · {gaps.ages_not_on_file.join(", ")}</span>}
      </div>
      {fixes.map((f: any, n: number) => (
        <div key={n} className="flex flex-wrap gap-1.5" title={f.evidence}>
          <span className="chip text-crit">{f.account}: {f.issue}{f.name ? `, ${f.name}` : ""}</span>
          {f.registration && <span className="chip text-ink-muted">{said(f.registration)}</span>}
        </div>
      ))}
      <div className="flex flex-wrap gap-1.5">
        <span className="chip text-ink-muted">not on file is not none</span>
        {(gaps.assumed ?? []).map((a: string) => <span key={a} className="chip text-ink-muted">assumed · {said(a)}</span>)}
      </div>
    </div>
  );
}
