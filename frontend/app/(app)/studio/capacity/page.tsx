"use client";
import { Gauge, Clock, ListChecks, CheckCircle2, Activity } from "lucide-react";
import PageHeader from "@/components/PageHeader";
import { Card, Spinner, StatTile } from "@/components/ui";
import { useApi } from "@/lib/hooks";
import { titleCase } from "@/lib/format";

const STATUS_COLOR: Record<string, string> = {
  proposed: "#c8a35e", approved: "#1f7a55", modified: "#2a5575", executed: "#1f7a55",
  dismissed: "#9aa7b2", rolled_back: "#b9852b", expired: "#cbd5dd",
};

export default function Capacity() {
  const { data: cap, loading } = useApi<any>("/api/studio/capacity");
  const { data: evals } = useApi<any[]>("/api/provenance/evaluations");
  if (loading || !cap) return <Spinner />;

  const byStatus = cap.recommendations_by_status || {};
  const total = Object.values(byStatus).reduce((s: number, n: any) => s + n, 0) || 1;
  const healthy = (evals || []).filter((e) => e.grade === "healthy").length;

  return (
    <div>
      <PageHeader
        title="Capacity & outcomes"
        sub="Reclaimed capacity, recommendation outcomes and agent health across the branch."
      />

      <div className="grid grid-cols-2 lg:grid-cols-4 gap-4 mb-6">
        <StatTile label="Capacity reclaimed" value={`${cap.estimated_hours_reclaimed}h`} hint="Indicative time saved" accent="positive" />
        <StatTile label="Agent runs" value={cap.total_agent_runs} hint="Across the workforce" />
        <StatTile label="Decisions made" value={cap.decisions_made} hint="Approved / modified / dismissed" />
        <StatTile label="Open items" value={cap.open_items} hint="Awaiting a human" accent="gold" />
      </div>

      <div className="grid lg:grid-cols-2 gap-5">
        <Card>
          <div className="font-semibold text-ink mb-4 flex items-center gap-2"><ListChecks size={17} /> Recommendation outcomes</div>
          <div className="space-y-2.5">
            {Object.entries(byStatus).filter(([, n]: any) => n > 0).map(([status, n]: any) => (
              <div key={status}>
                <div className="flex justify-between text-sm mb-0.5">
                  <span className="text-ink-soft">{titleCase(status)}</span>
                  <span className="text-ink-muted tabular-nums">{n}</span>
                </div>
                <div className="h-2 rounded-full bg-navy-100 overflow-hidden">
                  <div className="h-full" style={{ width: `${(n / total) * 100}%`, background: STATUS_COLOR[status] || "#9aa7b2" }} />
                </div>
              </div>
            ))}
          </div>
        </Card>

        <Card>
          <div className="font-semibold text-ink mb-4 flex items-center gap-2"><Activity size={17} /> Agent health</div>
          {evals?.length ? (
            <>
              <div className="flex items-end gap-2 mb-3">
                <span className="text-3xl font-semibold text-ink">{healthy}</span>
                <span className="text-sm text-ink-muted mb-1">/ {evals.length} agents healthy</span>
              </div>
              <div className="space-y-1.5">
                {evals.map((e) => (
                  <div key={e.agent_key} className="flex items-center gap-2 text-sm">
                    <span className="text-ink-soft w-44 truncate">{titleCase(e.agent_key)}</span>
                    <div className="h-1.5 flex-1 rounded-full bg-navy-100 overflow-hidden">
                      <div className={`h-full ${e.quality_score >= 0.75 ? "bg-positive" : e.quality_score >= 0.5 ? "bg-caution" : "bg-critical"}`} style={{ width: `${e.quality_score * 100}%` }} />
                    </div>
                    <span className="text-xs text-ink-muted tabular-nums w-7 text-right">{Math.round(e.quality_score * 100)}</span>
                  </div>
                ))}
              </div>
            </>
          ) : (
            <p className="text-sm text-ink-muted">Run the evaluation harness in Provenance to populate agent health.</p>
          )}
        </Card>
      </div>

      <Card className="mt-5">
        <div className="flex items-start gap-3">
          <CheckCircle2 className="text-positive shrink-0 mt-0.5" />
          <p className="text-sm text-ink-soft">
            The workforce has handled <span className="font-semibold text-ink">{cap.total_agent_runs}</span> runs and
            reclaimed an estimated <span className="font-semibold text-ink">{cap.estimated_hours_reclaimed} hours</span> of
            preparation, monitoring and documentation — capacity that shifts from administration to relationships,
            decoupling service cost from book growth.
          </p>
        </div>
      </Card>
    </div>
  );
}
