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
        {(result?.designation_issues ?? []).map((i: any, n: number) => <div key={n}><span className="chip text-crit">{i.account}: {i.issue}{i.name ? `, ${i.name}` : ""}</span></div>)}
        {events.slice(0, 3).map((e) => <div key={e.id}><span className="chip text-ink-muted">{said(e.kind)} {reviewDay(e.happened_on)}</span></div>)}
      </div>
    </div>
  );
}
