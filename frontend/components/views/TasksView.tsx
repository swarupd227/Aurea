"use client";

import { useState } from "react";
import { CheckSquare, Square, UserPlus, X } from "lucide-react";
import { Card, Empty, ErrorState, SkeletonList } from "@/components/ui";
import { useApi } from "@/lib/hooks";
import { api } from "@/lib/api";
import { titleCase } from "@/lib/format";
import { withRef } from "@/lib/refs";
import { ViewLink, useViewNav } from "./ViewNav";
import { ViewHeader, chip } from "./ViewHeader";

const warn = "chip bg-warn-bg text-warn";

/** Follow-ups, soonest due first. In the work pane, completing or assigning one is the
 * conversation's tool behind its Confirm card; on a page the controls act directly. */
export function TasksView() {
  const nav = useViewNav();
  const [status, setStatus] = useState("open");
  const [mine, setMine] = useState(false);
  const { data, loading, error, refetch } = useApi<any[]>(
    `/api/engage/tasks?status=${status}&assigned_to_me=${mine}`,
    [status, mine]
  );
  const { data: people } = useApi<any[]>("/api/engage/task-assignees");
  const [assigning, setAssigning] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const open = (data || []).filter((t) => t.status === "open");
  const overdue = open.filter((t) => t.overdue).length;
  const unassigned = open.filter((t) => !t.assigned_to).length;

  async function complete(t: any) {
    if (nav.say) return nav.say(withRef(`Mark the task "${t.title}" as done.`, "task", t.id));
    await api(`/api/engage/tasks/${t.id}/complete`, { body: {} });
    refetch();
  }

  async function assign(t: any, personId: string) {
    setAssigning(null);
    const person = (people || []).find((p) => p.id === personId);
    if (nav.say) {
      return nav.say(withRef(person ? `Assign the task "${t.title}" to ${person.name}.` : `Unassign the task "${t.title}".`, "task", t.id));
    }
    setBusy(true);
    try {
      await api(`/api/engage/tasks/${t.id}`, { method: "PATCH", body: { assigned_to: personId || null } });
      refetch();
    } finally { setBusy(false); }
  }

  return (
    <div className="view-container">
      <ViewHeader
        title="Tasks"
        meta={data ? (
          <>
            <span className={chip}>{open.length} open</span>
            {overdue > 0 && <span className={warn} data-testid="tasks-overdue">{overdue} overdue</span>}
            {unassigned > 0 && <span className={chip}>{unassigned} unassigned</span>}
          </>
        ) : null}
        actions={
          <div className="flex items-center gap-3">
            <label className="flex cursor-pointer items-center gap-2 text-sm text-ink-soft">
              <input type="checkbox" data-testid="tasks-mine" checked={mine} onChange={(e) => setMine(e.target.checked)} />
              Assigned to me
            </label>
            <div className="flex overflow-hidden rounded border border-border text-sm">
              {["open", "all"].map((s) => (
                <button key={s} data-testid={`tasks-filter-${s}`} aria-pressed={status === s} onClick={() => setStatus(s)}
                  className={`px-3 py-1.5 ${status === s ? "bg-border-soft text-ink" : "text-ink-muted hover:text-ink"}`}>{titleCase(s)}</button>
              ))}
            </div>
          </div>
        }
      />
      {loading ? (
        <SkeletonList count={4} />
      ) : error ? (
        <Card><ErrorState what="tasks" message={error} onRetry={refetch} /></Card>
      ) : !data?.length ? (
        <Card><Empty>No tasks</Empty></Card>
      ) : (
        <ul className="divide-y divide-border-soft overflow-hidden rounded-lg border border-border bg-surface" data-testid="tasks-list">
          {data.map((t) => (
            <li key={t.id} className="flex items-start gap-3 px-4 py-3" data-testid="task-row">
              {t.status === "open" ? (
                <button data-testid="task-complete" onClick={() => complete(t)} aria-label={`Mark "${t.title}" as done`}
                  className="mt-0.5 shrink-0 text-ink-muted transition-colors hover:text-ok">
                  <Square size={18} aria-hidden />
                </button>
              ) : (
                <CheckSquare size={18} className="mt-0.5 shrink-0 text-ok" aria-hidden />
              )}
              <div className="min-w-0 flex-1">
                <div className={`text-sm font-medium ${t.status === "open" ? "text-ink" : "text-ink-muted line-through"}`}>{t.title}</div>
                <div className="mt-1 flex flex-wrap items-center gap-1.5">
                  {t.subject_label && (t.household_id
                    ? <ViewLink href={`/studio/clients/${t.household_id}`} className={`${chip} hover:text-ink`}>{t.subject_label}</ViewLink>
                    : <span className={chip}>{t.subject_label}</span>)}
                  <span className={chip}>{titleCase(t.source)}</span>
                  {t.due_date && <span className={`${t.overdue ? warn : chip} tabular-nums`}>{t.overdue ? "Overdue" : "Due"} {t.due_date}</span>}
                  {t.assigned_to_name && <span className={chip}>{t.assigned_to_name}</span>}
                </div>
              </div>
              {t.status === "open" && (
                assigning === t.id ? (
                  <div className="flex shrink-0 items-center gap-1">
                    <select className="input h-7 py-0.5 text-xs" aria-label={`Assign "${t.title}"`} data-testid="task-assign-select"
                      defaultValue={t.assigned_to ?? ""} disabled={busy} onChange={(e) => assign(t, e.target.value)}>
                      <option value="">Nobody</option>
                      {(people || []).map((p) => <option key={p.id} value={p.id}>{p.name} ({titleCase(p.role)})</option>)}
                    </select>
                    <button className="text-ink-muted hover:text-ink" aria-label="Close" onClick={() => setAssigning(null)}><X size={14} aria-hidden /></button>
                  </div>
                ) : (
                  <button className="btn-ghost shrink-0 px-2 py-0.5 text-xs text-ink-muted" data-testid="task-assign" onClick={() => setAssigning(t.id)}>
                    <UserPlus size={13} aria-hidden /> Assign
                  </button>
                )
              )}
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}
