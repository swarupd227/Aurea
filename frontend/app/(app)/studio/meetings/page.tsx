"use client";
import { useState } from "react";
import Link from "next/link";
import { CalendarClock, ChevronRight, Plus } from "lucide-react";
import PageHeader from "@/components/PageHeader";
import { Card, SkeletonList, StatusBadge, Empty } from "@/components/ui";
import { useApi } from "@/lib/hooks";
import { api } from "@/lib/api";

export default function Meetings() {
  const { data, loading, refetch } = useApi<any[]>("/api/engage/meetings");
  const [showNew, setShowNew] = useState(false);
  return (
    <div>
      <PageHeader
        title="Meetings"
        sub="Pre-meeting briefs, note capture, and follow-up tasks."
        actions={<button className="btn-gold" onClick={() => setShowNew(true)}><Plus size={16} /> New meeting</button>}
      />
      {showNew && <NewMeeting onClose={() => setShowNew(false)} onCreated={() => { setShowNew(false); refetch(); }} />}
      {loading ? (
        <SkeletonList count={4} />
      ) : !data?.length ? (
        <Card><Empty>No meetings scheduled.</Empty></Card>
      ) : (
        <div className="grid gap-3">
          {data.map((m) => (
            <Link key={m.id} href={`/studio/meetings/${m.id}`} className="card p-5 flex items-center gap-4 hover:shadow-lift transition group">
              <div className="h-11 w-11 rounded-xl bg-navy-800 text-white flex items-center justify-center"><CalendarClock size={18} /></div>
              <div className="flex-1">
                <div className="font-semibold text-ink">{m.title}</div>
                <div className="text-xs text-ink-muted mt-1">
                  {m.household_name}{m.scheduled_at ? ` · ${new Date(m.scheduled_at).toLocaleString()}` : ""}
                </div>
              </div>
              <StatusBadge status={m.status} />
              <ChevronRight className="text-navy-300 group-hover:text-navy-600" />
            </Link>
          ))}
        </div>
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
      <div className="card p-6 w-full max-w-md" onClick={(e) => e.stopPropagation()}>
        <h3 className="text-lg font-semibold text-ink mb-4">New meeting</h3>
        <div className="space-y-3">
          <div><label className="label">Client</label>
            <select className="input" value={household_id} onChange={(e) => setHid(e.target.value)}>
              <option value="">Select a client…</option>
              {households?.map((h) => <option key={h.id} value={h.id}>{h.name}</option>)}
            </select>
          </div>
          <div><label className="label">Title</label><input className="input" value={title} onChange={(e) => setTitle(e.target.value)} /></div>
        </div>
        <div className="flex justify-end gap-2 mt-5">
          <button className="btn-ghost" onClick={onClose}>Cancel</button>
          <button className="btn-primary" disabled={busy || !household_id} onClick={create}>{busy ? "Creating…" : "Create"}</button>
        </div>
      </div>
    </div>
  );
}
