"use client";

import { Card, Empty, ErrorState, SkeletonList } from "@/components/ui";
import { useApi } from "@/lib/hooks";
import { money } from "@/lib/format";
import { reviewDay } from "@/app/workspace/components/artifacts/ReviewsCard";
import { ViewHeader } from "./ViewHeader";

const th = "px-4 py-2 font-medium";
export const stageTone: Record<string, string> = {
  complete: "text-ok", received: "text-ok", in_transit: "text-ink-muted", submitted: "text-ink-muted", requested: "text-ink-muted",
  rejected: "text-critical", debit_open: "text-warn",
};

/** Transfers into the firm: where each stands, whose move it is, rejects by code, and what is late. */
export function TransfersView() {
  const { data, loading, error, refetch } = useApi<any>("/api/transfer-files", []);
  if (error) return <div className="view-container"><Card><ErrorState what="transfers" message={error} onRetry={refetch} /></Card></div>;
  if (loading || !data) return <div className="view-container"><SkeletonList count={4} /></div>;
  const open = data.transfers.filter((t: any) => t.stage !== "complete").length;
  return (
    <div className="view-container">
      <ViewHeader title="Transfers in" />
      <div className="mb-4 grid grid-cols-2 gap-3 sm:grid-cols-4" data-testid="transfers-summary">
        {[["Transfers", data.transfers.length], ["Open", open], ["Late", data.late], ["Rejects", data.rejects_by_code.reduce((n: number, r: any) => n + r.count, 0)]].map(([label, n]) => (
          <Card key={label as string}><div className="text-[10px] uppercase tracking-wide text-ink-muted">{label}</div><div className="font-mono text-xl text-ink">{n as number}</div></Card>
        ))}
      </div>
      {data.rejects_by_code.length > 0 && (
        <Card className="mb-4"><div className="flex flex-wrap gap-1.5" data-testid="transfers-rejects">
          {data.rejects_by_code.map((r: any) => <span key={r.code} className="chip text-warn">{r.label} · {r.count}</span>)}
        </div></Card>
      )}
      {data.transfers.length === 0 ? <Empty>No transfers into the firm.</Empty> : (
        <Card className="overflow-hidden p-0"><div className="overflow-x-auto"><table className="w-full text-sm" data-testid="transfers-rows">
          <thead><tr className="text-left text-[10px] uppercase tracking-wide text-ink-muted">
            <th className={th}>Client</th><th className={th}>Type</th><th className={th}>From</th><th className={`${th} text-right`}>Amount</th>
            <th className={th}>Stage</th><th className={th}>Whose move</th><th className={th}>Days</th><th className={th}>Cost basis</th>
          </tr></thead>
          <tbody className="divide-y divide-border-soft">
            {data.transfers.map((t: any) => (
              <tr key={t.transfer_id}>
                <td className="px-4 py-2 text-ink">{t.client}</td>
                <td className="px-4 py-2 font-mono text-xs uppercase text-ink-muted">{t.type.replace(/_/g, " ")}</td>
                <td className="px-4 py-2 text-xs text-ink-muted">{t.delivering_firm ?? "—"}</td>
                <td className="px-4 py-2 text-right font-mono text-xs">{t.amount != null ? money(t.amount) : "—"}</td>
                <td className="px-4 py-2"><div className="flex flex-wrap gap-1">
                  <span className={`chip ${stageTone[t.stage] ?? ""}`}>{t.stage.replace(/_/g, " ")}</span>
                  {t.partial && <span className="chip text-warn">partial · {t.staying.length} stay</span>}
                  {t.debit_open && <span className="chip text-warn">debit {money(t.debit_amount)}</span>}
                  {t.exchanges.length > 0 && <span className="chip">1035</span>}
                </div></td>
                <td className="px-4 py-2 text-xs text-ink-muted">{t.owner ?? "—"}</td>
                <td className="px-4 py-2 font-mono text-xs text-ink-muted">{t.days_to_receive ?? "—"}</td>
                <td className="px-4 py-2 text-xs">
                  {t.cost_basis_on ? <span className="chip text-ok">{reviewDay(t.cost_basis_on)}</span>
                    : t.late.length ? <span className="chip text-critical">due {reviewDay(t.cost_basis_due)} · {t.late[0].days_late}d late</span>
                    : t.cost_basis_due ? <span className="chip">due {reviewDay(t.cost_basis_due)}</span> : <span className="text-ink-muted">—</span>}
                </td>
              </tr>
            ))}
          </tbody>
        </table></div></Card>
      )}
    </div>
  );
}
