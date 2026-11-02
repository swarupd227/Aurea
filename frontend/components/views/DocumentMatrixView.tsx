"use client";

import { Card, ErrorState, SkeletonList } from "@/components/ui";
import { useApi } from "@/lib/hooks";
import { ViewHeader } from "./ViewHeader";

const th = "px-4 py-2 font-medium";

/** The documents and detail each registration needs, what each custodian adds, and the states that allow each tenancy. */
export function DocumentMatrixView() {
  const { data, loading, error, refetch } = useApi<any>("/api/document-matrix", []);
  if (error) return <div className="view-container"><Card><ErrorState what="the document matrix" message={error} onRetry={refetch} /></Card></div>;
  if (loading || !data) return <div className="view-container"><SkeletonList count={4} /></div>;
  const custodians = Object.entries(data.custodians as Record<string, string[]>);
  return (
    <div className="view-container">
      <ViewHeader title="Document matrix" />
      <Card className="mb-4"><div className="flex flex-wrap gap-1.5" data-testid="matrix-rules">
        <span className="chip">Signed within {data.stale_days} days</span>
        <span className="chip">Tenancy by the entirety: {data.states.tenancy_by_the_entirety.join(" ")}</span>
        <span className="chip">Community property: {data.states.community_property.join(" ")}</span>
        {custodians.length === 0
          ? <span className="chip text-ink-muted">No custodian forms recorded</span>
          : custodians.map(([c, forms]) => <span key={c} className="chip">{c}: {forms.join(", ") || "none"}</span>)}
      </div></Card>
      <Card className="overflow-hidden p-0"><div className="overflow-x-auto"><table className="w-full text-sm" data-testid="matrix-rows">
        <thead><tr className="text-left text-[10px] uppercase tracking-wide text-ink-muted"><th className={th}>Registration</th><th className={th}>Documents</th><th className={th}>Detail</th></tr></thead>
        <tbody className="divide-y divide-border-soft">
          {data.registrations.map((r: any) => (
            <tr key={r.registration_type}>
              <td className="px-4 py-2 text-ink">{r.registration_type.replace(/_/g, " ")}</td>
              <td className="px-4 py-2"><div className="flex flex-wrap gap-1">{r.documents.map((d: any) => <span key={d.label} className="chip" title={d.any_of.join(" or ")}>{d.label}</span>)}</div></td>
              <td className="px-4 py-2"><div className="flex flex-wrap gap-1">{r.fields.length ? r.fields.map((f: string) => <span key={f} className="chip text-ink-muted">{f}</span>) : <span className="text-xs text-ink-muted">—</span>}</div></td>
            </tr>
          ))}
        </tbody>
      </table></div></Card>
    </div>
  );
}
