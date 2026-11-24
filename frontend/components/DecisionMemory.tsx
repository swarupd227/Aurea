import { firmLocale } from "@/lib/format";
/** What people decided before on the same finding, and what they asked its agent to remember (B-204). Chips only. */
export function DecisionMemory({ memory }: { memory: any }) {
  if (!memory || (!memory.last_decision && !memory.notes?.length)) return null;
  const last = memory.last_decision;
  const day = (iso?: string | null) =>
    iso ? new Date(`${iso.slice(0, 10)}T00:00:00`).toLocaleDateString(firmLocale(), { day: "numeric", month: "short", year: "numeric" }) : "";
  return (
    <div className="flex flex-wrap items-center gap-1.5" data-testid="decision-memory">
      {last && (
        <span className={`chip ${last.decision === "dismissed" ? "text-warn" : "text-ink-muted"}`} title={last.reason || undefined}
          data-testid="decision-memory-last">
          last time: {last.decision}{last.by ? ` by ${last.by}` : ""} · {day(last.on)}{last.reason ? ` · ${last.reason}` : ""}
        </span>
      )}
      {(memory.notes || []).map((n: any, i: number) => (
        <span key={i} className="chip text-ink-muted" data-testid="decision-memory-note">noted by {n.by}: {n.text}</span>
      ))}
    </div>
  );
}
