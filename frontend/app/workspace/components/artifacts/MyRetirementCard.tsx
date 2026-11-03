import { money, pct } from "@/lib/format";
import { CardHead, CardShell, Chip, LikelihoodBar, Tile, obj } from "./CanvasCardParts";

// The client's retirement projection (`read_my_retirement`): aurea_core/retirement.for_household,
// plus `what_if` - the levers the client asked about, or null. Every figure is a Monte Carlo estimate.

export interface MyRetirementPayload {
  household?: { id: string; name: string };
  household_id: string;
  current_age: number;
  retirement_age: number;
  longevity_age: number;
  years_to_retirement: number;
  income_target: number;
  income_need?: number;
  income_basis?: "stated" | "recorded_spending" | "assumed_draw";
  guaranteed_income?: number;
  income_at_retirement: number;
  projected_at_retirement: number;
  success_probability: number;
  on_track: boolean;
  target_success: number;
  median_depletion_age: number | null;
  sustainable_income: number;
  income_gap: number;
  levers?: { key: string; label: string; success: number }[];
  what_if: Record<string, number> | null;
  message?: string;
}

export function isMyRetirement(r: any): boolean {
  return obj(r)
    && typeof r.success_probability === "number"
    && typeof r.retirement_age === "number"
    && typeof r.income_target === "number"
    && typeof r.sustainable_income === "number"
    && Array.isArray(r.balance_by_age)
    && "what_if" in r && (r.what_if === null || obj(r.what_if));
}

const BASIS: Record<string, [string, string]> = {
  stated: ["income stated", "text-ink-muted"],
  recorded_spending: ["income from spending on file", "text-ink-muted"],
  assumed_draw: ["income assumed · 5% draw", "text-warn"],
};

export function MyRetirementCard({ result }: { result: MyRetirementPayload }) {
  const r = result;
  const basis = r.income_basis ? BASIS[r.income_basis] : null;
  const short = r.income_gap > 0;
  return (
    <CardShell testid="my-retirement-card">
      <CardHead title="Retirement">
        <Chip tone={r.on_track ? "text-ok" : "text-warn"}>{r.on_track ? "on track" : "below target"}</Chip>
        <Chip testid="my-retirement-estimate">estimate</Chip>
        {r.what_if && <Chip testid="my-retirement-what-if">what-if, nothing saved</Chip>}
      </CardHead>
      <div className="grid grid-cols-3 divide-x divide-border-soft border-b border-border-soft">
        <Tile label="Likelihood" value={pct(r.success_probability, 0)} tone={r.on_track ? "text-ok" : "text-warn"}
          sub={r.target_success != null ? `target ${pct(r.target_success, 0)}` : undefined} />
        <Tile label="Sustainable" value={money(r.sustainable_income)} sub="per year · today's money" />
        <Tile label={short ? "Shortfall" : "Headroom"} value={money(Math.abs(r.income_gap))} tone={short ? "text-warn" : "text-ok"} sub="per year" />
      </div>
      <div className="px-3 py-2"><LikelihoodBar value={r.success_probability} onTrack={r.on_track} /></div>
      <div className="flex flex-wrap gap-1.5 border-t border-border-soft px-3 py-1.5">
        <Chip>retire at {r.retirement_age}</Chip>
        <Chip>plan to {r.longevity_age}</Chip>
        <Chip>income {money(r.income_target)}/yr from the portfolio</Chip>
        <Chip>at retirement {money(r.projected_at_retirement)} median</Chip>
        {r.median_depletion_age != null && <Chip tone="text-warn">median depletion age {r.median_depletion_age}</Chip>}
        {!!r.guaranteed_income && r.guaranteed_income > 0 && <Chip>guaranteed {money(r.guaranteed_income)}/yr</Chip>}
        {basis && <Chip tone={basis[1]}>{basis[0]}</Chip>}
      </div>
      {r.levers && r.levers.length > 0 && (
        <table className="w-full table-fixed border-t border-border-soft text-sm">
          <tbody className="divide-y divide-border-soft">
            {r.levers.map((l) => (
              <tr key={l.key}>
                <td className="truncate px-3 py-1.5 text-ink-muted">{l.label}</td>
                <td className={`w-16 px-3 py-1.5 text-right font-mono text-xs tabular-nums ${l.success >= r.target_success ? "text-ok" : "text-ink"}`}>{pct(l.success, 0)}</td>
              </tr>
            ))}
          </tbody>
        </table>
      )}
    </CardShell>
  );
}
