"use client";

import { Card, Empty, ErrorState, SkeletonList } from "@/components/ui";
import { useApi } from "@/lib/hooks";
import { money } from "@/lib/format";
import { reviewDay } from "@/app/workspace/components/artifacts/ReviewsCard";
import { ViewHeader } from "./ViewHeader";

const th = "px-4 py-2 font-medium";
const MONEY = new Set(["in", "out", "total", "amount", "threshold"]);
const statusTone: Record<string, string> = { open: "text-warn", reported: "text-ok", closed: "text-ink-muted" };

/** Transaction monitoring: open alerts, investigations and when each report is due. Compliance and operations only. */
export function MonitoringView() {
  const { data, loading, error, refetch } = useApi<any>("/api/aml-monitoring", []);
  if (error) return <div className="view-container"><Card><ErrorState what="transaction monitoring" message={error} onRetry={refetch} /></Card></div>;
  if (loading || !data) return <div className="view-container"><SkeletonList count={4} /></div>;
  const s = data.summary;
  const open = data.alerts.filter((a: any) => !a.decision);
  return (
    <div className="view-container">
      <ViewHeader title="Transaction monitoring" />
      <div className="mb-4 grid grid-cols-2 gap-3 sm:grid-cols-4" data-testid="monitoring-summary">
        {[["Open alerts", s.open_alerts], ["Of households with movements, alerted", s.alert_rate_pct != null ? `${s.alert_rate_pct}%` : "—"],
          ["Open investigations", s.open_investigations], ["Reports due in 7 days", s.reports_due_7_days]].map(([label, n]) => (
          <Card key={label as string}><div className="text-[10px] uppercase tracking-wide text-ink-muted">{label}</div><div className="font-mono text-xl text-ink">{n as any}</div></Card>
        ))}
      </div>
      <Card className="mb-4 overflow-hidden p-0">
        <div className="border-b border-border-soft px-4 py-2 text-sm font-medium text-ink">Investigations</div>
        {data.investigations.length === 0 ? <div className="px-4 py-3"><Empty>No investigations.</Empty></div> : (
          <div className="overflow-x-auto"><table className="w-full text-sm" data-testid="monitoring-investigations">
            <thead><tr className="text-left text-[10px] uppercase tracking-wide text-ink-muted"><th className={th}>Household</th><th className={th}>Status</th><th className={th}>Detected</th><th className={th}>Report due</th><th className={th}>Steps</th></tr></thead>
            <tbody className="divide-y divide-border-soft">
              {data.investigations.map((i: any) => (
                <tr key={i.id}>
                  <td className="px-4 py-2 text-ink">{i.household}</td>
                  <td className="px-4 py-2"><span className={`chip ${statusTone[i.status] ?? ""}`}>{i.status}</span></td>
                  <td className="px-4 py-2 font-mono text-xs text-ink-muted">{reviewDay(i.detected_on)}</td>
                  <td className="px-4 py-2 text-xs">
                    {i.reported_on ? <span className="chip text-ok">{i.clock.regime?.report ?? "report"} {i.report_reference}</span>
                      : i.clock.due ? <span className={`chip ${i.clock.days_left <= 7 ? "text-critical" : "text-warn"}`}>{reviewDay(i.clock.due)} · {i.clock.days_left}d</span>
                      : <span className="chip text-ink-muted">{i.clock.regime ? "suspicion not recorded" : "no clock for this market"}</span>}
                  </td>
                  <td className="px-4 py-2"><div className="flex flex-wrap gap-1">{i.steps.map((st: any, n: number) => <span key={n} className="chip text-ink-muted">{st.step.replace(/_/g, " ")}</span>)}</div></td>
                </tr>
              ))}
            </tbody>
          </table></div>
        )}
      </Card>
      <Card className="overflow-hidden p-0">
        <div className="border-b border-border-soft px-4 py-2 text-sm font-medium text-ink">Alerts</div>
        {data.alerts.length === 0 ? <div className="px-4 py-3"><Empty>No alerts.</Empty></div> : (
          <div className="overflow-x-auto"><table className="w-full text-sm" data-testid="monitoring-alerts">
            <thead><tr className="text-left text-[10px] uppercase tracking-wide text-ink-muted"><th className={th}>Household</th><th className={th}>Scenario</th><th className={th}>Figures</th><th className={th}>When</th><th className={th}>Decision</th></tr></thead>
            <tbody className="divide-y divide-border-soft">
              {[...open, ...data.alerts.filter((a: any) => a.decision)].map((a: any) => (
                <tr key={a.key}>
                  <td className="px-4 py-2 text-ink">{a.household}</td>
                  <td className="px-4 py-2 text-xs text-ink">{a.label}</td>
                  <td className="px-4 py-2"><div className="flex flex-wrap gap-1">{Object.entries(a.figures).filter(([, v]) => v != null).map(([k, v]) => <span key={k} className="chip font-mono text-[11px]">{k.replace(/_/g, " ")} {MONEY.has(k) ? money(v as number) : String(v)}</span>)}</div></td>
                  <td className="px-4 py-2 font-mono text-xs text-ink-muted">{reviewDay(a.last_on)}</td>
                  <td className="px-4 py-2">{a.decision ? <span className={`chip ${a.decision.decision === "escalated" ? "text-warn" : "text-ink-muted"}`}>{a.decision.decision}</span> : <span className="chip text-critical">open</span>}</td>
                </tr>
              ))}
            </tbody>
          </table></div>
        )}
      </Card>
    </div>
  );
}
