"use client";

import { Card, Empty, ErrorState, SkeletonList } from "@/components/ui";
import { useApi } from "@/lib/hooks";
import { reviewDay } from "@/app/workspace/components/artifacts/ReviewsCard";
import { AllClients, ViewHeader } from "./ViewHeader";

const th = "px-4 py-2 font-medium";

/** Every consent a client gave or withdrew, with its evidence, what stands, and disclosures due again. */
export function ConsentsView({ household = null }: { household?: string | null }) {
  const { data, loading, error, refetch } = useApi<any>(household ? `/api/consents?household=${household}` : "/api/consents", [household]);
  if (error) return <div className="view-container"><Card><ErrorState what="consents" message={error} onRetry={refetch} /></Card></div>;
  if (loading || !data || (data.household_id ?? null) !== household) return <div className="view-container"><SkeletonList count={3} /></div>;
  return (
    <div className="view-container">
      <ViewHeader title="Consents" actions={household ? <AllClients href="/studio/consents" /> : undefined} />
      {data.standing && (
        <Card className="mb-4"><div className="flex flex-wrap gap-1.5" data-testid="consents-standing">
          {data.kinds.map((k: any) => {
            const s = data.standing[k.kind];
            return <span key={k.kind} className={`chip ${s?.status === "granted" ? "text-ok" : s ? "text-warn" : "text-ink-muted"}`}>{k.label} {s ? s.status : "not on file"}</span>;
          })}
          {data.from_agreements.map((a: any, i: number) => <span key={i} className="chip text-ok">discretion in the signed agreement</span>)}
        </div></Card>
      )}
      {data.redeliveries_due.length > 0 && (
        <Card className="mb-4"><div className="flex flex-wrap gap-1.5" data-testid="consents-redeliveries">
          {data.redeliveries_due.map((r: any, i: number) => <span key={i} className="chip text-warn">{r.account} · {r.doc_type.replace(/_/g, " ")} due {reviewDay(r.due)}</span>)}
        </div></Card>
      )}
      {data.records.length === 0 ? <Empty>No consents recorded.</Empty> : (
        <Card className="overflow-hidden p-0"><div className="overflow-x-auto"><table className="w-full text-sm" data-testid="consents-rows">
          <thead><tr className="text-left text-[10px] uppercase tracking-wide text-ink-muted">{!household && <th className={th}>Client</th>}<th className={th}>Consent</th><th className={th}>Status</th><th className={th}>Given by</th><th className={th}>When</th><th className={th}>Evidence</th></tr></thead>
          <tbody className="divide-y divide-border-soft">
            {data.records.map((r: any) => (
              <tr key={r.id}>
                {!household && <td className="px-4 py-2 text-ink">{r.client}</td>}
                <td className="px-4 py-2 text-ink">{r.label}</td>
                <td className="px-4 py-2"><span className={`chip ${r.status === "granted" ? "text-ok" : "text-warn"}`}>{r.status}</span></td>
                <td className="px-4 py-2 text-xs text-ink-muted">{r.given_by}</td>
                <td className="px-4 py-2 font-mono text-xs text-ink-muted">{reviewDay(r.given_on)} · {r.method.replace(/_/g, " ")}</td>
                <td className="px-4 py-2 text-xs text-ink-muted">{r.evidence}</td>
              </tr>
            ))}
          </tbody>
        </table></div></Card>
      )}
    </div>
  );
}
