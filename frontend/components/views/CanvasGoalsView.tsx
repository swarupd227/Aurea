"use client";

import { useEffect, useRef, useState } from "react";
import {
  ArrowRight, Briefcase, ChevronDown, ChevronUp, Gift, GraduationCap, Home, Minus, Plus, RotateCcw, Target, TrendingUp,
} from "lucide-react";
import { Card, Empty, ErrorState, SkeletonList } from "@/components/ui";
import { api } from "@/lib/api";
import { money, num, pct } from "@/lib/format";
import { CanvasHouseholdPicker } from "./CanvasView";
import { useRole } from "./CanvasRetirementView";
import { AllClients, ViewHeader } from "./ViewHeader";

const ROUTE = "/canvas/goals";
const ENDPOINT = "/api/canvas/goals";

const KIND_ICON: Record<string, any> = {
  retirement: TrendingUp, education: GraduationCap, property: Home, legacy: Gift, other: Target,
};

type Override = { years_delta?: number; target_scale?: number };

const BAR: Record<string, string> = { ok: "bg-ok", warn: "bg-warn", crit: "bg-crit" };
const likelihoodTone = (g: any) => (g.on_track ? "ok" : g.success_probability >= 0.5 ? "warn" : "crit");

function Ring({ prob, tone, size = 64 }: { prob: number; tone: string; size?: number }) {
  const r = (size - 10) / 2;
  const circ = 2 * Math.PI * r;
  const filled = circ * Math.max(0, Math.min(1, prob));
  const color = `var(--color-${tone})`;
  return (
    <svg width={size} height={size} className="shrink-0" aria-hidden="true">
      <circle cx={size / 2} cy={size / 2} r={r} fill="none" stroke="var(--color-border-soft)" strokeWidth={7} />
      <circle
        cx={size / 2} cy={size / 2} r={r} fill="none" stroke={color} strokeWidth={7}
        strokeDasharray={`${filled} ${circ - filled}`} strokeLinecap="round"
        transform={`rotate(-90 ${size / 2} ${size / 2})`} style={{ transition: "stroke-dasharray 0.6s ease" }}
      />
      <text x="50%" y="50%" textAnchor="middle" dominantBaseline="central" fontSize={13} fontWeight="600" fill={color}>
        {Math.round(prob * 100)}%
      </text>
    </svg>
  );
}

function Delta({ delta }: { delta: number }) {
  if (Math.abs(delta) < 0.005) return <span className="text-xs text-ink-muted">—</span>;
  return <span className={`font-mono text-xs font-semibold tabular-nums ${delta > 0 ? "text-ok" : "text-crit"}`}>{delta > 0 ? "+" : ""}{Math.round(delta * 100)}pp</span>;
}

function Stepper({ label, value, onDown, onUp, downOff, upOff, testid }: {
  label: string; value: string; onDown: () => void; onUp: () => void; downOff: boolean; upOff: boolean; testid: string;
}) {
  return (
    <div>
      <div className="tile-label mb-1">{label}</div>
      <div className="flex items-center gap-2">
        <button onClick={onDown} disabled={downOff} aria-label={`${label} down`} data-testid={`${testid}-down`}
          className="btn-outline h-7 w-7 rounded-lg p-0"><Minus size={12} aria-hidden="true" /></button>
        <span className="w-20 text-center font-mono text-sm font-semibold tabular-nums text-ink">{value}</span>
        <button onClick={onUp} disabled={upOff} aria-label={`${label} up`} data-testid={`${testid}-up`}
          className="btn-outline h-7 w-7 rounded-lg p-0"><Plus size={12} aria-hidden="true" /></button>
      </div>
    </div>
  );
}

function GoalCard({ goal, rank, total, onMoveUp, onMoveDown, onOverride, overrides }: {
  goal: any; rank: number; total: number; onMoveUp: () => void; onMoveDown: () => void;
  onOverride: (patch: Override) => void; overrides: Override;
}) {
  const Icon = KIND_ICON[goal.kind] || Target;
  const yearsDelta = overrides.years_delta || 0;
  const scale = overrides.target_scale || 1.0;
  const tone = likelihoodTone(goal);
  return (
    <Card className={`p-4 ${goal.on_track ? "" : "border-warn"}`} data-testid="canvas-goal">
      <div className="flex items-start gap-3">
        <div className="flex flex-col items-center gap-0.5 pt-1">
          <button onClick={onMoveUp} disabled={rank === 1} aria-label="Raise priority" data-testid="canvas-goal-up"
            className="rounded p-0.5 text-ink-muted hover:bg-border-soft disabled:cursor-not-allowed disabled:opacity-30">
            <ChevronUp size={16} aria-hidden="true" />
          </button>
          <span className="w-4 text-center font-mono text-xs font-semibold text-ink-muted">{rank}</span>
          <button onClick={onMoveDown} disabled={rank === total} aria-label="Lower priority" data-testid="canvas-goal-down"
            className="rounded p-0.5 text-ink-muted hover:bg-border-soft disabled:cursor-not-allowed disabled:opacity-30">
            <ChevronDown size={16} aria-hidden="true" />
          </button>
        </div>
        <span className="mt-1 hidden h-9 w-9 shrink-0 items-center justify-center rounded-xl bg-border-soft text-ink-soft cq-sm:flex">
          <Icon size={17} aria-hidden="true" />
        </span>
        <div className="min-w-0 flex-1">
          <div className="flex items-start justify-between gap-3">
            <div className="min-w-0">
              <div className="truncate text-sm font-medium text-ink">{goal.name}</div>
              <div className="mt-1 flex flex-wrap gap-1.5">
                <span className="chip bg-border-soft text-ink-soft tabular-nums">target {money(goal.target_amount)}</span>
                <span className="chip bg-border-soft text-ink-soft tabular-nums">{goal.years}y</span>
                {!goal.on_track && goal.shortfall > 0 && (
                  <span className="chip bg-warn-bg text-warn tabular-nums">shortfall {money(goal.shortfall)}</span>
                )}
                <span className="chip bg-border-soft text-ink-muted">estimate</span>
              </div>
            </div>
            <Ring prob={goal.success_probability} tone={tone} />
          </div>
          <div className="mt-3 h-1.5 w-full overflow-hidden rounded-full bg-border-soft">
            <div className={`h-full rounded-full ${BAR[tone]} transition-all duration-700`} style={{ width: `${Math.round(goal.success_probability * 100)}%` }} />
          </div>
          <div className="mt-1 flex justify-between gap-2 font-mono text-[11px] tabular-nums text-ink-muted">
            <span>p10 {money(goal.projected_p10)}</span>
            <span>median {money(goal.projected_median)}</span>
            <span>p90 {money(goal.projected_p90)}</span>
          </div>
          <div className="mt-3 grid grid-cols-1 gap-3 border-t border-border-soft pt-3 cq-sm:grid-cols-2">
            <Stepper label="Timeline" testid="canvas-goal-years"
              value={yearsDelta === 0 ? "as on file" : yearsDelta > 0 ? `+${yearsDelta}y` : `${yearsDelta}y`}
              onDown={() => onOverride({ years_delta: Math.max(-5, yearsDelta - 1) })}
              onUp={() => onOverride({ years_delta: Math.min(10, yearsDelta + 1) })}
              downOff={yearsDelta <= -5} upOff={yearsDelta >= 10} />
            <Stepper label="Target amount" testid="canvas-goal-target"
              value={`${Math.round(scale * 100)}%`}
              onDown={() => onOverride({ target_scale: Math.max(0.5, parseFloat((scale - 0.1).toFixed(1))) })}
              onUp={() => onOverride({ target_scale: Math.min(1.5, parseFloat((scale + 0.1).toFixed(1))) })}
              downOff={scale <= 0.5} upOff={scale >= 1.5} />
          </div>
        </div>
      </div>
    </Card>
  );
}

/** The engine's lever labels by key, so an amount reads in the firm's currency. */
function leverLabel(lev: any): string {
  if (lev.key === "contribute_5k") return `Add ${money(5000)}/yr contribution`;
  return lev.label;
}

function TradeoffMatrix({ matrix }: { matrix: any[] }) {
  const [open, setOpen] = useState<string | null>(matrix[0]?.goal_id || null);
  if (!matrix.length) return null;
  return (
    <Card className="p-4" data-testid="canvas-goals-tradeoff">
      <div className="mb-3 flex flex-wrap items-center gap-1.5">
        <span className="tile-label flex items-center gap-1.5"><ArrowRight size={13} aria-hidden="true" /> What if</span>
        <span className="chip bg-border-soft text-ink-muted">estimate</span>
      </div>
      <div className="mb-3 flex flex-wrap gap-1.5">
        {matrix.map((m) => (
          <button key={m.goal_id} onClick={() => setOpen(open === m.goal_id ? null : m.goal_id)} data-testid="canvas-goals-tradeoff-goal"
            className={`chip transition ${open === m.goal_id ? "border border-ink-soft bg-border text-ink" : "border border-border bg-border-soft text-ink-soft hover:bg-border"}`}>
            {m.goal_name}
          </button>
        ))}
      </div>
      {matrix.filter((m) => m.goal_id === open).map((m) => (
        <div key={m.goal_id} className="space-y-3">
          {m.levers.map((lev: any) => (
            <div key={lev.key} className="rounded-xl border border-border-soft p-3">
              <div className="mb-2 text-xs font-medium text-ink">{leverLabel(lev)}</div>
              <div className="space-y-1.5">
                {lev.impact.map((imp: any) => (
                  <div key={imp.id} className="flex items-center gap-3 text-xs">
                    <span className="w-28 truncate text-ink-soft cq-sm:w-36">{imp.name}</span>
                    <div className="h-1.5 flex-1 overflow-hidden rounded-full bg-border-soft">
                      <div className={`h-full rounded-full ${imp.success_probability >= 0.7 ? "bg-ok" : imp.success_probability >= 0.5 ? "bg-warn" : "bg-crit"}`}
                        style={{ width: `${Math.round(imp.success_probability * 100)}%`, transition: "width 0.5s" }} />
                    </div>
                    <span className="w-9 text-right font-mono tabular-nums text-ink">{Math.round(imp.success_probability * 100)}%</span>
                    <span className="w-12 text-right"><Delta delta={imp.delta} /></span>
                  </div>
                ))}
              </div>
            </div>
          ))}
        </div>
      ))}
    </Card>
  );
}

function Row({ label, value }: { label: string; value: React.ReactNode }) {
  return (
    <div className="flex justify-between gap-2 text-xs">
      <span className="text-ink-muted">{label}</span>
      <span className="font-mono tabular-nums text-ink">{value}</span>
    </div>
  );
}

/** One household's goals, side by side on one pool of capital. Reordering and the steppers are
 * what-ifs: each re-asks the engine (POST, nothing saved) and the household's goals stay as filed. */
function Goals({ householdId, staff }: { householdId: string | null; staff: boolean }) {
  const [plan, setPlan] = useState<any>(null);
  const [loading, setLoading] = useState(true);
  const [refreshing, setRefreshing] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [order, setOrder] = useState<string[]>([]);
  const [overrides, setOverrides] = useState<Record<string, Override>>({});
  const debounce = useRef<ReturnType<typeof setTimeout> | null>(null);
  const path = householdId ? `${ENDPOINT}?household_id=${encodeURIComponent(householdId)}` : ENDPOINT;

  async function fetchPlan(nextOrder: string[], ov: Record<string, Override>, quiet = false) {
    if (quiet) setRefreshing(true); else setLoading(true);
    setError(null);
    try {
      const body: any = {};
      if (nextOrder.length) body.priorities = Object.fromEntries(nextOrder.map((id, i) => [id, i + 1]));
      const goalOverrides = Object.fromEntries(Object.entries(ov).filter(([, p]) => Object.keys(p || {}).length));
      if (Object.keys(goalOverrides).length) body.goal_overrides = goalOverrides;
      const result = await api(path, { method: "POST", body });
      setPlan(result);
      if (!nextOrder.length) setOrder((result.goals || []).map((g: any) => g.id));
    } catch (e: any) {
      setError(e?.message || "Failed to load");
    } finally {
      setLoading(false);
      setRefreshing(false);
    }
  }

  useEffect(() => {
    setOverrides({});
    setOrder([]);
    fetchPlan([], {});
    return () => { if (debounce.current) clearTimeout(debounce.current); };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [path]);

  function refresh(nextOrder: string[], ov: Record<string, Override>) {
    if (debounce.current) clearTimeout(debounce.current);
    debounce.current = setTimeout(() => fetchPlan(nextOrder, ov, true), 600);
  }

  function move(id: string, dir: -1 | 1) {
    const next = [...order];
    const i = next.indexOf(id);
    const j = i + dir;
    if (i < 0 || j < 0 || j >= next.length) return;
    [next[i], next[j]] = [next[j], next[i]];
    setOrder(next);
    refresh(next, overrides);
  }

  function patch(id: string, p: Override) {
    const next = { ...overrides, [id]: { ...(overrides[id] || {}), ...p } };
    setOverrides(next);
    refresh(order, next);
  }

  function reset() {
    if (debounce.current) clearTimeout(debounce.current);
    setOverrides({});
    setOrder([]);
    fetchPlan([], {});
  }

  const name: string | undefined = plan?.household?.name;
  const title = staff && name ? `Goals · ${name}` : "Goals";

  if (error && !plan) {
    return <><ViewHeader title={title} actions={staff ? <AllClients href={ROUTE} /> : undefined} />
      <Card><ErrorState what="the goals" message={error} onRetry={reset} /></Card></>;
  }
  if (loading || !plan) return <SkeletonList count={4} />;

  const goals: any[] = plan.goals || [];
  const inOrder = order.length ? order.map((id) => goals.find((g) => g.id === id)).filter(Boolean) : goals;
  const onTrack = goals.filter((g) => g.on_track).length;
  const filedOrder = JSON.stringify(goals.map((g) => g.id));
  const whatIf = Object.values(overrides).some((v) => Object.keys(v || {}).length > 0);
  const reordered = order.length > 0 && JSON.stringify(order) !== filedOrder;
  const hc = plan.human_capital ?? 0;
  const total = plan.total_effective_capital ?? 0;
  const a = plan.assumptions || {};

  return (
    <>
      <ViewHeader
        title={title}
        meta={<>
          <span className={`chip ${onTrack === goals.length ? "bg-ok-bg text-ok" : "bg-warn-bg text-warn"} tabular-nums`} data-testid="canvas-goals-on-track">
            {onTrack} of {goals.length} on track
          </span>
          <span className="chip bg-border-soft text-ink-soft">estimate</span>
          {staff && <span className="chip bg-border-soft text-ink-soft">client view</span>}
          {(whatIf || reordered) && <span className="chip bg-border-soft text-ink-soft" data-testid="canvas-goals-what-if">what-if · nothing saved</span>}
          {refreshing && <span className="chip bg-border-soft text-ink-muted">updating</span>}
          {error && <span className="chip bg-crit-bg text-crit">update failed</span>}
        </>}
        actions={<>
          {(whatIf || reordered) && (
            <button onClick={reset} className="btn-ghost px-2 py-1 text-xs" data-testid="canvas-goals-reset">
              <RotateCcw size={12} aria-hidden="true" /> Reset
            </button>
          )}
          {staff && <AllClients href={ROUTE} />}
        </>}
      />

      <div className="mb-4 grid grid-cols-2 gap-3 cq-md:grid-cols-4" data-testid="canvas-goals-summary">
        {[
          ["Invested capital", money(plan.shared_capital), "portfolio today"],
          ["Human capital", money(hc), "career earnings · PV · estimate"],
          ["Effective capital", money(total), "portfolio + career · estimate"],
          ["Goals on track", `${onTrack} / ${goals.length}`, "estimate"],
        ].map(([label, v, sub]) => (
          <Card key={label} className="p-4">
            <div className="tile-label">{label}</div>
            <div className="mt-1 font-mono text-xl tabular-nums text-ink">{v}</div>
            <div className="mt-1 text-[11px] text-ink-muted">{sub}</div>
          </Card>
        ))}
      </div>

      <div className="grid gap-4 cq-md:grid-cols-5">
        <div className="space-y-3 cq-md:col-span-3" data-testid="canvas-goals-list">
          {goals.length > 0 && (
            <div className="flex flex-wrap items-center gap-1.5">
              <span className="tile-label">Priority order</span>
              <span className="chip bg-border-soft text-ink-muted">1 draws first</span>
            </div>
          )}
          {inOrder.map((goal: any, i: number) => (
            <GoalCard key={goal.id} goal={goal} rank={i + 1} total={inOrder.length}
              onMoveUp={() => move(goal.id, -1)} onMoveDown={() => move(goal.id, 1)}
              onOverride={(p) => patch(goal.id, p)} overrides={overrides[goal.id] || {}} />
          ))}
          {goals.length === 0 && <Card><Empty icon={<Target size={28} />}>No goals on file.</Empty></Card>}
        </div>

        <div className="space-y-4 cq-md:col-span-2">
          <Card className="p-4" data-testid="canvas-goals-human-capital">
            <div className="mb-2 flex flex-wrap items-center gap-1.5">
              <span className="tile-label flex items-center gap-1.5"><Briefcase size={13} aria-hidden="true" /> Career as an asset</span>
              <span className="chip bg-border-soft text-ink-muted">estimate</span>
            </div>
            <div className="space-y-1.5">
              <Row label="Human capital · PV" value={money(hc)} />
              <Row label="Current age" value={plan.current_age ?? "not on file"} />
              <Row label="Retirement age" value={plan.retirement_age ?? "not on file"} />
              <Row label="Annual income" value={plan.annual_income != null ? money(plan.annual_income) : "not on file"} />
              {plan.current_age != null && plan.retirement_age != null && (
                <Row label="Working years left" value={Math.max(0, plan.retirement_age - plan.current_age)} />
              )}
            </div>
            {total > 0 && (
              <div className="mt-3 border-t border-border-soft pt-3">
                <div className="mb-1 flex justify-between text-xs text-ink-muted">
                  <span>Career earnings</span><span className="font-mono tabular-nums">{pct(hc / total, 0)}</span>
                </div>
                <div className="h-2 overflow-hidden rounded-full bg-border-soft">
                  <div className="h-full rounded-full bg-ink-muted" style={{ width: `${Math.round((hc / total) * 100)}%` }} />
                </div>
                <div className="mt-1 flex justify-between text-xs text-ink-muted">
                  <span>Portfolio</span><span className="font-mono tabular-nums">{pct((plan.shared_capital ?? 0) / total, 0)}</span>
                </div>
              </div>
            )}
          </Card>

          <Card className="p-4" data-testid="canvas-goals-assumptions">
            <div className="mb-2 flex flex-wrap items-center gap-1.5">
              <span className="tile-label">Projection assumptions</span>
              <span className="chip bg-border-soft text-ink-muted">estimate</span>
            </div>
            <div className="space-y-1.5">
              <Row label="Expected return" value={a.expected_return != null ? pct(a.expected_return, 1) : "—"} />
              <Row label="Volatility" value={a.volatility != null ? pct(a.volatility, 1) : "—"} />
              <Row label="Simulations" value={a.sims != null ? num(a.sims) : "—"} />
            </div>
          </Card>
        </div>
      </div>

      {plan.tradeoff_matrix?.length > 0 && (
        <div className="mt-4"><TradeoffMatrix key={path} matrix={plan.tradeoff_matrix} /></div>
      )}
    </>
  );
}

/** The client's goals trade-off. A client sees their own household whatever is asked for (the server
 * resolves it); staff pick a household, then see what that client sees. */
export function CanvasGoalsView({ household = null }: { household?: string | null }) {
  const role = useRole();
  let body;
  if (role === undefined) body = <SkeletonList count={4} />;
  else if (role === "client") body = <Goals householdId={null} staff={false} />;
  else if (!household) body = <CanvasHouseholdPicker route={ROUTE} title="Goals" testId="canvas-goals-households" />;
  else body = <Goals key={household} householdId={household} staff />;
  return <div className="view-container" data-testid="canvas-goals-view">{body}</div>;
}
