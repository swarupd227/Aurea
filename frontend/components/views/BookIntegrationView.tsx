"use client";

import { useState } from "react";
import { GitMerge, ChevronRight, Plus } from "lucide-react";
import { Card, Spinner, StatusBadge, Empty } from "@/components/ui";
import { useApi } from "@/lib/hooks";
import { api } from "@/lib/api";
import { useViewNav, ViewLink } from "./ViewNav";
import { ViewHeader, chip } from "./ViewHeader";

/** The one source a new acquisition can be loaded from: a generated sample feed. No file import exists. */
export const SAMPLE_SOURCE_FIRM = "Northbridge Advisory";
export const START_SAMPLE_SENTENCE = `Start a new acquisition from ${SAMPLE_SOURCE_FIRM} using the sample book`;

/** Acquired books being mapped into the firm's records. In the work pane loading one is the
 * conversation's tool behind its Confirm card; on a page the button loads it directly. */
export function BookIntegrationView() {
  const nav = useViewNav();
  const { data, loading, refetch } = useApi<any[]>("/api/onboarding/book-batches");
  const [busy, setBusy] = useState(false);

  async function newBatch() {
    if (nav.say) return nav.say(START_SAMPLE_SENTENCE);
    setBusy(true);
    try {
      await api("/api/onboarding/book-batches", { body: { source_firm: SAMPLE_SOURCE_FIRM } });
      refetch();
    } finally {
      setBusy(false);
    }
  }

  const batches = data || [];
  const open = batches.filter((b) => b.status === "received" || b.status === "reconciled").length;

  return (
    <div className="view-container" data-testid="book-integration-view">
      <ViewHeader
        title="Book integration"
        meta={batches.length ? (
          <>
            <span className={`${chip} tabular-nums`}>{batches.length} batches</span>
            {open > 0 && <span className="chip bg-warn-bg text-warn tabular-nums">Open {open}</span>}
          </>
        ) : undefined}
        actions={
          <button className="btn-primary" disabled={busy} onClick={newBatch} data-testid="book-load-sample">
            <Plus size={16} /> {busy ? "Loading…" : nav.say ? "Ask Astra to load sample book" : "Load sample book"}
          </button>
        }
      />
      {loading ? (
        <Spinner />
      ) : !batches.length ? (
        <Card><Empty>No acquired books</Empty></Card>
      ) : (
        <div className="grid gap-3">
          {batches.map((b) => (
            <ViewLink key={b.id} href={`/studio/book-integration/${b.id}`} data-testid="book-batch-row"
              className="card group flex min-w-0 flex-wrap items-center gap-4 p-5 transition hover:shadow-lift">
              <div className="flex h-11 w-11 shrink-0 items-center justify-center rounded-xl border border-border text-ink-soft"><GitMerge size={18} /></div>
              <div className="min-w-[10rem] flex-1">
                <div className="font-semibold text-ink">{b.source_firm}</div>
                <div className="mt-1 flex flex-wrap gap-1.5">
                  <span className={`${chip} tabular-nums`}>{b.feed?.clients?.length || 0} clients</span>
                  <span className={`${chip} tabular-nums`}>{b.feed?.holdings?.length || 0} holdings</span>
                  {b.stats?.conflicts ? <span className="chip bg-crit-bg text-crit tabular-nums">{b.stats.conflicts} conflicts</span> : null}
                </div>
              </div>
              <StatusBadge status={b.status} />
              <ChevronRight className="text-ink-muted group-hover:text-ink" />
            </ViewLink>
          ))}
        </div>
      )}
    </div>
  );
}
