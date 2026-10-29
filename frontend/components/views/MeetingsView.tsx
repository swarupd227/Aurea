"use client";

import { useState } from "react";
import { CalendarClock, ChevronRight, Plus } from "lucide-react";
import { Card, SkeletonList, StatusBadge, Empty, ErrorState } from "@/components/ui";
import { useApi } from "@/lib/hooks";
import { api } from "@/lib/api";
import { ViewLink } from "./ViewNav";
import { ViewHeader, chip } from "./ViewHeader";

/** Client meetings, newest first. A meeting opens in the work pane when the list is there. */
export function MeetingsView() {
  const { data, loading, error, refetch } = useApi<any[]>("/api/engage/meetings");
  const [showNew, setShowNew] = useState(false);
  const briefed = (data || []).filter((m) => m.brief?.agenda).length;
  const passed = (data || []).filter((m) => m.date_passed).length;
  return (
    <div className="view-container">
      <ViewHeader
        title="Meetings"
        meta={data ? (
          <>
            <span className={chip}>{data.length} meetings</span>
            <span className={chip}>{briefed} briefed</span>
            {passed > 0 && <span className="chip bg-warn-bg text-warn" data-testid="meetings-date-passed">{passed} date passed</span>}
          </>
        ) : null}
        actions={<button className="btn-primary" data-testid="meeting-new" onClick={() => setShowNew(true)}><Plus size={16} /> New meeting</button>}
      />
      {showNew && <NewMeeting onClose={() => setShowNew(false)} onCreated={() => { setShowNew(false); refetch(); }} />}
      {loading ? (
        <SkeletonList count={4} />
      ) : error ? (
        <Card><ErrorState what="meetings" message={error} onRetry={refetch} /></Card>
      ) : !data?.length ? (
        <Card><Empty>No meetings scheduled.</Empty></Card>
      ) : (
        <ul className="divide-y divide-border-soft overflow-hidden rounded-lg border border-border bg-surface">
          {data.map((m) => (
            <li key={m.id}>
              <ViewLink href={`/studio/meetings/${m.id}`} data-testid="meeting-row"
                className="group flex items-center gap-3 px-4 py-3 transition-colors hover:bg-border-soft/60 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-inset focus-visible:ring-accent">
                <span aria-hidden className="flex h-9 w-9 shrink-0 items-center justify-center rounded bg-border-soft text-ink-soft"><CalendarClock size={16} /></span>
                <span className="min-w-0 flex-1">
                  <span className="block truncate font-medium text-ink">{m.title}</span>
                  <span className="mt-0.5 block truncate text-xs text-ink-muted">
                    {m.household_name}{m.scheduled_at ? ` · ${new Date(m.scheduled_at).toLocaleString()}` : ""}
                  </span>
                </span>
                {m.brief?.agenda && <span className={`${chip} hidden cq-sm:inline-flex`}>brief</span>}
                {m.date_passed && <span className="chip bg-warn-bg text-warn">date passed</span>}
                <StatusBadge status={m.status} />
                <ChevronRight size={16} className="shrink-0 text-ink-faint transition-colors group-hover:text-ink" aria-hidden />
              </ViewLink>
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}

function NewMeeting({ onClose, onCreated }: { onClose: () => void; onCreated: () => void }) {
  const { data: households } = useApi<any[]>("/api/core/households");
  const [household_id, setHid] = useState("");
  const [title, setTitle] = useState("Portfolio review");
  const [busy, setBusy] = useState(false);
  async function create() {
    if (!household_id) return;
    setBusy(true);
    try { await api("/api/engage/meetings", { body: { household_id, title } }); onCreated(); }
    finally { setBusy(false); }
  }
  return (
    <div className="fixed inset-0 bg-ink/30 z-50 flex items-center justify-center p-4" onClick={onClose}>
      <div className="card p-6 w-full max-w-md" role="dialog" aria-label="New meeting" onClick={(e) => e.stopPropagation()}>
        <h3 className="text-lg font-semibold text-ink mb-4">New meeting</h3>
        <div className="space-y-3">
          <div><label className="label" htmlFor="meeting-client">Client</label>
            <select id="meeting-client" className="input" value={household_id} onChange={(e) => setHid(e.target.value)}>
              <option value="">Select a client…</option>
              {households?.map((h) => <option key={h.id} value={h.id}>{h.name}</option>)}
            </select>
          </div>
          <div><label className="label" htmlFor="meeting-title">Title</label><input id="meeting-title" className="input" value={title} onChange={(e) => setTitle(e.target.value)} /></div>
        </div>
        <div className="flex justify-end gap-2 mt-5">
          <button className="btn-ghost" onClick={onClose}>Cancel</button>
          <button className="btn-primary" disabled={busy || !household_id} onClick={create}>{busy ? "Creating…" : "Create"}</button>
        </div>
      </div>
    </div>
  );
}
