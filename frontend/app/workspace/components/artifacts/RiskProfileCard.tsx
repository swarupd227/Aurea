export type RiskStatus =
  | "more_risk" | "less_risk" | "none_on_file" | "aligned" | "kept" | "not_taken" | "not_scored" | "no_mandate";

export interface RiskMandate {
  mandate_id: string;
  mandate: string;
  risk_profile: string | null;
  capacity_for_loss: string | null;
  status: RiskStatus;
  decision: { decision: string; reason: string; decided_by: string; decided_at: string } | null;
}

export interface RiskAssessment {
  id: string;
  status: "scored" | "not_scored";
  missing: string[];
  tolerance: string | null;
  capacity: string | null;
  indicated: string | null;
  tension: boolean;
  drivers: string[];
  completed_at: string;
  completed_by_role: string;
}

export interface RiskHousehold {
  household_id: string;
  household: string;
  status: RiskStatus;
  assessment: RiskAssessment | null;
  mandates: RiskMandate[];
}

const STATUS: Record<RiskStatus, [string, string]> = {
  more_risk: ["more risk than answers", "text-crit"],
  none_on_file: ["nothing on file", "text-warn"],
  less_risk: ["less risk than answers", "text-warn"],
  not_scored: ["incomplete", "text-warn"],
  not_taken: ["not taken", "text-ink-muted"],
  kept: ["kept by adviser", "text-ink-muted"],
  aligned: ["aligned", "text-ok"],
  no_mandate: ["no mandate", "text-ink-muted"],
};

export function RiskStatusChip({ status }: { status: RiskStatus }) {
  const [text, tone] = STATUS[status] ?? [status, "text-ink-muted"];
  return <span className={`chip ${tone}`} data-testid={`risk-status-${status}`}>{text}</span>;
}

const onFile = (m: RiskMandate) => `${m.risk_profile ?? "—"} · ${m.capacity_for_loss ?? "—"}`;

function Rows({ rows }: { rows: { household: RiskHousehold; m: RiskMandate | null }[] }) {
  return (
    <table className="w-full text-sm">
      <thead>
        <tr className="text-left text-[10px] uppercase tracking-wide text-ink-muted">
          <th className="px-3 py-1.5 font-medium">Mandate</th>
          <th className="px-3 py-1.5 font-medium">Indicated</th>
          <th className="px-3 py-1.5 font-medium">On file</th>
          <th className="px-3 py-1.5 text-right font-medium">Status</th>
        </tr>
      </thead>
      <tbody className="divide-y divide-border-soft">
        {rows.map(({ household: h, m }) => (
          <tr key={m?.mandate_id ?? h.household_id}>
            <td className="px-3 py-1.5">
              <div className="text-ink">{m?.mandate ?? h.household}</div>
              {m && <div className="text-xs text-ink-muted">{h.household}</div>}
            </td>
            <td className="px-3 py-1.5 font-mono text-xs text-ink">{h.assessment?.indicated ?? "—"}</td>
            <td className="px-3 py-1.5 font-mono text-xs text-ink-muted">{m ? onFile(m) : "—"}</td>
            <td className="px-3 py-1.5 text-right"><RiskStatusChip status={m?.status ?? h.status} /></td>
          </tr>
        ))}
      </tbody>
    </table>
  );
}

const SHOWN = 8;

export function RiskProfileCard({ result }: { result: any }) {
  if (Array.isArray(result?.households)) {
    const s = result.summary;
    const homes: RiskHousehold[] = result.households;
    const rows = homes.flatMap((h) => (h.mandates.length ? h.mandates.map((m) => ({ household: h, m })) : [{ household: h, m: null }]));
    const flagged = rows.filter((r) => ["more_risk", "less_risk", "none_on_file"].includes(r.m?.status ?? r.household.status));
    const shown = (flagged.length ? flagged : rows).slice(0, SHOWN);
    return (
      <div className="overflow-hidden rounded-lg border border-border bg-surface" data-testid="risk-profile-card">
        <div className="flex flex-wrap items-center justify-between gap-2 border-b border-border-soft px-3 py-2">
          <div className="text-sm font-medium text-ink">Risk profiles</div>
          <div className="flex flex-wrap gap-1.5">
            <span className="chip text-crit">{s.more_risk} more risk</span>
            <span className="chip text-warn">{s.less_risk} less risk</span>
            <span className="chip text-ok">{s.aligned} aligned</span>
            <span className="chip text-ink-muted">{s.not_taken} not taken</span>
          </div>
        </div>
        <Rows rows={shown} />
        {rows.length > shown.length && (
          <div className="border-t border-border-soft px-3 py-2"><span className="chip text-ink-muted">+{rows.length - shown.length} more</span></div>
        )}
      </div>
    );
  }
  if (Array.isArray(result?.mandates)) {
    const h = result as RiskHousehold;
    const a = h.assessment;
    return (
      <div className="overflow-hidden rounded-lg border border-border bg-surface" data-testid="risk-profile-card">
        <div className="flex flex-wrap items-center justify-between gap-2 border-b border-border-soft px-3 py-2">
          <div className="text-sm font-medium text-ink">Risk profile · {h.household}</div>
          <div className="flex flex-wrap gap-1.5">
            {a?.tolerance && <span className="chip text-ink-muted">tolerance {a.tolerance}</span>}
            {a?.capacity && <span className="chip text-ink-muted">capacity {a.capacity}</span>}
            {a?.indicated && <span className="chip text-ink">indicated {a.indicated}</span>}
            {a?.tension && <span className="chip text-warn">tolerance and capacity differ</span>}
          </div>
        </div>
        {h.mandates.length
          ? <Rows rows={h.mandates.map((m) => ({ household: h, m }))} />
          : <div className="px-3 py-3"><RiskStatusChip status={h.status} /></div>}
        {!!a?.drivers.length && (
          <div className="flex flex-wrap gap-1.5 border-t border-border-soft px-3 py-2">
            {a.drivers.map((d) => <span key={d} className="chip text-ink-muted">{d.replace(/_/g, " ")}</span>)}
          </div>
        )}
      </div>
    );
  }
  return (
    <div className="flex flex-wrap items-center gap-1.5 rounded-lg border border-border bg-surface px-3 py-2" data-testid="risk-decision-card">
      {result?.mandate && <span className="chip text-ink">{result.mandate}</span>}
      <span className="chip text-ink-muted">{result?.decision === "adopt" ? "adopted" : "kept"}</span>
      <span className="chip text-ink-muted">{result?.was?.risk_profile ?? "—"} · {result?.was?.capacity_for_loss ?? "—"}</span>
      <span className="chip text-ink">{result?.risk_profile ?? "—"} · {result?.capacity_for_loss ?? "—"}</span>
    </div>
  );
}
