"use client";

import { useState } from "react";
import { Card, Empty, ErrorState, SkeletonList } from "@/components/ui";
import { useApi } from "@/lib/hooks";
import { ViewLink } from "./ViewNav";
import { ViewHeader } from "./ViewHeader";

const th = "px-4 py-2 font-medium";
const days = (v: number | null | undefined) => (v == null ? "—" : `${v}d`);

/** Where each account stands and whose move it is, what is stuck by kind, each household, and the three clocks. */
export function OnboardingQueuesView() {
  const { data, loading, error, refetch } = useApi<any>("/api/onboarding/states", []);
  const [queue, setQueue] = useState<string | null>(null);
  if (error) return <div className="view-container"><Card><ErrorState what="onboarding queues" message={error} onRetry={refetch} /></Card></div>;
  if (loading || !data) return <div className="view-container"><SkeletonList count={3} /></div>;
  const c = data.clocks;
  const shown = queue ? data.queues[queue].items : Object.values(data.queues).flatMap((q: any) => q.items);
  return (
    <div className="view-container">
      <ViewHeader title="Onboarding queues" />
      <div className="mb-4 grid grid-cols-3 gap-3" data-testid="onboarding-clocks">
        {[["Paperwork", c.paperwork, "signed to open"], ["Money", c.money, "open to funded"], ["Invested", c.invested, "funded to invested"]].map(([label, s, what]: any) => (
          <Card key={label}>
            <div className="tile-label">{label} clock</div>
            <div className="font-mono text-2xl tabular-nums text-ink">{days(s.p50)}</div>
            <div className="font-mono text-xs text-ink-muted">P90 {days(s.p90)} · {s.n} accounts · {what}</div>
          </Card>
        ))}
      </div>

      <Card className="mb-4 overflow-hidden p-0">
        <div className="flex flex-wrap gap-1.5 border-b border-border-soft px-4 py-2.5" data-testid="onboarding-queue-tabs">
          <button className={`chip ${queue === null ? "text-ink" : "text-ink-muted"}`} onClick={() => setQueue(null)}>all {data.summary.exceptions}</button>
          {Object.entries(data.queues).map(([k, q]: any) => (
            <button key={k} className={`chip ${queue === k ? "text-ink" : q.items.length ? "text-warn" : "text-ink-muted"}`} onClick={() => setQueue(k)}>{q.label} {q.items.length}</button>
          ))}
        </div>
        {shown.length === 0 ? <div className="px-4 py-3"><span className="chip text-ink-muted">nothing stuck</span></div> : (
          <div className="overflow-x-auto"><table className="w-full text-sm" data-testid="onboarding-exceptions">
            <thead><tr className="text-left text-[10px] uppercase tracking-wide text-ink-muted"><th className={th}>Account</th><th className={th}>Queue</th><th className={th}>Detail</th><th className={th}>Owner</th><th className={`${th} text-right`}>Age</th></tr></thead>
            <tbody className="divide-y divide-border-soft">
              {shown.map((e: any, i: number) => (
                <tr key={i}>
                  <td className="px-4 py-2"><ViewLink href={`/studio/onboarding/${e.case_id}`} className="text-ink hover:underline">{e.account}</ViewLink></td>
                  <td className="px-4 py-2"><span className="chip text-warn">{e.label}</span></td>
                  <td className="px-4 py-2 text-xs text-ink-muted">{e.detail}</td>
                  <td className="px-4 py-2 text-xs text-ink-muted">{e.owner}</td>
                  <td className="px-4 py-2 text-right font-mono text-xs tabular-nums text-ink-muted">{days(e.age_days)}</td>
                </tr>
              ))}
            </tbody>
          </table></div>
        )}
      </Card>

      {data.accounts.length === 0 ? <Empty>No accounts in onboarding.</Empty> : (
        <Card className="overflow-hidden p-0"><div className="overflow-x-auto"><table className="w-full text-sm" data-testid="onboarding-states">
          <thead><tr className="text-left text-[10px] uppercase tracking-wide text-ink-muted"><th className={th}>Account</th><th className={th}>State</th><th className={th}>Whose move</th><th className={`${th} text-right`}>In state</th></tr></thead>
          <tbody className="divide-y divide-border-soft">
            {data.accounts.map((a: any) => (
              <tr key={a.case_id}>
                <td className="px-4 py-2"><ViewLink href={`/studio/onboarding/${a.case_id}`} className="text-ink hover:underline">{a.account}</ViewLink>
                  <div className="text-xs text-ink-muted">{(a.registration_type ?? "").replace(/_/g, " ")}</div></td>
                <td className="px-4 py-2"><span className="chip text-ink">{a.state.replace(/_/g, " ")}</span> <span className="font-mono text-xs text-ink-muted">{a.step}/{a.steps}</span></td>
                <td className="px-4 py-2 text-xs text-ink-muted">{a.owner}</td>
                <td className="px-4 py-2 text-right"><span className={`chip ${a.overdue ? "text-crit" : "text-ink-muted"}`}>{days(a.age_days)}{a.sla_days != null ? ` of ${a.sla_days}d` : ""}</span></td>
              </tr>
            ))}
          </tbody>
        </table></div></Card>
      )}
    </div>
  );
}
