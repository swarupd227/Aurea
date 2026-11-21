import { firmLocale, titleCase } from "@/lib/format";
import { CardHead, CardShell, Chip, obj } from "./CanvasCardParts";

// read_foundation_sources as a card, and the Foundation section of the Connectors page (B-161 to B-163): where each
// domain of the firm's data comes from, what its owner can supply, its last sync, how fresh the copy is, its records,
// open breaks and waiting commands. Figures, tables and chips only - the agent's message carries the sentences.

export interface FoundationDomain {
  domain: string;
  label: string;
  owner: string;
  native: boolean;
  adapter: string;
  built: boolean;
  capabilities: { tax_lots?: boolean; transactions_days?: number | null | false; events?: boolean };
  // What the owner's own system says it is (an MCP server declares it), and whether it is a simulation.
  platform?: string | null;
  simulated?: boolean;
  connector_id: string | null;
  mode: "mock" | "live" | null;
  last_sync: { at: string | null; status: string; records: number } | null;
  as_of: string | null;
  records: number;
  from_source: number | null;
  open_breaks: number;
  pending_commands: number;
  owners_available: string[];
}
export interface FoundationBreakField { field: string; here: any; source: any; synced?: any }
export interface FoundationBreakRow {
  id: string; domain: string; entity: string; external_id: string; fields: FoundationBreakField[]; detected_at: string | null;
}
export interface FoundationCommandRow {
  id: string; domain: string; kind: string; to: string; status: string; activity_type: string | null; summary?: string | null;
  error: string | null; created_at: string | null; confirmed_at: string | null;
}
export interface FoundationPayload {
  native: boolean;
  simulated?: boolean;
  domains: FoundationDomain[];
  breaks: FoundationBreakRow[];
  commands: FoundationCommandRow[];
  message?: string;
}

export function isFoundationSources(r: any): r is FoundationPayload {
  return obj(r)
    && typeof r.native === "boolean"
    && Array.isArray(r.domains)
    && Array.isArray(r.breaks)
    && Array.isArray(r.commands)
    && r.domains.every((d: any) => obj(d) && typeof d.domain === "string" && typeof d.owner === "string" && obj(d.capabilities));
}

function day(v: string | null | undefined): string {
  if (!v) return "—";
  const d = new Date(v);
  return isNaN(d.getTime()) ? String(v) : d.toLocaleDateString(firmLocale(), { day: "numeric", month: "short", year: "numeric" });
}

function value(v: any): string {
  if (v === null || v === undefined || v === "") return "—";
  if (typeof v === "boolean") return v ? "yes" : "no";
  if (typeof v === "object") return JSON.stringify(v);
  return String(v);
}

const TH = "whitespace-nowrap px-3 py-1.5 text-left font-mono text-[10px] font-medium uppercase tracking-wider text-ink-muted";
const TD = "px-3 py-1.5 align-top";
const STATUS_TONE: Record<string, string> = { success: "text-ok", partial: "text-warn", error: "text-crit",
  pending: "text-warn", confirmed: "text-ok", failed: "text-crit" };

/** One row per domain: owner, capabilities, last sync, freshness, records, breaks and commands. */
export function FoundationDomainsTable({ domains, action }: { domains: FoundationDomain[]; action?: (d: FoundationDomain) => React.ReactNode }) {
  return (
    <div className="overflow-x-auto">
      <table className="w-full text-sm" data-testid="foundation-domains">
        <thead>
          <tr className="border-b border-border-soft">
            <th className={TH}>Domain</th>
            <th className={TH}>Owner</th>
            <th className={TH}>Supplies</th>
            <th className={TH}>Last sync</th>
            <th className={TH}>As of</th>
            <th className={`${TH} text-right`}>Records</th>
            <th className={`${TH} text-right`}>Breaks</th>
            <th className={`${TH} text-right`}>Waiting</th>
            {action && <th className={TH} />}
          </tr>
        </thead>
        <tbody className="divide-y divide-border-soft">
          {domains.map((d) => (
            <tr key={d.domain} data-testid={`foundation-domain-${d.domain}`}>
              <td className={`${TD} text-ink`}>{d.label}</td>
              <td className={TD}>
                <span className="flex flex-wrap gap-1">
                  <Chip tone={d.native ? "text-ink-muted" : "text-ink"} testid="foundation-owner">{d.adapter}</Chip>
                  {d.platform && <Chip testid="foundation-platform">{d.platform}</Chip>}
                  {d.mode && <Chip tone={d.mode === "live" ? "text-ok" : "text-warn"}>{d.mode}</Chip>}
                  {d.simulated && <Chip tone="text-warn" testid="foundation-simulated">simulated</Chip>}
                  {!d.built && <Chip tone="text-crit">not built</Chip>}
                </span>
              </td>
              <td className={TD}>
                <span className="flex flex-wrap gap-1">
                  <Chip tone={d.capabilities.tax_lots ? "text-ok" : "text-ink-muted"}>{d.capabilities.tax_lots ? "lots" : "no lots"}</Chip>
                  <Chip>{d.capabilities.transactions_days === false ? "no history" : d.capabilities.transactions_days == null ? "all history" : `${d.capabilities.transactions_days}d history`}</Chip>
                  {d.capabilities.events && <Chip>events</Chip>}
                </span>
              </td>
              <td className={`${TD} whitespace-nowrap font-mono text-xs text-ink-soft`}>
                {d.native ? <Chip>live tables</Chip> : d.last_sync ? (
                  <span className="flex flex-wrap items-center gap-1">
                    {day(d.last_sync.at)}
                    <Chip tone={STATUS_TONE[d.last_sync.status] || "text-ink-muted"}>{d.last_sync.status}</Chip>
                  </span>
                ) : "—"}
              </td>
              <td className={`${TD} whitespace-nowrap font-mono text-xs text-ink-soft`}>{d.native ? "—" : day(d.as_of)}</td>
              <td className={`${TD} text-right font-mono text-xs tabular-nums text-ink-soft`}>
                {d.records}{d.from_source != null && <span className="text-ink-muted"> · {d.from_source} synced</span>}
              </td>
              <td className={`${TD} text-right`}>
                {d.open_breaks ? <Chip tone="text-warn" testid="foundation-breaks-count">{d.open_breaks}</Chip> : <span className="font-mono text-xs text-ink-muted">0</span>}
              </td>
              <td className={`${TD} text-right`}>
                {d.pending_commands ? <Chip tone="text-warn">{d.pending_commands}</Chip> : <span className="font-mono text-xs text-ink-muted">0</span>}
              </td>
              {action && <td className={`${TD} whitespace-nowrap text-right`}>{action(d)}</td>}
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

/** Each open break: the record, and each field here, at the source and as last synced. */
export function FoundationBreaksTable({ rows }: { rows: FoundationBreakRow[] }) {
  return (
    <div className="overflow-x-auto">
      <table className="w-full text-sm" data-testid="foundation-breaks">
        <thead>
          <tr className="border-b border-border-soft">
            <th className={TH}>Record</th>
            <th className={TH}>Field</th>
            <th className={TH}>Here</th>
            <th className={TH}>Source</th>
            <th className={TH}>Last synced</th>
            <th className={TH}>Found</th>
          </tr>
        </thead>
        <tbody className="divide-y divide-border-soft">
          {rows.flatMap((b) => (b.fields.length ? b.fields : [{ field: "—", here: null, source: null }]).map((f, i) => (
            <tr key={`${b.id}-${i}`}>
              <td className={`${TD} text-ink`}>{i === 0 ? <Chip>{titleCase(b.entity.replace(/^crm_/, ""))}</Chip> : null}</td>
              <td className={`${TD} font-mono text-xs text-ink-soft`}>{f.field}</td>
              <td className={`${TD} font-mono text-xs text-warn`}>{value(f.here)}</td>
              <td className={`${TD} font-mono text-xs text-ink-soft`}>{value(f.source)}</td>
              <td className={`${TD} font-mono text-xs text-ink-muted`}>{value(f.synced)}</td>
              <td className={`${TD} whitespace-nowrap font-mono text-xs text-ink-muted`}>{i === 0 ? day(b.detected_at) : null}</td>
            </tr>
          )))}
        </tbody>
      </table>
    </div>
  );
}

/** What Astra sent a Foundation system, and whether it has come back. */
export function FoundationCommandsTable({ rows }: { rows: FoundationCommandRow[] }) {
  return (
    <div className="overflow-x-auto">
      <table className="w-full text-sm" data-testid="foundation-commands">
        <thead>
          <tr className="border-b border-border-soft">
            <th className={TH}>Sent</th>
            <th className={TH}>What</th>
            <th className={TH}>To</th>
            <th className={TH}>Status</th>
            <th className={TH}>Back</th>
          </tr>
        </thead>
        <tbody className="divide-y divide-border-soft">
          {rows.map((m) => (
            <tr key={m.id}>
              <td className={`${TD} whitespace-nowrap font-mono text-xs text-ink-soft`}>{day(m.created_at)}</td>
              <td className={TD}><Chip>{m.summary || m.activity_type || m.kind}</Chip></td>
              <td className={`${TD} font-mono text-xs text-ink-soft`}>{m.to}</td>
              <td className={TD}><Chip tone={STATUS_TONE[m.status] || "text-ink-muted"}>{m.status}</Chip></td>
              <td className={`${TD} whitespace-nowrap font-mono text-xs text-ink-muted`}>{day(m.confirmed_at)}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

export function FoundationSourcesCard({ result }: { result: FoundationPayload }) {
  const r = result;
  const owned = r.domains.filter((d) => !d.native).length;
  const breaks = r.domains.reduce((n, d) => n + d.open_breaks, 0);
  const waiting = r.domains.reduce((n, d) => n + d.pending_commands, 0);
  return (
    <CardShell testid="foundation-sources-card">
      <CardHead title="Foundation sources">
        <Chip>{r.native ? "all native" : `${owned} connected`}</Chip>
        {r.simulated && <Chip tone="text-warn" testid="foundation-sources-simulated">simulated</Chip>}
        {breaks > 0 && <Chip tone="text-warn">{breaks} {breaks === 1 ? "break" : "breaks"}</Chip>}
        {waiting > 0 && <Chip tone="text-warn">{waiting} waiting</Chip>}
      </CardHead>
      <FoundationDomainsTable domains={r.domains} />
      {r.breaks.length > 0 && (
        <div className="border-t border-border-soft">
          <FoundationBreaksTable rows={r.breaks.slice(0, 6)} />
        </div>
      )}
      {r.commands.length > 0 && (
        <div className="border-t border-border-soft">
          <FoundationCommandsTable rows={r.commands.slice(0, 6)} />
        </div>
      )}
    </CardShell>
  );
}
