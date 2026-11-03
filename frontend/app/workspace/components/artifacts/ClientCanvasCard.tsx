import { money, pct, titleCase } from "@/lib/format";
import { CardHead, CardShell, Chip, LikelihoodBar, Tile, obj } from "./CanvasCardParts";

// What a client sees on their Canvas home (aurea_core/client_view.home), for `read_client_canvas`
// (staff, members included) and `read_my_wealth` (the client, members dropped).

export interface CanvasGoal {
  name: string;
  kind: string;
  target_amount: number;
  on_track: boolean;
  probability: number;
  projected_median: number;
  horizon_years: number;
  assumed: string[];
  estimate: boolean;
}

export interface ClientCanvasPayload {
  household: { name?: string } & Record<string, any>;
  household_id: string;
  currency: string | null;
  adviser: { name: string | null; title: string | null; email: string | null } | null;
  members?: { id: string; name: string }[];
  total_wealth: number;
  allocation: Record<string, number>;
  data_confidence?: number;
  goals: CanvasGoal[];
  goals_on_track: number;
  stress: { scenario: string; impact_pct: number; estimate: boolean } | null;
  reports: { id: string; title: string; period: string; published_at: string | null }[];
  unread_messages: number;
  next_gen?: { id: string; name: string }[];
  as_of?: string;
  message?: string;
}

export function isClientCanvas(r: any): boolean {
  return obj(r)
    && "total_wealth" in r && typeof r.total_wealth === "number"
    && "goals_on_track" in r && typeof r.goals_on_track === "number"
    && Array.isArray(r.goals)
    && obj(r.allocation)
    && Array.isArray(r.reports)
    && typeof r.unread_messages === "number";
}

const TOP = 4;

export function ClientCanvasCard({ result }: { result: ClientCanvasPayload }) {
  const r = result;
  const ccy = r.currency ?? undefined;
  const classes = Object.entries(r.allocation).filter(([, v]) => typeof v === "number" && v > 0).sort((a, b) => b[1] - a[1]);
  const held = classes.reduce((s, [, v]) => s + v, 0);
  return (
    <CardShell testid="client-canvas-card">
      <CardHead title={r.household?.name ?? "Canvas"}>
        {r.adviser?.name ? <Chip testid="client-canvas-adviser">adviser {r.adviser.name}</Chip> : <Chip tone="text-warn">no adviser on file</Chip>}
        {r.members && <Chip>{r.members.length} members</Chip>}
      </CardHead>
      <div className="grid grid-cols-2 divide-x divide-border-soft border-b border-border-soft sm:grid-cols-4">
        <Tile label="Managed" value={money(r.total_wealth, ccy)} />
        <Tile label="Goals on track" value={`${r.goals_on_track} / ${r.goals.length}`} sub="estimate"
          tone={r.goals.length && r.goals_on_track === r.goals.length ? "text-ok" : r.goals.length ? "text-warn" : "text-ink-muted"} />
        <Tile label="Unread" value={r.unread_messages} tone={r.unread_messages ? "text-warn" : "text-ink-muted"} />
        <Tile label="Reports" value={r.reports.length} />
      </div>
      {(classes.length > 0 || r.stress) && (
        <div className="flex flex-wrap gap-1.5 border-b border-border-soft px-3 py-1.5" data-testid="client-canvas-allocation">
          {classes.slice(0, TOP).map(([cls, v]) => <Chip key={cls}>{titleCase(cls)} {pct(held ? v / held : 0, 0)}</Chip>)}
          {classes.length > TOP && <Chip>+{classes.length - TOP} more</Chip>}
          {r.stress && <Chip tone="text-crit">{titleCase(r.stress.scenario)} {pct(r.stress.impact_pct, 0)} · estimate</Chip>}
        </div>
      )}
      {r.goals.length > 0 && (
        <ul className="divide-y divide-border-soft" data-testid="client-canvas-goals">
          {r.goals.map((g, i) => (
            <li key={`${g.name}-${i}`} className="px-3 py-1.5">
              <div className="flex items-center justify-between gap-2">
                <span className="min-w-0 truncate text-sm text-ink">{g.name}</span>
                <span className={`shrink-0 font-mono text-xs tabular-nums ${g.on_track ? "text-ok" : "text-warn"}`}>{pct(g.probability, 0)}</span>
              </div>
              <div className="mt-1"><LikelihoodBar value={g.probability} onTrack={g.on_track} /></div>
              <div className="mt-1 flex flex-wrap gap-1">
                <Chip>target {money(g.target_amount, ccy)}</Chip>
                <Chip testid="client-canvas-goal-estimate">estimate</Chip>
                {g.assumed?.includes("years") && <Chip tone="text-warn">assumed {g.horizon_years}-year horizon</Chip>}
              </div>
            </li>
          ))}
        </ul>
      )}
    </CardShell>
  );
}
