import { titleCase } from "@/lib/format";
import { CardHead, CardShell, Chip, obj } from "./CanvasCardParts";

// Every admin write tool (update_firm_profile, set_agent_switches, invite_user, change_user, save_segment,
// set_connector, ...) as a card: the record, its section, and each field from what it was to what it is.
// Figures and chips only - the agent's message carries the sentences, so `message` is not shown.

export interface AdminChange { field: string; from: any; to: any }
export interface AdminChangePayload {
  section: string;
  record: { id?: string | null; label: string };
  changes: AdminChange[];
  message?: string;
}

export function isAdminChange(r: any): r is AdminChangePayload {
  return obj(r)
    && typeof r.section === "string"
    && obj(r.record)
    && typeof r.record.label === "string"
    && Array.isArray(r.changes)
    && r.changes.every((c: any) => obj(c) && typeof c.field === "string");
}

function said(v: any): string {
  if (v === null || v === undefined || v === "") return "—";
  if (typeof v === "boolean") return v ? "yes" : "no";
  if (Array.isArray(v)) return v.length ? v.map(said).join(", ") : "—";
  if (typeof v === "object") return JSON.stringify(v);
  return String(v);
}

export function AdminChangeCard({ result }: { result: AdminChangePayload }) {
  const r = result;
  return (
    <CardShell testid="admin-change-card">
      <CardHead title={r.record.label}>
        <Chip>{titleCase(r.section)}</Chip>
        {r.changes.length === 0 && <Chip>no change</Chip>}
      </CardHead>
      {r.changes.length > 0 && (
        <table className="w-full table-fixed text-sm">
          <tbody className="divide-y divide-border-soft">
            {r.changes.map((c, i) => (
              <tr key={`${c.field}-${i}`} data-testid="admin-change-row">
                <td className="w-1/3 truncate px-3 py-1.5 font-mono text-xs text-ink-muted" title={c.field}>{titleCase(c.field)}</td>
                <td className="break-words px-3 py-1.5 font-mono text-xs">
                  <span className="text-ink-muted">{said(c.from)}</span>
                  <span className="px-1.5 text-ink-faint">→</span>
                  <span className="text-ink">{said(c.to)}</span>
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      )}
    </CardShell>
  );
}
