import { reviewDay } from "./ReviewsCard";

export type IpsStatus = "breach" | "excepted" | "within" | "not_assessed" | "no_ips";

export interface IpsFinding {
  term: string;
  kind: "allocation" | "position" | "restricted" | "cash" | "profile";
  asset_class?: string;
  symbol?: string;
  weight?: number;
  low?: number;
  high?: number;
  limit?: number;
  until?: string;
  reason?: string;
  accepted_by?: string;
}

export interface IpsTerms {
  id: string;
  version: number;
  accepted_on: string;
  targets: Record<string, number>;
  drift_band: number;
  max_position: number | null;
  min_cash: number | null;
  restricted: string[];
  document: string;
  source: string;
}

export interface IpsMandate {
  mandate_id: string;
  mandate: string;
  model: string | null;
  household_id: string | null;
  household: string | null;
  status: IpsStatus;
  measured_against: "ips" | "model" | null;
  ips: IpsTerms | null;
  weights: Record<string, number>;
  targets: Record<string, number>;
  drift_band: number | null;
  price_source: string;
  findings: IpsFinding[];
  excepted: IpsFinding[];
  model_drift: { max_drift: number; band: number; outside: boolean } | null;
}

const STATUS: Record<IpsStatus, [string, string]> = {
  breach: ["breach", "text-crit"],
  excepted: ["exception", "text-warn"],
  within: ["within", "text-ok"],
  not_assessed: ["not assessed", "text-ink-muted"],
  no_ips: ["no IPS on file", "text-warn"],
};

export function IpsStatusChip({ status }: { status: IpsStatus }) {
  const [text, tone] = STATUS[status] ?? [status, "text-ink-muted"];
  return <span className={`chip ${tone}`} data-testid={`ips-status-${status}`}>{text}</span>;
}

export const share = (v: number | null | undefined, dp = 1) => (v == null ? "—" : `${(v * 100).toFixed(dp)}%`);

export function findingText(f: IpsFinding): string {
  if (f.kind === "allocation") return `${(f.asset_class ?? "").replace(/_/g, " ")} ${share(f.weight)} · ${share(f.low, 0)}-${share(f.high, 0)}`;
  if (f.kind === "position") return `${f.symbol} ${share(f.weight)} · limit ${share(f.limit, 0)}`;
  if (f.kind === "restricted") return `${f.symbol} ${share(f.weight)} · restricted`;
  if (f.kind === "cash") return `cash ${share(f.weight)} · floor ${share(f.limit, 0)}`;
  return "risk profile changed";
}

export function IpsFindings({ row }: { row: Pick<IpsMandate, "findings" | "excepted"> }) {
  return (
    <div className="flex flex-wrap gap-1.5">
      {row.findings.map((f) => <span key={f.term} className="chip text-crit" data-testid="ips-finding">{findingText(f)}</span>)}
      {row.excepted.map((f) => (
        <span key={f.term} className="chip text-warn" title={`${f.reason ?? ""} · ${f.accepted_by ?? ""}`} data-testid="ips-excepted">
          {findingText(f)} · to {reviewDay(f.until)}
        </span>
      ))}
    </div>
  );
}

export function measuredText(m: Pick<IpsMandate, "ips" | "model">): string {
  return m.ips ? `IPS v${m.ips.version}` : m.model ? `${m.model} model` : "—";
}

function Rows({ rows }: { rows: IpsMandate[] }) {
  return (
    <table className="w-full text-sm">
      <thead>
        <tr className="text-left text-[10px] uppercase tracking-wide text-ink-muted">
          <th className="px-3 py-1.5 font-medium">Mandate</th>
          <th className="px-3 py-1.5 font-medium">Measured against</th>
          <th className="px-3 py-1.5 font-medium">Outside</th>
          <th className="px-3 py-1.5 text-right font-medium">Status</th>
        </tr>
      </thead>
      <tbody className="divide-y divide-border-soft">
        {rows.map((m) => (
          <tr key={m.mandate_id}>
            <td className="px-3 py-1.5">
              <div className="text-ink">{m.mandate}</div>
              <div className="text-xs text-ink-muted">{m.household ?? "—"}</div>
            </td>
            <td className="px-3 py-1.5 font-mono text-xs text-ink-muted">{measuredText(m)}</td>
            <td className="px-3 py-1.5"><IpsFindings row={m} /></td>
            <td className="px-3 py-1.5 text-right"><IpsStatusChip status={m.status} /></td>
          </tr>
        ))}
      </tbody>
    </table>
  );
}

const SHOWN = 8;

export function IpsCard({ result }: { result: any }) {
  if (Array.isArray(result?.mandates)) {
    const s = result.summary;
    const rows: IpsMandate[] = result.mandates;
    const shown = rows.slice(0, SHOWN);
    return (
      <div className="overflow-hidden rounded-lg border border-border bg-surface" data-testid="ips-card">
        <div className="flex flex-wrap items-center justify-between gap-2 border-b border-border-soft px-3 py-2">
          <div className="text-sm font-medium text-ink">IPS monitoring{result.household ? ` · ${result.household}` : ""}</div>
          <div className="flex flex-wrap gap-1.5">
            <span className="chip text-crit">{s.breach} breach</span>
            <span className="chip text-warn">{s.excepted} exception</span>
            <span className="chip text-ok">{s.within} within</span>
            <span className="chip text-ink-muted">{s.no_ips} no IPS on file</span>
          </div>
        </div>
        <Rows rows={shown} />
        {rows.length > shown.length && (
          <div className="border-t border-border-soft px-3 py-2"><span className="chip text-ink-muted">+{rows.length - shown.length} more</span></div>
        )}
      </div>
    );
  }
  const m = result as IpsMandate;
  return (
    <div className="overflow-hidden rounded-lg border border-border bg-surface" data-testid="ips-card">
      <div className="flex flex-wrap items-center justify-between gap-2 border-b border-border-soft px-3 py-2">
        <div className="text-sm font-medium text-ink">IPS · {m.mandate}</div>
        <div className="flex flex-wrap gap-1.5">
          <span className="chip text-ink-muted">{measuredText(m)}</span>
          <IpsStatusChip status={m.status} />
        </div>
      </div>
      <Rows rows={[m]} />
      {m.ips && (
        <div className="flex flex-wrap gap-1.5 border-t border-border-soft px-3 py-2">
          {Object.entries(m.ips.targets).map(([c, w]) => <span key={c} className="chip text-ink-muted">{c.replace(/_/g, " ")} {share(w, 0)}</span>)}
          <span className="chip text-ink-muted">band ±{share(m.ips.drift_band)}</span>
          {m.ips.max_position != null && <span className="chip text-ink-muted">largest holding {share(m.ips.max_position, 0)}</span>}
          {m.ips.min_cash != null && <span className="chip text-ink-muted">cash floor {share(m.ips.min_cash, 0)}</span>}
          {m.ips.restricted.map((r) => <span key={r} className="chip text-ink-muted">restricted {r}</span>)}
          <span className="chip text-ink-muted" title={m.ips.document}>accepted {reviewDay(m.ips.accepted_on)}</span>
        </div>
      )}
    </div>
  );
}
