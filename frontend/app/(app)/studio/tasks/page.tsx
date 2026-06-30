"use client";
import { useState } from "react";
import { CheckSquare, Square, UserPlus, X } from "lucide-react";
import PageHeader from "@/components/PageHeader";
import { Card, Spinner, Empty } from "@/components/ui";
import { useApi } from "@/lib/hooks";
import { api } from "@/lib/api";
import { titleCase } from "@/lib/format";

export default function Tasks() {
  const [status, setStatus] = useState("open");
  const [assignedToMe, setAssignedToMe] = useState(false);
  const { data, loading, refetch } = useApi<any[]>(
    `/api/engage/tasks?status=${status}&assigned_to_me=${assignedToMe}`,
    [status, assignedToMe]
  );
  const { data: users } = useApi<any[]>("/api/admin/users");
  const [assigning, setAssigning] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  async function complete(id: string) {
    await api(`/api/engage/tasks/${id}/complete`, { body: {} });
    refetch();
  }

  async function assign(taskId: string, userId: string | null) {
    setBusy(true);
    try {
      await api(`/api/engage/tasks/${taskId}`, { method: "PATCH", body: { assigned_to: userId } });
      setAssigning(null);
      refetch();
    } finally { setBusy(false); }
  }

  const staffUsers = (users || []).filter((u: any) => u.role !== "client");

  return (
    <div>
      <PageHeader
        title="Tasks"
        sub="Follow-ups from meetings and reviews."
        actions={
          <div className="flex items-center gap-2">
            <label className="flex items-center gap-2 text-sm text-ink-soft cursor-pointer">
              <input type="checkbox" checked={assignedToMe} onChange={(e) => setAssignedToMe(e.target.checked)} />
              Assigned to me
            </label>
            <div className="flex rounded-xl border border-navy-200 overflow-hidden text-sm">
              {["open", "all"].map((s) => (
                <button key={s} onClick={() => setStatus(s)} className={`px-3 py-1.5 ${status === s ? "bg-navy-800 text-white" : "bg-surface text-ink-soft"}`}>{titleCase(s)}</button>
              ))}
            </div>
          </div>
        }
      />
      {loading ? (
        <Spinner />
      ) : !data?.length ? (
        <Card><Empty>No tasks. Approve meeting notes to create follow-ups.</Empty></Card>
      ) : (
        <div className="space-y-2">
          {data.map((t) => (
            <Card key={t.id} className="py-3">
              <div className="flex items-start gap-3">
                <button onClick={() => t.status === "open" && complete(t.id)} className={`mt-0.5 ${t.status === "open" ? "text-navy-400 hover:text-positive" : "text-positive"}`}>
                  {t.status === "done" ? <CheckSquare size={20} /> : <Square size={20} />}
                </button>
                <div className="flex-1 min-w-0">
                  <div className={`text-sm font-medium ${t.status === "done" ? "text-ink-muted line-through" : "text-ink"}`}>{t.title}</div>
                  <div className="text-xs text-ink-muted mt-0.5">
                    {t.subject_label || "—"} · {titleCase(t.source)}
                    {t.due_date ? ` · due ${t.due_date}` : ""}
                    {t.assigned_to_name ? <span className="ml-2 text-navy-600">→ {t.assigned_to_name}</span> : ""}
                  </div>
                </div>
                {t.status === "open" && (
                  assigning === t.id ? (
                    <div className="flex items-center gap-1 shrink-0">
                      <select
                        className="input text-xs py-0.5 h-7"
                        defaultValue=""
                        onChange={(e) => assign(t.id, e.target.value || null)}
                        disabled={busy}
                      >
                        <option value="">Unassign</option>
                        {staffUsers.map((u: any) => (
                          <option key={u.id} value={u.id}>{u.full_name || u.email} ({titleCase(u.role)})</option>
                        ))}
                      </select>
                      <button className="text-ink-muted hover:text-ink" onClick={() => setAssigning(null)}><X size={14} /></button>
                    </div>
                  ) : (
                    <button className="btn-ghost text-xs py-0.5 px-2 shrink-0 text-ink-muted" onClick={() => setAssigning(t.id)}>
                      <UserPlus size={13} /> Assign
                    </button>
                  )
                )}
              </div>
            </Card>
          ))}
        </div>
      )}
    </div>
  );
}
