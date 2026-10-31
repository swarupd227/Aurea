"use client";

import { useState } from "react";
import { ChevronRight, FileText } from "lucide-react";
import { Card, Empty, ErrorState, SkeletonList, StatusBadge } from "@/components/ui";
import { useApi } from "@/lib/hooks";
import { ViewLink } from "./ViewNav";
import { ViewHeader, chip } from "./ViewHeader";

const FILTERS: [string, string][] = [["all", "All"], ["draft", "Draft"], ["client_ready", "With clients"]];

/** What can happen to a report next, as a chip. A draft reaches a client only through its proposal,
 * so one without an open proposal says what became of it. */
export function ReportState({ report }: { report: any }) {
  if (report.awaiting_decision) return <span className="chip bg-warn-bg text-warn" data-testid="report-awaiting">Awaiting decision</span>;
  if (report.status !== "draft") return null;
  const label = report.proposal_status === "dismissed" ? "Dismissed"
    : report.proposal_status === "rolled_back" ? "Withdrawn" : "No proposal";
  return <span className={chip} data-testid="report-no-proposal">{label}</span>;
}

/** Client reports, newest first. A report opens in the work pane when the list is there. */
export function ReportsView() {
  const { data, loading, error, refetch } = useApi<any[]>("/api/engage/reports");
  const [filter, setFilter] = useState("all");
  const rows = (data || []).filter((r) => filter === "all" || r.status === filter);
  const drafts = (data || []).filter((r) => r.status === "draft").length;
  const waiting = (data || []).filter((r) => r.awaiting_decision).length;
  return (
    <div className="view-container">
      <ViewHeader
        title="Client reports"
        meta={data ? (
          <>
            <span className={chip}>{data.length} reports</span>
            <span className={chip}>{drafts} draft</span>
            {waiting > 0 && <span className="chip bg-warn-bg text-warn" data-testid="reports-awaiting">{waiting} awaiting decision</span>}
          </>
        ) : null}
        actions={
          <div className="flex overflow-hidden rounded border border-border text-sm">
            {FILTERS.map(([key, label]) => (
              <button key={key} data-testid={`reports-filter-${key}`} aria-pressed={filter === key} onClick={() => setFilter(key)}
                className={`px-3 py-1.5 ${filter === key ? "bg-border-soft text-ink" : "text-ink-muted hover:text-ink"}`}>{label}</button>
            ))}
          </div>
        }
      />
      {loading ? (
        <SkeletonList count={4} />
      ) : error ? (
        <Card><ErrorState what="reports" message={error} onRetry={refetch} /></Card>
      ) : !rows.length ? (
        <Card><Empty>No reports</Empty></Card>
      ) : (
        <ul className="divide-y divide-border-soft overflow-hidden rounded-lg border border-border bg-surface" data-testid="reports-list">
          {rows.map((r) => (
            <li key={r.id}>
              <ViewLink href={`/studio/reports/${r.id}`} data-testid="report-row"
                className="group flex items-center gap-3 px-4 py-3 transition-colors hover:bg-border-soft/60 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-inset focus-visible:ring-accent">
                <span aria-hidden className="flex h-9 w-9 shrink-0 items-center justify-center rounded bg-border-soft text-ink-soft"><FileText size={16} /></span>
                <span className="min-w-0 flex-1">
                  <span className="block truncate font-medium text-ink">{r.title}</span>
                  <span className="mt-0.5 block truncate text-xs text-ink-muted">{r.household_name}{r.period ? ` · ${r.period}` : ""}</span>
                </span>
                <span className="hidden cq-sm:inline-flex"><ReportState report={r} /></span>
                <StatusBadge status={r.status} />
                <ChevronRight size={16} className="shrink-0 text-ink-faint transition-colors group-hover:text-ink" aria-hidden />
              </ViewLink>
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}
