import { money, pct } from "@/lib/format";
import { CardHead, CardShell, Chip, LikelihoodBar, Tile, obj } from "./CanvasCardParts";

// The client's goals side by side (`read_my_goals`): aurea_core/goal_tradeoff.for_household, the engine every
// screen reads. A goal with a share of wealth set aside is funded from it; the rest share the pool. Every
// likelihood is an estimate; career earnings sit beside the goals and are not counted toward them.

export interface TradeoffGoal {
  id: string;
  name: string;
  kind: string;
  priority: number;
  target_amount: number;
  years: number;
  success_probability: number;
  on_track: boolean;
  projected_median: number;
  projected_p10: number;
  projected_p90: number;
  shortfall: number;
  funded_amount: number;
  share?: number | null;
  assumed?: string[];
}

export interface MyGoalsPayload {
  household?: { id: string; name: string };
  household_id: string;
  goals: TradeoffGoal[];
  invested?: number;
  shared_capital: number;
  human_capital: number;
  total_effective_capital: number;
  earnings?: { basis: string };
  tradeoff_matrix: any[];
  assumptions: { expected_return?: number; volatility?: number; sims?: number };
  current_age: number | null;
  retirement_age: number | null;
  annual_income: number | null;
  message?: string;
}

const isGoal = (g: any) => obj(g) && typeof g.id === "string" && typeof g.success_probability === "number" && typeof g.on_track === "boolean";

export function isMyGoals(r: any): boolean {
  return obj(r)
    && Array.isArray(r.goals) && r.goals.every(isGoal)
    && Array.isArray(r.tradeoff_matrix)
    && typeof r.shared_capital === "number"
    && typeof r.human_capital === "number";
}

export function MyGoalsCard({ result }: { result: MyGoalsPayload }) {
  const r = result;
  const onTrack = r.goals.filter((g) => g.on_track).length;
  return (
    <CardShell testid="my-goals-card">
      <CardHead title="Goals">
        <Chip tone={r.goals.length && onTrack === r.goals.length ? "text-ok" : r.goals.length ? "text-warn" : "text-ink-muted"}>
          {onTrack} of {r.goals.length} on track
        </Chip>
        <Chip testid="my-goals-estimate">estimate</Chip>
      </CardHead>
      <div className="grid grid-cols-3 divide-x divide-border-soft border-b border-border-soft">
        <Tile label="Invested" value={money(r.invested ?? r.shared_capital)} />
        <Tile label="Career earnings" value={r.earnings?.basis === "not_on_file" ? "not on file" : money(r.human_capital)} sub="not counted toward goals" />
        <Tile label="Shared pool" value={money(r.shared_capital)} sub="not set aside for a goal" />
      </div>
      {r.goals.length === 0 ? (
        <div className="px-3 py-2"><Chip>no goals on file</Chip></div>
      ) : (
        <ul className="divide-y divide-border-soft" data-testid="my-goals-list">
          {r.goals.map((g, i) => (
            <li key={g.id} className="px-3 py-1.5">
              <div className="flex items-center justify-between gap-2">
                <span className="min-w-0 truncate text-sm text-ink"><span className="mr-1.5 font-mono text-xs text-ink-muted">{i + 1}</span>{g.name}</span>
                <span className={`shrink-0 font-mono text-xs tabular-nums ${g.on_track ? "text-ok" : "text-warn"}`}>{pct(g.success_probability, 0)}</span>
              </div>
              <div className="mt-1"><LikelihoodBar value={g.success_probability} onTrack={g.on_track} /></div>
              <div className="mt-1 flex flex-wrap gap-1">
                <Chip>target {money(g.target_amount)}</Chip>
                <Chip>{g.years}y</Chip>
                {g.share != null ? <Chip>{pct(g.share, 0)} set aside</Chip> : <Chip>shares the pool</Chip>}
                {g.assumed?.includes("years") && <Chip tone="text-warn">assumed horizon</Chip>}
                <Chip>median {money(g.projected_median)}</Chip>
                {!g.on_track && g.shortfall > 0 && <Chip tone="text-warn">shortfall {money(g.shortfall)}</Chip>}
              </div>
            </li>
          ))}
        </ul>
      )}
    </CardShell>
  );
}
