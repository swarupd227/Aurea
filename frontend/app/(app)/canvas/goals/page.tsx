"use client";
import { useCallback, useEffect, useRef, useState } from "react";
import {
  Target, ChevronUp, ChevronDown, AlertTriangle, CheckCircle2,
  TrendingUp, Briefcase, GraduationCap, Home, Gift, Info,
  RefreshCw, ArrowRight, Minus, Plus,
} from "lucide-react";
import PageHeader from "@/components/PageHeader";
import { Card, Spinner } from "@/components/ui";
import { api, getUser } from "@/lib/api";
import { money, pct } from "@/lib/format";

// ── helpers ───────────────────────────────────────────────────────────────────

const KIND_ICON: Record<string, any> = {
  retirement: TrendingUp, education: GraduationCap, property: Home,
  legacy: Gift, other: Target,
};

const KIND_COLOR: Record<string, string> = {
  retirement: "bg-navy-700", education: "bg-gold-600",
  property: "bg-emerald-600", legacy: "bg-purple-600", other: "bg-slate-500",
};

function SuccessRing({ prob, size = 80 }: { prob: number; size?: number }) {
  const r = (size - 10) / 2;
  const circ = 2 * Math.PI * r;
  const filled = circ * prob;
  const color = prob >= 0.85 ? "#22c55e" : prob >= 0.70 ? "#f59e0b" : "#ef4444";
  return (
    <svg width={size} height={size} className="shrink-0">
      <circle cx={size / 2} cy={size / 2} r={r} fill="none" stroke="#e2e8f0" strokeWidth={8} />
      <circle
        cx={size / 2} cy={size / 2} r={r} fill="none"
        stroke={color} strokeWidth={8}
        strokeDasharray={`${filled} ${circ - filled}`}
        strokeLinecap="round"
        transform={`rotate(-90 ${size / 2} ${size / 2})`}
        style={{ transition: "stroke-dasharray 0.6s ease" }}
      />
      <text x="50%" y="50%" textAnchor="middle" dominantBaseline="central"
            fontSize={size < 70 ? 11 : 14} fontWeight="700" fill={color}>
        {Math.round(prob * 100)}%
      </text>
    </svg>
  );
}

function DeltaBadge({ delta }: { delta: number }) {
  if (Math.abs(delta) < 0.005) return <span className="text-xs text-ink-muted">—</span>;
  const pos = delta > 0;
  return (
    <span className={`inline-flex items-center gap-0.5 text-xs font-semibold ${pos ? "text-positive" : "text-critical"}`}>
      {pos ? "+" : ""}{Math.round(delta * 100)}pp
    </span>
  );
}

// ── Goal card ─────────────────────────────────────────────────────────────────

function GoalCard({
  goal, rank, total, onMoveUp, onMoveDown, onOverride, overrides,
}: {
  goal: any; rank: number; total: number;
  onMoveUp: () => void; onMoveDown: () => void;
  onOverride: (patch: any) => void;
  overrides: any;
}) {
  const Icon = KIND_ICON[goal.kind] || Target;
  const colorClass = KIND_COLOR[goal.kind] || "bg-slate-500";
  const yearsDelta = overrides.years_delta || 0;
  const targetScale = overrides.target_scale || 1.0;

  return (
    <Card className={`transition-all ${!goal.on_track ? "border-caution/40" : ""}`}>
      <div className="flex items-start gap-4">
        {/* Priority handle */}
        <div className="flex flex-col items-center gap-0.5 pt-1">
          <button
            onClick={onMoveUp} disabled={rank === 1}
            className="p-0.5 rounded hover:bg-navy-100 disabled:opacity-30 disabled:cursor-not-allowed transition"
            title="Increase priority"
          >
            <ChevronUp size={16} className="text-navy-400" />
          </button>
          <span className="text-xs font-bold text-navy-400 w-4 text-center">{rank}</span>
          <button
            onClick={onMoveDown} disabled={rank === total}
            className="p-0.5 rounded hover:bg-navy-100 disabled:opacity-30 disabled:cursor-not-allowed transition"
            title="Decrease priority"
          >
            <ChevronDown size={16} className="text-navy-400" />
          </button>
        </div>

        {/* Icon */}
        <div className={`h-10 w-10 rounded-xl ${colorClass} text-white flex items-center justify-center shrink-0 mt-1`}>
          <Icon size={18} />
        </div>

        {/* Body */}
        <div className="flex-1 min-w-0">
          <div className="flex items-start justify-between gap-3">
            <div>
              <div className="font-semibold text-ink text-sm">{goal.name}</div>
              <div className="text-xs text-ink-muted mt-0.5">
                Target {money(goal.target_amount * targetScale)} in {goal.years + yearsDelta}y
                {!goal.on_track && (
                  <span className="ml-2 inline-flex items-center gap-1 text-caution">
                    <AlertTriangle size={11} /> {money(goal.shortfall)} shortfall
                  </span>
                )}
              </div>
            </div>
            <SuccessRing prob={goal.success_probability} />
          </div>

          {/* Success bar */}
          <div className="mt-3 h-1.5 w-full bg-navy-100 rounded-full overflow-hidden">
            <div
              className={`h-full rounded-full transition-all duration-700 ${goal.on_track ? "bg-positive" : goal.success_probability >= 0.5 ? "bg-caution" : "bg-critical"}`}
              style={{ width: `${Math.round(goal.success_probability * 100)}%` }}
            />
          </div>
          <div className="flex justify-between text-[11px] text-ink-muted mt-1">
            <span>p10 {money(goal.projected_p10)}</span>
            <span>median {money(goal.projected_median)}</span>
            <span>p90 {money(goal.projected_p90)}</span>
          </div>

          {/* What-if controls */}
          <div className="mt-4 pt-3 border-t border-navy-100 grid grid-cols-2 gap-3">
            {/* Delay slider */}
            <div>
              <label className="text-[11px] font-semibold text-ink-muted uppercase tracking-wide block mb-1">
                Delay timeline
              </label>
              <div className="flex items-center gap-2">
                <button
                  onClick={() => onOverride({ years_delta: Math.max(-5, yearsDelta - 1) })}
                  className="h-7 w-7 rounded-lg border border-navy-200 flex items-center justify-center hover:bg-navy-50 transition"
                  disabled={yearsDelta <= -5}
                >
                  <Minus size={12} />
                </button>
                <span className="text-sm font-semibold text-ink w-16 text-center">
                  {yearsDelta === 0 ? "No change" : yearsDelta > 0 ? `+${yearsDelta}y later` : `${yearsDelta}y earlier`}
                </span>
                <button
                  onClick={() => onOverride({ years_delta: Math.min(10, yearsDelta + 1) })}
                  className="h-7 w-7 rounded-lg border border-navy-200 flex items-center justify-center hover:bg-navy-50 transition"
                  disabled={yearsDelta >= 10}
                >
                  <Plus size={12} />
                </button>
              </div>
            </div>

            {/* Target scale */}
            <div>
              <label className="text-[11px] font-semibold text-ink-muted uppercase tracking-wide block mb-1">
                Target amount
              </label>
              <div className="flex items-center gap-2">
                <button
                  onClick={() => onOverride({ target_scale: Math.max(0.5, parseFloat((targetScale - 0.1).toFixed(1))) })}
                  className="h-7 w-7 rounded-lg border border-navy-200 flex items-center justify-center hover:bg-navy-50 transition"
                  disabled={targetScale <= 0.5}
                >
                  <Minus size={12} />
                </button>
                <span className="text-sm font-semibold text-ink w-16 text-center">
                  {targetScale === 1.0 ? "100%" : `${Math.round(targetScale * 100)}%`}
                </span>
                <button
                  onClick={() => onOverride({ target_scale: Math.min(1.5, parseFloat((targetScale + 0.1).toFixed(1))) })}
                  className="h-7 w-7 rounded-lg border border-navy-200 flex items-center justify-center hover:bg-navy-50 transition"
                  disabled={targetScale >= 1.5}
                >
                  <Plus size={12} />
                </button>
              </div>
            </div>
          </div>
        </div>
      </div>
    </Card>
  );
}

// ── Trade-off matrix ──────────────────────────────────────────────────────────

function TradeoffMatrix({ matrix, goals }: { matrix: any[]; goals: any[] }) {
  const [open, setOpen] = useState<string | null>(matrix[0]?.goal_id || null);

  if (!matrix.length) return null;

  const goalName = (id: string) => goals.find((g) => g.id === id)?.name || id;

  return (
    <Card>
      <div className="font-semibold text-ink mb-1 flex items-center gap-2">
        <ArrowRight size={16} className="text-navy-400" /> What happens if…
      </div>
      <p className="text-xs text-ink-muted mb-4">
        Pick a goal and see how each lever changes success probabilities across the full plan.
      </p>
      <div className="flex flex-wrap gap-2 mb-4">
        {matrix.map((m) => (
          <button
            key={m.goal_id}
            onClick={() => setOpen(open === m.goal_id ? null : m.goal_id)}
            className={`chip transition ${open === m.goal_id ? "bg-navy-800 text-white" : "bg-navy-50 text-navy-700 hover:bg-navy-100"}`}
          >
            {m.goal_name}
          </button>
        ))}
      </div>

      {matrix.filter((m) => m.goal_id === open).map((m) => (
        <div key={m.goal_id} className="space-y-3">
          {m.levers.map((lev: any) => (
            <div key={lev.key} className="rounded-xl border border-navy-100 p-3">
              <div className="text-xs font-semibold text-ink mb-2">{lev.label}</div>
              <div className="space-y-1.5">
                {lev.impact.map((imp: any) => (
                  <div key={imp.id} className="flex items-center gap-3 text-xs">
                    <span className="w-36 truncate text-ink-soft">{imp.name}</span>
                    <div className="flex-1 h-1.5 bg-navy-100 rounded-full overflow-hidden">
                      <div
                        className={`h-full rounded-full ${imp.success_probability >= 0.7 ? "bg-positive" : imp.success_probability >= 0.5 ? "bg-caution" : "bg-critical"}`}
                        style={{ width: `${Math.round(imp.success_probability * 100)}%`, transition: "width 0.5s" }}
                      />
                    </div>
                    <span className="w-8 text-right font-medium text-ink">{Math.round(imp.success_probability * 100)}%</span>
                    <span className="w-14 text-right"><DeltaBadge delta={imp.delta} /></span>
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

// ── Main page ─────────────────────────────────────────────────────────────────

export default function GoalsPage() {
  const user = typeof window !== "undefined" ? getUser() : null;
  const isClient = user?.role === "client";

  const [households, setHouseholds] = useState<any[]>([]);
  const [hid, setHid] = useState<string | null>(null);
  const [plan, setPlan] = useState<any>(null);
  const [loading, setLoading] = useState(true);
  const [refreshing, setRefreshing] = useState(false);

  // Priority order: array of goal ids in priority order (index 0 = rank 1)
  const [priorityOrder, setPriorityOrder] = useState<string[]>([]);
  // Per-goal what-if overrides: {goal_id: {years_delta, target_scale}}
  const [overrides, setOverrides] = useState<Record<string, any>>({});

  const debounceRef = useRef<ReturnType<typeof setTimeout> | null>(null);

  // Load households (staff) or resolve automatically (client)
  useEffect(() => {
    (async () => {
      if (!isClient) {
        const hh = await api("/api/core/households");
        setHouseholds(hh);
        setHid(hh[0]?.id || null);
      } else {
        await fetchPlan(null, [], {});
        setLoading(false);
      }
    })();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  useEffect(() => {
    if (!isClient && hid) {
      setOverrides({});
      setPriorityOrder([]);
      fetchPlan(hid, [], {});
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [hid]);

  async function fetchPlan(
    householdId: string | null,
    order: string[],
    ov: Record<string, any>,
    quiet = false,
  ) {
    if (!quiet) setLoading(true); else setRefreshing(true);
    try {
      const priorities: Record<string, number> = {};
      order.forEach((id, i) => { priorities[id] = i + 1; });

      const goalOverrides: Record<string, any> = {};
      Object.entries(ov).forEach(([id, patch]) => {
        if (Object.keys(patch).length) goalOverrides[id] = patch;
      });

      const endpoint = isClient ? "/api/canvas/goals" : `/api/core/households/${householdId}/goal-tradeoff`;
      const body: any = {};
      if (order.length) body.priorities = priorities;
      if (Object.keys(goalOverrides).length) body.goal_overrides = goalOverrides;
      if (householdId && !isClient) body.household_id = householdId;

      const result = await api(endpoint, { body });
      setPlan(result);
      // Initialise priority order from API on first load
      if (!order.length && result.goals?.length) {
        setPriorityOrder(result.goals.map((g: any) => g.id));
      }
    } catch (e: any) {
      console.error("goal-tradeoff error", e);
    } finally {
      setLoading(false);
      setRefreshing(false);
    }
  }

  // Debounced re-fetch whenever priorities or overrides change
  const triggerRefresh = useCallback(
    (order: string[], ov: Record<string, any>) => {
      if (debounceRef.current) clearTimeout(debounceRef.current);
      debounceRef.current = setTimeout(() => {
        fetchPlan(hid, order, ov, true);
      }, 600);
    },
    // eslint-disable-next-line react-hooks/exhaustive-deps
    [hid],
  );

  function moveGoal(id: string, dir: -1 | 1) {
    const next = [...priorityOrder];
    const idx = next.indexOf(id);
    const swap = idx + dir;
    if (swap < 0 || swap >= next.length) return;
    [next[idx], next[swap]] = [next[swap], next[idx]];
    setPriorityOrder(next);
    triggerRefresh(next, overrides);
  }

  function patchOverride(id: string, patch: any) {
    const next = { ...overrides, [id]: { ...(overrides[id] || {}), ...patch } };
    setOverrides(next);
    triggerRefresh(priorityOrder, next);
  }

  function resetAll() {
    setOverrides({});
    setPriorityOrder(plan?.goals?.map((g: any) => g.id) || []);
    fetchPlan(hid, [], {}, false);
  }

  if (loading || !plan) return <Spinner label="Running goal analysis…" />;

  const goalsInOrder = priorityOrder.length
    ? priorityOrder.map((id) => plan.goals.find((g: any) => g.id === id)).filter(Boolean)
    : plan.goals;

  const allOnTrack = plan.goals.every((g: any) => g.on_track);
  const hasOverrides = Object.values(overrides).some((v) => Object.keys(v || {}).length > 0)
    || (priorityOrder.length > 0 && JSON.stringify(priorityOrder) !== JSON.stringify(plan.goals.map((g: any) => g.id)));

  return (
    <div className="max-w-4xl mx-auto">
      <PageHeader
        title="Will I reach my goals?"
        sub="Your goals compete for the same capital. Change priorities and timelines to find the plan that works."
        actions={
          !isClient && households.length > 0 ? (
            <div className="flex items-center gap-2">
              {refreshing && <RefreshCw size={14} className="animate-spin text-ink-muted" />}
              <select className="input text-sm" value={hid || ""} onChange={(e) => setHid(e.target.value)}>
                {households.map((h) => <option key={h.id} value={h.id}>{h.name}</option>)}
              </select>
            </div>
          ) : refreshing ? <RefreshCw size={14} className="animate-spin text-ink-muted" /> : undefined
        }
      />

      {/* Insight banner */}
      <div className={`rounded-2xl p-5 mb-6 ${allOnTrack ? "bg-positive/8 border border-positive/20" : "bg-caution/8 border border-caution/20"}`}>
        <div className="flex items-start gap-3">
          {allOnTrack
            ? <CheckCircle2 size={20} className="text-positive shrink-0 mt-0.5" />
            : <AlertTriangle size={20} className="text-caution shrink-0 mt-0.5" />}
          <p className="text-sm text-ink leading-relaxed">{plan.insight}</p>
        </div>
      </div>

      {/* Summary tiles */}
      <div className="grid grid-cols-2 md:grid-cols-4 gap-3 mb-6">
        <div className="card p-4">
          <div className="tile-label">Invested capital</div>
          <div className="text-xl font-bold text-ink mt-1">{money(plan.shared_capital)}</div>
          <div className="text-xs text-ink-muted mt-1">Portfolio today</div>
        </div>
        <div className="card p-4">
          <div className="tile-label">Human capital</div>
          <div className="text-xl font-bold text-ink mt-1">{money(plan.human_capital)}</div>
          <div className="text-xs text-ink-muted mt-1">Career earnings (PV)</div>
        </div>
        <div className="card p-4">
          <div className="tile-label">Total effective capital</div>
          <div className="text-xl font-bold text-ink mt-1">{money(plan.total_effective_capital)}</div>
          <div className="text-xs text-ink-muted mt-1">Portfolio + career</div>
        </div>
        <div className="card p-4">
          <div className="tile-label">Goals on track</div>
          <div className="text-xl font-bold text-ink mt-1">
            {plan.goals.filter((g: any) => g.on_track).length}
            <span className="text-base font-normal text-ink-muted"> / {plan.goals.length}</span>
          </div>
          <div className="text-xs text-ink-muted mt-1">At ≥70% probability</div>
        </div>
      </div>

      <div className="grid md:grid-cols-5 gap-5">
        {/* Goals list */}
        <div className="md:col-span-3 space-y-3">
          <div className="flex items-center justify-between mb-1">
            <div className="text-xs font-semibold text-ink-muted uppercase tracking-wide flex items-center gap-1.5">
              <Info size={12} /> Priority order — highest priority draws capital first
            </div>
            {hasOverrides && (
              <button onClick={resetAll} className="text-xs text-navy-600 hover:text-ink underline">
                Reset all
              </button>
            )}
          </div>

          {goalsInOrder.map((goal: any, i: number) => (
            <GoalCard
              key={goal.id}
              goal={goal}
              rank={i + 1}
              total={goalsInOrder.length}
              onMoveUp={() => moveGoal(goal.id, -1)}
              onMoveDown={() => moveGoal(goal.id, 1)}
              onOverride={(patch) => patchOverride(goal.id, patch)}
              overrides={overrides[goal.id] || {}}
            />
          ))}

          {plan.goals.length === 0 && (
            <div className="card p-8 text-center text-ink-muted">
              <Target size={32} className="mx-auto mb-3 text-navy-200" />
              <p className="text-sm">No goals set up yet.</p>
              <p className="text-xs mt-1">Ask your adviser to add goals to see the trade-off analysis.</p>
            </div>
          )}
        </div>

        {/* Right panel: human capital + trade-off matrix */}
        <div className="md:col-span-2 space-y-4">
          {/* Human capital card */}
          <Card>
            <div className="font-semibold text-ink mb-1 flex items-center gap-2">
              <Briefcase size={16} className="text-navy-400" /> Your career as an asset
            </div>
            <p className="text-xs text-ink-muted mb-3">
              Your remaining career earnings are worth approximately {money(plan.human_capital)} in today's money.
              This is your biggest asset — it reduces how much investment risk you need to take.
            </p>
            <div className="space-y-2">
              <div className="flex justify-between text-xs">
                <span className="text-ink-muted">Current age</span>
                <span className="font-medium text-ink">{plan.current_age}</span>
              </div>
              <div className="flex justify-between text-xs">
                <span className="text-ink-muted">Retirement age</span>
                <span className="font-medium text-ink">{plan.retirement_age}</span>
              </div>
              <div className="flex justify-between text-xs">
                <span className="text-ink-muted">Annual income est.</span>
                <span className="font-medium text-ink">{money(plan.annual_income)}</span>
              </div>
              <div className="flex justify-between text-xs">
                <span className="text-ink-muted">Working years left</span>
                <span className="font-medium text-ink">{Math.max(0, plan.retirement_age - plan.current_age)}</span>
              </div>
            </div>
            {/* HC as share of total effective capital */}
            {plan.total_effective_capital > 0 && (
              <div className="mt-3 pt-3 border-t border-navy-100">
                <div className="flex justify-between text-xs text-ink-muted mb-1">
                  <span>Career earnings</span>
                  <span>{pct(plan.human_capital / plan.total_effective_capital, 0)}</span>
                </div>
                <div className="h-2 rounded-full bg-navy-100 overflow-hidden">
                  <div
                    className="h-full rounded-full bg-gold-600"
                    style={{ width: `${Math.round((plan.human_capital / plan.total_effective_capital) * 100)}%` }}
                  />
                </div>
                <div className="flex justify-between text-xs text-ink-muted mt-1">
                  <span>Portfolio</span>
                  <span>{pct(plan.shared_capital / plan.total_effective_capital, 0)}</span>
                </div>
              </div>
            )}
          </Card>

          {/* MC assumptions */}
          <Card>
            <div className="font-semibold text-ink mb-2 text-sm">Projection assumptions</div>
            <div className="space-y-1.5 text-xs">
              <div className="flex justify-between">
                <span className="text-ink-muted">Expected return</span>
                <span className="font-medium text-ink">{pct(plan.assumptions?.expected_return, 1)}</span>
              </div>
              <div className="flex justify-between">
                <span className="text-ink-muted">Volatility</span>
                <span className="font-medium text-ink">{pct(plan.assumptions?.volatility, 1)}</span>
              </div>
              <div className="flex justify-between">
                <span className="text-ink-muted">Simulations</span>
                <span className="font-medium text-ink">{(plan.assumptions?.sims || 0).toLocaleString()}</span>
              </div>
              <div className="flex justify-between">
                <span className="text-ink-muted">"On track" threshold</span>
                <span className="font-medium text-ink">≥70% probability</span>
              </div>
            </div>
            <p className="text-[11px] text-ink-muted mt-3">
              Capital-market assumptions based on long-run blended return for your allocation.
              Priority 1 goals draw first; residual capital grows forward for lower-priority goals.
            </p>
          </Card>
        </div>
      </div>

      {/* Trade-off matrix */}
      {plan.tradeoff_matrix?.length > 0 && (
        <div className="mt-5">
          <TradeoffMatrix matrix={plan.tradeoff_matrix} goals={plan.goals} />
        </div>
      )}
    </div>
  );
}
