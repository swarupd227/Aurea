"use client";

import { useEffect, useMemo, useRef, useState } from "react";
import RecommendationCard from "@/components/RecommendationCard";
import { SkeletonList, Empty, ErrorState } from "@/components/ui";
import { useApi } from "@/lib/hooks";
import { useViewNav } from "./ViewNav";
import { ViewHeader, chip } from "./ViewHeader";

const STATUSES = [
  { key: "open", label: "Open" },
  { key: "all", label: "All" },
];

/** The agents' proposals, for a human decision. `focusId` (the `rec` query) brings one proposal to
 * the top, opened - a Needs-you row or a conversation card lands on the proposal it named. */
export function RecommendationsView({ focusId }: { focusId?: string | null }) {
  const nav = useViewNav();
  const [status, setStatus] = useState("open");
  const { data, loading, error, refetch } = useApi<any[]>(`/api/studio/feed?status=${status}`, [status]);

  const rows = useMemo(() => {
    const list = data || [];
    if (!focusId) return list;
    const hit = list.find((r) => String(r.id) === focusId);
    return hit ? [hit, ...list.filter((r) => r !== hit)] : list;
  }, [data, focusId]);
  const focusMissing = !!focusId && !loading && !!data && !rows.some((r) => String(r.id) === focusId);

  // A proposal asked for by id may already be decided: look in all of them before saying so.
  useEffect(() => {
    if (focusMissing && status === "open") setStatus("all");
  }, [focusMissing, status]);

  const top = useRef<HTMLDivElement>(null);
  useEffect(() => {
    if (focusId && !loading) top.current?.scrollIntoView({ block: "nearest" });
  }, [focusId, loading]);

  const open = (data || []).filter((r) => r.status === "proposed").length;

  return (
    <div className="view-container" ref={top}>
      <ViewHeader
        title="Recommendations"
        meta={data ? <span className={chip} data-testid="recs-count">{open} open{status === "all" ? ` · ${data.length} shown` : ""}</span> : null}
        actions={
          <div role="tablist" aria-label="Which proposals" className="flex overflow-hidden rounded-lg border border-border text-sm">
            {STATUSES.map((s) => (
              <button
                key={s.key}
                role="tab"
                aria-selected={status === s.key}
                onClick={() => setStatus(s.key)}
                className={`px-3 py-1.5 transition-colors ${status === s.key ? "bg-border-soft text-ink" : "text-ink-muted hover:text-ink"}`}
              >
                {s.label}
              </button>
            ))}
          </div>
        }
      />
      {loading ? (
        <SkeletonList count={3} />
      ) : error ? (
        <div className="rounded-lg border border-border bg-surface p-8">
          <ErrorState what="recommendations" message={error} onRetry={refetch} />
        </div>
      ) : !rows.length ? (
        <div className="rounded-lg border border-border bg-surface p-8"><Empty>No recommendations.</Empty></div>
      ) : (
        <div className="space-y-3">
          {focusMissing && status === "all" && (
            <div className="rounded-lg border border-border bg-surface px-3 py-2 text-xs text-ink-muted" role="status">
              Proposal not found
            </div>
          )}
          {rows.map((r) => (
            <RecommendationCard
              key={r.id}
              rec={r}
              defaultOpen={String(r.id) === focusId}
              onDecided={() => {
                refetch();
                nav.changed();
              }}
            />
          ))}
        </div>
      )}
    </div>
  );
}
