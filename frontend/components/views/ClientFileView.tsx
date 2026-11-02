"use client";

import { Card, Empty, ErrorState, SkeletonList } from "@/components/ui";
import { useApi } from "@/lib/hooks";
import { reviewDay } from "@/app/workspace/components/artifacts/ReviewsCard";
import { said } from "@/app/workspace/components/artifacts/ClientFileCard";
import { ViewLink } from "./ViewNav";
import { AllClients, ViewHeader } from "./ViewHeader";

/** Trusted contacts, estate documents and life events. What goes on file is recorded through the conversation,
 * from the document. With no household it lists what is missing, client by client. */
export function ClientFileView({ household = null }: { household?: string | null }) {
  const { data, loading, error, refetch } = useApi<any>(household ? `/api/client-file?household=${household}` : "/api/client-file", [household]);
  if (error) return <div className="view-container"><Card><ErrorState what="the client file" message={error} onRetry={refetch} /></Card></div>;
  if (loading || !data || (data.household_id ?? null) !== household) return <div className="view-container"><SkeletonList count={3} /></div>;

  if (!household) {
    const s = data.summary;
    return (
      <div className="view-container">
        <ViewHeader title="Client file" />
        <div className="mb-4 grid grid-cols-2 gap-3 cq-md:grid-cols-4" data-testid="file-summary">
          {[["Households", s.households, "text-ink"], ["No trusted contact", s.with_no_trusted_contact, s.with_no_trusted_contact ? "text-warn" : "text-ink-muted"],
            ["Documents out of date", s.documents_out_of_date, s.documents_out_of_date ? "text-warn" : "text-ink-muted"], ["Life events this year", s.recent_events, "text-ink-muted"]].map(([label, v, tone]: any) => (
            <Card key={label}><div className="tile-label">{label}</div><div className={`font-mono text-2xl tabular-nums ${tone}`}>{v}</div></Card>
          ))}
        </div>
        <Card className="mb-4"><div className="flex flex-wrap gap-1.5"><span className="chip text-ink-muted">recorded from a document or note</span><span className="chip text-ink-muted">not on file does not mean none exists</span></div></Card>
        {data.households.length === 0 ? <Empty>No households.</Empty> : (
          <Card className="overflow-hidden p-0">
            <div className="overflow-x-auto">
              <table className="w-full text-sm" data-testid="file-rows">
                <thead><tr className="text-left text-[10px] uppercase tracking-wide text-ink-muted"><th className="px-4 py-2 font-medium">Client</th><th className="px-4 py-2 text-right font-medium">No contact</th><th className="px-4 py-2 text-right font-medium">Not on file</th><th className="px-4 py-2 text-right font-medium">Out of date</th><th className="px-4 py-2 text-right font-medium">Events</th></tr></thead>
                <tbody className="divide-y divide-border-soft">
                  {data.households.map((h: any) => (
                    <tr key={h.household_id}>
                      <td className="px-4 py-2"><ViewLink href={`/studio/client-file?household=${h.household_id}`} className="text-ink hover:underline">{h.household}</ViewLink></td>
                      {[h.no_trusted_contact, h.documents_not_on_file, h.out_of_date, h.recent_events].map((v: number, i: number) => <td key={i} className="px-4 py-2 text-right font-mono text-xs tabular-nums text-ink-muted">{v}</td>)}
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          </Card>
        )}
      </div>
    );
  }

  return (
    <div className="view-container">
      <ViewHeader title={`Client file · ${data.household}`} actions={<AllClients href="/studio/client-file" />} />
      {data.designation_issues.length > 0 && (
        <Card className="mb-4"><div className="flex flex-wrap gap-1.5" data-testid="file-issues">
          {data.designation_issues.map((i: any, n: number) => <span key={n} className="chip text-crit">{i.account}: {i.issue}{i.name ? `, ${i.name}` : ""}</span>)}
        </div></Card>
      )}
      <div className="mb-4 space-y-4" data-testid="file-people">
        {data.people.map((p: any) => (
          <Card key={p.person_id}>
            <div className="mb-2 text-sm font-medium text-ink">{p.name}</div>
            <div className="mb-2 flex flex-wrap gap-1.5">
              {p.trusted_contact
                ? <span className="chip text-ok" title={p.trusted_contact.document}>trusted contact {p.trusted_contact.name}, {p.trusted_contact.relationship}</span>
                : <span className="chip text-warn" data-testid="file-no-contact">no trusted contact</span>}
            </div>
            <div className="flex flex-wrap gap-1.5">
              {p.documents.map((d: any) => <span key={d.id} className={`chip ${d.out_of_date ? "text-warn" : "text-ok"}`} title={d.document}>{said(d.kind)} {reviewDay(d.signed_on)}{d.why ? ` · before ${said(d.why)}` : ""}</span>)}
              {p.not_on_file.map((k: string) => <span key={k} className="chip text-ink-muted">{said(k)} not on file</span>)}
            </div>
          </Card>
        ))}
      </div>
      <Card className="overflow-hidden p-0">
        <div className="border-b border-border-soft px-4 py-2.5 text-sm font-medium text-ink">Life events</div>
        {data.events.length === 0 ? <div className="px-4 py-3"><span className="chip text-ink-muted">none on file</span></div> : (
          <div className="divide-y divide-border-soft" data-testid="file-events">
            {data.events.map((e: any) => (
              <div key={e.id} className="flex flex-wrap items-center gap-1.5 px-4 py-2" title={e.document}>
                <span className="text-sm text-ink">{said(e.kind)}</span>
                <span className="font-mono text-xs text-ink-muted">{reviewDay(e.happened_on)}</span>
                {e.touches.map((t: string) => <span key={t} className="chip text-ink-muted">{said(t)}</span>)}
              </div>
            ))}
          </div>
        )}
      </Card>
    </div>
  );
}
