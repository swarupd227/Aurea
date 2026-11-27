"use client";

import { Card, Empty, ErrorState, SkeletonList } from "@/components/ui";
import { useApi } from "@/lib/hooks";
import { reviewDay } from "@/app/workspace/components/artifacts/ReviewsCard";
import { PLAN_STATE, PlanStateChip, share } from "@/app/workspace/components/artifacts/PlanRecordCard";
import { ViewLink } from "./ViewNav";
import { ViewHeader } from "./ViewHeader";

function Tile({ label, value, sub, tone = "text-ink" }: { label: string; value: string | number; sub?: string; tone?: string }) {
  return (
    <Card>
      <div className="tile-label">{label}</div>
      <div className={`font-mono text-2xl tabular-nums ${tone}`}>{value}</div>
      {sub && <div className="font-mono text-xs tabular-nums text-ink-muted">{sub}</div>}
    </Card>
  );
}

const th = "px-4 py-2 font-medium";
const num = "px-4 py-2 text-right font-mono text-xs tabular-nums text-ink-muted";

/** The firm's plans of record (B-181): coverage, freshness, the QC pass rate and scenario depth, and where each
 * household's plan stands. Each state is worked out from what is recorded; a baseline, a review and a delivery are
 * recorded from a client's Plan tab or the conversation, behind a Confirm card. An adviser sees their own households. */
export function PlansView() {
  const { data, loading, error, refetch } = useApi<any>("/api/plan-record", []);
  if (error) return <div className="view-container"><Card><ErrorState what="the plans of record" message={error} onRetry={refetch} /></Card></div>;
  if (loading || !data) return <div className="view-container"><SkeletonList count={4} /></div>;
  const { coverage: c, freshness: f, qc: q, scenario_depth: d } = data;
  const rows: any[] = data.households ?? [];
  return (
    <div className="view-container">
      <ViewHeader title="Plans of record" />
      <div className="mb-4 grid grid-cols-2 gap-3 cq-md:grid-cols-4" data-testid="plans-summary">
        <Tile label="Coverage" value={share(c.share)} sub={`${c.delivered} of ${c.active} delivered`} />
        <Tile label="Fresh" value={share(f.share)} sub={`${f.fresh} of ${f.delivered} · ${f.stale} stale`} tone={f.stale ? "text-warn" : "text-ink"} />
        <Tile label="QC pass rate" value={q.pass_rate === null ? "no reviews" : share(q.pass_rate)} sub={`${q.passed} of ${q.reviews}`} tone={q.pass_rate === null ? "text-ink-muted" : "text-ink"} />
        <Tile label="Scenario depth" value={d.median ?? "—"} sub={`median · mean ${d.mean ?? "—"} · ${d.baselines} baselines`} />
      </div>
      <Card className="mb-4">
        <div className="flex flex-wrap gap-1.5" data-testid="plans-basis">
          <span className="chip text-ink-muted">{data.scope === "own" ? "your households" : "the firm's active households"}</span>
          <span className="chip text-ink-muted">state worked out from what is recorded</span>
          <span className="chip text-ink-muted">QC {reviewDay(q.since)} – {reviewDay(q.until)}</span>
          <span className="chip text-ink-muted">{data.line?.assumed ? "complexity line not set · every plan reviewed" : `complexity line ${data.line?.line}`}</span>
          {f.cadence_assumed > 0 && <span className="chip text-ink-muted">{f.cadence_assumed} on an assumed {f.default_months}-month cadence</span>}
          <span className="chip text-ink-muted">assumptions {data.assumption_version}</span>
        </div>
      </Card>
      <Card className="mb-4">
        <div className="flex flex-wrap gap-1.5" data-testid="plans-by-state">
          {(data.states ?? []).map((s: string) => (
            <span key={s} className={`chip tabular-nums ${data.by_state[s] ? "text-ink" : "text-ink-muted"}`}>{PLAN_STATE[s] ?? s} {data.by_state[s]}</span>
          ))}
        </div>
      </Card>
      {rows.length === 0 ? <Empty>No active households.</Empty> : (
        <Card className="overflow-hidden p-0">
          <div className="overflow-x-auto">
            <table className="w-full text-sm" data-testid="plans-households">
              <thead><tr className="text-left text-[10px] uppercase tracking-wide text-ink-muted">
                <th className={th}>Client</th><th className={th}>State</th><th className={`${th} text-right`}>Version</th>
                <th className={th}>Adviser</th><th className={`${th} text-right`}>Complexity</th><th className={th}>Review</th>
                <th className={`${th} text-right`}>Missing</th><th className={`${th} text-right`}>Scenarios</th>
                <th className={th}>Delivered</th><th className={`${th} text-right`}>Open tasks</th>
              </tr></thead>
              <tbody className="divide-y divide-border-soft">
                {rows.map((r) => (
                  <tr key={r.household_id} data-testid="plans-row">
                    <td className="px-4 py-2"><ViewLink href={`/studio/plan?household=${r.household_id}`} className="text-ink hover:underline">{r.household}</ViewLink></td>
                    <td className="px-4 py-2"><PlanStateChip state={r.state} />{r.advice_held && <span className="chip ml-1.5 text-warn" data-testid="advice-gate">new advice held</span>}</td>
                    <td className={num}>{r.version || "—"}</td>
                    <td className="px-4 py-2 text-xs text-ink-muted">{r.adviser ?? "unassigned"}</td>
                    <td className={num}>{r.complexity}</td>
                    <td className="px-4 py-2 text-xs text-ink-muted">{r.review_needed ? "needed" : "not needed"}</td>
                    <td className={r.missing ? num.replace("text-ink-muted", "text-warn") : num}>{r.missing}</td>
                    <td className={num}>{r.scenarios_since_baseline}</td>
                    <td className="px-4 py-2">
                      {r.delivered_on ? <span className={`chip ${r.fresh ? "text-ok" : "text-warn"}`}>{reviewDay(r.delivered_on)} · {r.fresh ? "fresh" : "stale"}</span> : <span className="text-xs text-ink-muted">—</span>}
                    </td>
                    <td className={num}>{r.open_tasks}</td>
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
