import { money } from "@/lib/format";
import { reviewDay } from "./ReviewsCard";

export interface Grant {
  id: string;
  person: string | null;
  kind: string;
  employer: string;
  symbol: string | null;
  units: number;
  strike: number | null;
  vested: number;
  unvested: number;
  expires_on: string | null;
  expired: boolean;
  price: number | null;
  value_vested: number | null;
  value_unvested: number | null;
  next_vest_on: string | null;
  source_document: string;
}

export interface Exposure {
  symbol: string;
  name: string;
  held: number;
  granted: number;
  weight: number;
  concentrated: boolean;
  plan: { method: string; target_weight: number; target_date: string; reached: boolean; overdue: boolean; document: string } | null;
  scenarios: { years: number; sold_a_year: number; units_a_year: number; gain_a_year: number | null }[];
}

export interface EquityHousehold {
  household_id: string;
  household: string;
  grants: Grant[];
  exposures: Exposure[];
  value_vested: number;
  value_unvested: number;
  value_vesting_12m: number;
  next_vest_on: string | null;
}

export const pct = (v: number) => `${(v * 100).toFixed(1)}%`;

export function PlanChips({ exposure: e }: { exposure: Exposure }) {
  return (
    <div className="flex flex-wrap gap-1.5" data-testid="exposure-chips">
      {e.concentrated && <span className="chip text-warn">concentrated</span>}
      {e.plan ? (
        <span className={`chip ${e.plan.overdue ? "text-crit" : e.plan.reached ? "text-ok" : "text-ink-muted"}`} title={e.plan.document}>
          {e.plan.method.replace("_", " ")} to {pct(e.plan.target_weight)} by {reviewDay(e.plan.target_date)}
        </span>
      ) : e.concentrated && <span className="chip text-crit" data-testid="exposure-no-plan">no plan</span>}
    </div>
  );
}

export function EquityCompCard({ result }: { result: any }) {
  const rows: EquityHousehold[] = Array.isArray(result?.households) ? result.households : [result];
  const lines = rows.flatMap((r) => r.exposures.map((e) => ({ r, e })));
  return (
    <div className="overflow-hidden rounded-lg border border-border bg-surface" data-testid="equity-card">
      <div className="flex flex-wrap items-center justify-between gap-2 border-b border-border-soft px-3 py-2">
        <div className="text-sm font-medium text-ink">Equity compensation and concentration</div>
        {result?.summary && (
          <div className="flex flex-wrap gap-1.5">
            <span className="chip text-ink-muted">{result.summary.with_grants} with grants</span>
            <span className="chip text-warn">{result.summary.concentrated} concentrated</span>
            <span className={`chip ${result.summary.no_plan ? "text-crit" : "text-ok"}`}>{result.summary.no_plan} no plan</span>
          </div>
        )}
      </div>
      {lines.length === 0 && rows.every((r) => r.grants.length === 0) ? (
        <div className="px-3 py-2"><span className="chip text-ink-muted">none on file</span></div>
      ) : (
        <div className="overflow-x-auto">
          <table className="w-full text-sm">
            <tbody className="divide-y divide-border-soft">
              {lines.slice(0, 8).map(({ r, e }) => (
                <tr key={`${r.household_id}${e.symbol}`}>
                  <td className="px-3 py-1.5 text-ink">{r.household}</td>
                  <td className="px-3 py-1.5 font-mono text-xs text-ink">{e.symbol}</td>
                  <td className="px-3 py-1.5 text-right font-mono text-xs tabular-nums text-ink-muted">{pct(e.weight)}</td>
                  <td className="px-3 py-1.5"><PlanChips exposure={e} /></td>
                </tr>
              ))}
              {rows.flatMap((r) => r.grants.map((g) => (
                <tr key={g.id}>
                  <td className="px-3 py-1.5 text-ink">{g.person ?? r.household}</td>
                  <td className="px-3 py-1.5 font-mono text-xs text-ink">{g.kind.toUpperCase()} {g.symbol ?? ""}</td>
                  <td className="px-3 py-1.5 text-right font-mono text-xs tabular-nums text-ink-muted">{g.value_vested != null ? money(g.value_vested + (g.value_unvested ?? 0)) : "—"}</td>
                  <td className="px-3 py-1.5">{g.next_vest_on && <span className="chip text-ink-muted">next vest {reviewDay(g.next_vest_on)}</span>}</td>
                </tr>
              ))).slice(0, 6)}
            </tbody>
          </table>
        </div>
      )}
    </div>
  );
}
