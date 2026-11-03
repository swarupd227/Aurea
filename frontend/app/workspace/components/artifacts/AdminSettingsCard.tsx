import { firmLocale, money, pct, titleCase } from "@/lib/format";
import { CardHead, CardShell, Chip, obj } from "./CanvasCardParts";

// read_admin_settings as a card: one section of the firm's configuration as a compact table. Figures and
// chips only - the agent's message carries the sentences, so `message` is not shown.

export interface AdminSettingsColumn { key: string; label: string; kind?: string }
export interface AdminSettingsPayload {
  section: string;
  title: string;
  columns: AdminSettingsColumn[];
  rows: Record<string, any>[];
  message?: string;
}

export function isAdminSettings(r: any): r is AdminSettingsPayload {
  return obj(r)
    && typeof r.section === "string"
    && typeof r.title === "string"
    && Array.isArray(r.columns)
    && Array.isArray(r.rows)
    && r.columns.every((c: any) => obj(c) && typeof c.key === "string" && typeof c.label === "string");
}

const CAP = 12;
const NUMERIC = new Set(["money", "bps", "pct", "number"]);

function plain(v: any): string {
  if (v === null || v === undefined || v === "") return "—";
  if (Array.isArray(v)) return v.length ? v.map(plain).join(", ") : "—";
  if (typeof v === "object") return JSON.stringify(v);
  return String(v);
}

function date(v: any): string {
  if (!v) return "—";
  const d = new Date(v);
  return isNaN(d.getTime()) ? String(v) : d.toLocaleDateString(firmLocale(), { day: "numeric", month: "short", year: "numeric" });
}

/** A share as a fraction (0.6) or as a percentage already (60). */
function share(v: any): string {
  const n = Number(v);
  if (v === null || v === undefined || v === "" || isNaN(n)) return "—";
  return Math.abs(n) > 1 ? `${n}%` : pct(n, 0);
}

export function AdminCell({ kind, value, row }: { kind?: string; value: any; row?: Record<string, any> }) {
  switch (kind) {
    case "chip":
      return value === null || value === undefined || value === "" ? <span className="text-ink-muted">—</span> : <Chip>{titleCase(String(value))}</Chip>;
    case "money":
      return <>{typeof value === "number" ? money(value, row?.currency || row?.ccy) : plain(value)}</>;
    case "bps":
      return <>{value === null || value === undefined || value === "" ? "—" : `${value} bps`}</>;
    case "pct":
      return <>{share(value)}</>;
    case "date":
      return <>{date(value)}</>;
    case "bool":
      return value === null || value === undefined
        ? <span className="text-ink-muted">—</span>
        : <Chip tone={value ? "text-ok" : "text-ink-muted"}>{value ? "yes" : "no"}</Chip>;
    default:
      return <>{plain(value)}</>;
  }
}

export function AdminSettingsCard({ result }: { result: AdminSettingsPayload }) {
  const r = result;
  const rows = r.rows.slice(0, CAP);
  const more = r.rows.length - rows.length;
  return (
    <CardShell testid="admin-settings-card">
      <CardHead title={r.title}>
        <Chip>{titleCase(r.section)}</Chip>
        <Chip>{r.rows.length} {r.rows.length === 1 ? "row" : "rows"}</Chip>
      </CardHead>
      {r.rows.length === 0 ? (
        <div className="px-3 py-2"><Chip>none</Chip></div>
      ) : (
        <div className="overflow-x-auto">
          <table className="w-full text-sm">
            <thead>
              <tr className="border-b border-border-soft">
                {r.columns.map((c) => (
                  <th key={c.key} className={`whitespace-nowrap px-3 py-1.5 font-mono text-[10px] font-medium uppercase tracking-wider text-ink-muted ${NUMERIC.has(c.kind || "") ? "text-right" : "text-left"}`}>
                    {c.label}
                  </th>
                ))}
              </tr>
            </thead>
            <tbody className="divide-y divide-border-soft">
              {rows.map((row, i) => (
                <tr key={row.id ?? i}>
                  {r.columns.map((c, j) => (
                    <td key={c.key}
                      className={`px-3 py-1.5 ${NUMERIC.has(c.kind || "") ? "text-right font-mono text-xs tabular-nums text-ink-soft" : j === 0 ? "text-ink" : "text-ink-soft"} ${c.kind === "date" ? "whitespace-nowrap font-mono text-xs" : ""}`}>
                      <AdminCell kind={c.kind} value={row[c.key]} row={row} />
                    </td>
                  ))}
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
      {more > 0 && (
        <div className="border-t border-border-soft px-3 py-1.5"><Chip>+{more} more</Chip></div>
      )}
    </CardShell>
  );
}
