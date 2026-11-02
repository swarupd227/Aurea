"use client";

import { Activity, ListChecks } from "lucide-react";
import { Card, ErrorState, SkeletonList } from "@/components/ui";
import { useApi } from "@/lib/hooks";
import { titleCase } from "@/lib/format";
import { ViewHeader } from "./ViewHeader";

/** `/api/studio/capacity` (backend `atlas/history.capacity_outcomes`). */
interface CapacityOutcomes {
  total_agent_runs: number;
  recommendations_by_status: Record<string, number>;
  open_items: number;
  decisions_made: number;
  estimated_hours_reclaimed: number;
}

/** `/api/provenance/evaluations`: the latest score per agent. */
interface AgentEvaluation {
  agent_key: string;
  quality_score: number;
  grade: string;
  sample_size?: number;
  computed_at?: string | null;
}

// Outcome bars: tokens, not hand-picked hex, and no yellow (it means only agent working, primary, focus).
const STATUS_BAR: Record<string, string> = {
  proposed: "bg-warn", approved: "bg-ok", modified: "bg-info", executed: "bg-ok",
  dismissed: "bg-ink-faint", rolled_back: "bg-warn", expired: "bg-border",
};

const muted = "chip bg-border-soft text-ink-muted";

/** Reclaimed capacity, recommendation outcomes and agent health across the firm. Read only. */
export function CapacityView() {
  const { data: cap, loading, error, refetch } = useApi<CapacityOutcomes>("/api/studio/capacity");
  const { data: evals } = useApi<AgentEvaluation[]>("/api/provenance/evaluations");
  if (error) return <div className="view-container"><Card><ErrorState what="capacity" message={error} onRetry={refetch} /></Card></div>;
  if (loading || !cap) return <div className="view-container"><SkeletonList count={3} /></div>;

  const byStatus = cap.recommendations_by_status || {};
  const total = Object.values(byStatus).reduce((s, n) => s + n, 0) || 1;
  const healthy = (evals || []).filter((e) => e.grade === "healthy").length;
  const tiles: [string, string | number, string, string | null][] = [
    ["Capacity reclaimed", `${cap.estimated_hours_reclaimed}h`, "text-ok", "estimate"],
    ["Agent runs", cap.total_agent_runs, "text-ink", null],
    ["Decisions made", cap.decisions_made, "text-ink", "approved · modified · dismissed · executed"],
    ["Open items", cap.open_items, cap.open_items ? "text-warn" : "text-ink-muted", "awaiting a person"],
  ];

  return (
    <div className="view-container">
      <ViewHeader title="Capacity & outcomes" />

      <div className="mb-6 grid grid-cols-2 gap-3 cq-md:grid-cols-4" data-testid="capacity-summary">
        {tiles.map(([label, value, tone, note]) => (
          <Card key={label} className="min-w-0 p-4">
            <div className="tile-label">{label}</div>
            <div className={`mt-1 font-mono text-2xl tabular-nums ${tone}`}>{value}</div>
            {note && <div className="mt-1.5"><span className={note === "estimate" ? "chip bg-warn-bg text-warn" : muted}>{note}</span></div>}
          </Card>
        ))}
      </div>

      <div className="grid gap-5 cq-md:grid-cols-2">
        <Card data-testid="capacity-outcomes">
          <div className="mb-4 flex items-center gap-2 font-semibold text-ink"><ListChecks size={17} /> Recommendation outcomes</div>
          <div className="space-y-2.5">
            {Object.entries(byStatus).filter(([, n]) => n > 0).map(([status, n]) => (
              <div key={status}>
                <div className="mb-0.5 flex justify-between text-sm">
                  <span className="text-ink-soft">{titleCase(status)}</span>
                  <span className="tabular-nums text-ink-muted">{n}</span>
                </div>
                <div className="h-2 overflow-hidden rounded-full bg-border-soft">
                  <div className={`h-full ${STATUS_BAR[status] || "bg-ink-faint"}`} style={{ width: `${(n / total) * 100}%` }} />
                </div>
              </div>
            ))}
          </div>
        </Card>

        <Card data-testid="capacity-agent-health">
          <div className="mb-4 flex items-center gap-2 font-semibold text-ink"><Activity size={17} /> Agent health</div>
          {evals?.length ? (
            <>
              <div className="mb-3 flex items-end gap-2">
                <span className="text-3xl font-semibold tabular-nums text-ink">{healthy}</span>
                <span className="mb-1 text-sm text-ink-muted">/ {evals.length} agents healthy</span>
              </div>
              <div className="space-y-1.5">
                {evals.map((e) => (
                  <div key={e.agent_key} className="flex items-center gap-2 text-sm">
                    <span className="w-32 shrink-0 truncate text-ink-soft cq-sm:w-44" title={titleCase(e.agent_key)}>{titleCase(e.agent_key)}</span>
                    <div className="h-1.5 flex-1 overflow-hidden rounded-full bg-border-soft">
                      <div className={`h-full ${e.quality_score >= 0.75 ? "bg-ok" : e.quality_score >= 0.5 ? "bg-warn" : "bg-crit"}`} style={{ width: `${e.quality_score * 100}%` }} />
                    </div>
                    <span className="w-7 text-right text-xs tabular-nums text-ink-muted">{Math.round(e.quality_score * 100)}</span>
                  </div>
                ))}
              </div>
            </>
          ) : (
            <div className="text-sm text-ink-muted">No agent has been scored yet.</div>
          )}
        </Card>
      </div>
    </div>
  );
}
