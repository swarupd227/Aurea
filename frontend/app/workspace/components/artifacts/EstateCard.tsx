import { money, pct, titleCase } from "@/lib/format";

// The estate read (read_estate) as a card: figures and chips only - the agent's message carries the
// sentences.

export interface EstatePayload {
  household_id: string;
  household_name: string;
  total_estate_value: number;
  wealth_breakdown: Record<string, { value: number; pct: number }>;
  trust_structures: { id: string; name: string; entity_type: string; governance_score: number; jurisdiction: string | null }[];
  heir_readiness: { id: string; name: string; readiness_score: number; readiness_level: string; has_mandate: boolean; has_goals: boolean; has_profile: boolean }[];
  succession_gaps: { type: string; severity: string; title: string; detail?: string }[];
  succession_risk: { score: number; level: string; gaps_count: number; high_severity_count: number };
  step_up_basis: { unrealised_gain: number; step_up_benefit: number } | null;
  generated_at?: string;
  message?: string;
}

const obj = (v: any) => v !== null && typeof v === "object" && !Array.isArray(v);

export function isEstate(r: any): boolean {
  return obj(r)
    && typeof r.household_name === "string"
    && typeof r.total_estate_value === "number"
    && obj(r.wealth_breakdown)
    && Array.isArray(r.trust_structures)
    && Array.isArray(r.heir_readiness)
    && Array.isArray(r.succession_gaps)
    && obj(r.succession_risk)
    && typeof r.succession_risk.score === "number"
    && typeof r.succession_risk.level === "string";
}

const Chip = ({ children, tone = "text-ink-muted" }: { children: React.ReactNode; tone?: string }) => (
  <span className={`inline-block rounded border border-border px-1.5 py-0.5 font-mono text-[11px] ${tone}`}>{children}</span>
);

function Tile({ label, value, tone = "text-ink" }: { label: string; value: React.ReactNode; tone?: string }) {
  return (
    <div className="px-3 py-2">
      <div className="font-mono text-[11px] uppercase tracking-wider text-ink-muted">{label}</div>
      <div className={`mt-0.5 font-mono text-lg tabular-nums ${tone}`}>{value}</div>
    </div>
  );
}

const RISK_TONE: Record<string, string> = { high: "text-crit", moderate: "text-warn", low: "text-ok" };
const SEV_TONE: Record<string, string> = { high: "text-crit", medium: "text-warn", low: "text-ink-muted" };
const READY_TONE: Record<string, string> = { ready: "text-ok", developing: "text-warn", early: "text-ink-muted" };

export function EstateCard({ result }: { result: EstatePayload }) {
  const r = result;
  const classes = Object.entries(r.wealth_breakdown);
  return (
    <div className="overflow-hidden rounded-lg border border-border bg-surface" data-testid="estate-card">
      <div className="flex items-center justify-between gap-2 border-b border-border-soft px-3 py-2">
        <div className="min-w-0 truncate text-sm font-medium text-ink">{r.household_name}</div>
        <Chip tone={RISK_TONE[r.succession_risk.level]}>{titleCase(r.succession_risk.level)} risk · {r.succession_risk.score}/100</Chip>
      </div>
      <div className="grid grid-cols-3 divide-x divide-border-soft border-b border-border-soft">
        <Tile label="Estate" value={money(r.total_estate_value)} />
        <Tile label="Gaps" value={r.succession_gaps.length} tone={r.succession_gaps.length ? "text-warn" : "text-ok"} />
        <Tile label="High" value={r.succession_risk.high_severity_count ?? 0} tone={r.succession_risk.high_severity_count ? "text-crit" : "text-ink-muted"} />
      </div>
      {classes.length > 0 && (
        <div className="flex flex-wrap gap-1.5 border-b border-border-soft px-3 py-1.5">
          {classes.map(([cls, info]) => <Chip key={cls}>{titleCase(cls)} {pct(info?.pct, 0)}</Chip>)}
        </div>
      )}
      {r.step_up_basis && (
        <div className="flex flex-wrap gap-1.5 border-b border-border-soft px-3 py-1.5">
          <Chip>§1014 unrealised {money(r.step_up_basis.unrealised_gain)}</Chip>
          <Chip tone="text-ok">step-up {money(r.step_up_basis.step_up_benefit)}</Chip>
        </div>
      )}
      <table className="w-full table-fixed text-sm">
        <tbody className="divide-y divide-border-soft">
          {r.succession_gaps.map((g, i) => (
            <tr key={`g${i}`}>
              <td className="truncate px-3 py-1.5 text-ink">{g.title}</td>
              <td className={`w-20 px-3 py-1.5 text-right text-xs ${SEV_TONE[g.severity] ?? "text-ink-muted"}`}>{titleCase(g.severity)}</td>
            </tr>
          ))}
          {r.trust_structures.map((t) => (
            <tr key={t.id}>
              <td className="truncate px-3 py-1.5 text-ink-muted">{t.name}</td>
              <td className="w-20 px-3 py-1.5 text-right font-mono text-xs tabular-nums text-ink-muted">{t.governance_score}/100</td>
            </tr>
          ))}
          {r.heir_readiness.map((h) => (
            <tr key={h.id}>
              <td className="truncate px-3 py-1.5 text-ink-muted">{h.name} · heir</td>
              <td className={`w-20 px-3 py-1.5 text-right font-mono text-xs tabular-nums ${READY_TONE[h.readiness_level] ?? "text-ink-muted"}`}>{h.readiness_score}/100</td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}
