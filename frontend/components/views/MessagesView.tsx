"use client";

import { ChevronRight, MessageSquare } from "lucide-react";
import { Card, Empty, ErrorState, SkeletonList } from "@/components/ui";
import { useApi } from "@/lib/hooks";
import { timeAgo } from "@/lib/format";
import { ViewLink } from "./ViewNav";
import { ViewHeader, chip } from "./ViewHeader";

/** One row per client with a message, newest first. A thread opens in the work pane when the list
 * is there. */
export function MessagesView() {
  const { data, loading, error, refetch } = useApi<any[]>("/api/engage/messages");
  const unread = (data || []).reduce((n, t) => n + t.unread, 0);
  const owed = (data || []).filter((t) => t.needs_reply).length;
  return (
    <div className="view-container">
      <ViewHeader
        title="Messages"
        meta={data ? (
          <>
            <span className={chip}>{data.length} clients</span>
            {unread > 0 && <span className="chip bg-warn-bg text-warn" data-testid="messages-unread">{unread} unread</span>}
            {owed > 0 && <span className={chip} data-testid="messages-reply-owed">{owed} waiting for a reply</span>}
          </>
        ) : null}
      />
      {loading && !data ? (
        <SkeletonList count={4} />
      ) : error ? (
        <Card><ErrorState what="messages" message={error} onRetry={refetch} /></Card>
      ) : !data?.length ? (
        <Card><Empty>No messages</Empty></Card>
      ) : (
        <ul className="divide-y divide-border-soft overflow-hidden rounded-lg border border-border bg-surface" data-testid="messages-list">
          {data.map((t) => (
            <li key={t.household_id}>
              <ViewLink href={`/studio/messages/${t.household_id}`} data-testid="message-row"
                className="group flex items-center gap-3 px-4 py-3 transition-colors hover:bg-border-soft/60 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-inset focus-visible:ring-accent">
                <span aria-hidden className="flex h-9 w-9 shrink-0 items-center justify-center rounded bg-border-soft text-ink-soft"><MessageSquare size={16} /></span>
                <span className="min-w-0 flex-1">
                  <span className="block truncate font-medium text-ink">{t.household_name}</span>
                  <span className="mt-0.5 block truncate text-xs text-ink-muted">{t.last_body}</span>
                </span>
                {t.unread > 0 && <span className="chip bg-warn-bg text-warn tabular-nums">{t.unread}</span>}
                {t.needs_reply && t.unread === 0 && <span className={`${chip} hidden cq-sm:inline-flex`}>Reply owed</span>}
                <span className="shrink-0 text-xs tabular-nums text-ink-muted">{timeAgo(t.last_at)}</span>
                <ChevronRight size={16} className="shrink-0 text-ink-faint transition-colors group-hover:text-ink" aria-hidden />
              </ViewLink>
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}
