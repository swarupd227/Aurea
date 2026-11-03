"use client";
import { useEffect, useRef, useState } from "react";
import { CheckCircle2, Clock, PiggyBank, RotateCcw, ShieldAlert, Sparkles, TrendingUp, Wallet } from "lucide-react";
import { Card, ErrorState, SkeletonList } from "./ui";
import { RetirementFan } from "./Charts";
import { api } from "@/lib/api";
import { money, pct } from "@/lib/format";

type Plan = any;
type Levers = { retirement_age: number; longevity_age: number; annual_income: number };

/** What the retirement engine stood in for, by the key it names in `assumed` (aurea_core/retirement.py). */
export const RETIREMENT_ASSUMED: Record<string, (p: any) => string> = {
  current_age: () => "assumed age 54",
  retirement_age: (p) => `assumed retirement at ${p.retirement_age}`,
  longevity_age: (p) => `assumed to age ${p.longevity_age}`,
  funding_share: (p) => `assumed ${Math.round((p.funding_share ?? 0) * 100)}% set aside`,
};

const INCOME_BASIS: Record<string, [string, string]> = {
  stated: ["income stated on the goal", "text-ink-muted"],
  recorded_spending: ["income from spending on file", "text-ink-muted"],
  assumed_draw: ["income assumed · 5% draw", "text-warn"],
};

/** The retirement projection (aurea_core/retirement.py) as figures, with what-if levers. The levers
 * are reads: each one re-asks the projection with query parameters and nothing is saved. Every
 * figure here is a Monte Carlo estimate and is labelled as one. */
export default function RetirementPlanner({
  basePath,
  client = false,
  query,
  onLoad,
}: {
  basePath: string; // e.g. /api/core/households/<id>/retirement  or  /api/canvas/retirement
  client?: boolean;
  query?: Record<string, string | number>; // extra params merged into every request (e.g. household_id)
  /** Called with the first projection loaded, e.g. so a view can name the household it is about. */
  onLoad?: (plan: Plan) => void;
}) {
  const [plan, setPlan] = useState<Plan | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [ctrl, setCtrl] = useState<Levers | null>(null);
  const [base, setBase] = useState<Levers | null>(null);
  const inited = useRef(false);
  const queryKey = JSON.stringify(query || {});

  async function load(params?: Record<string, number>) {
    const merged: Record<string, string> = {};
    for (const [k, v] of Object.entries({ ...(query || {}), ...(params || {}) })) merged[k] = String(v);
    const qs = Object.keys(merged).length ? "?" + new URLSearchParams(merged).toString() : "";
    const p = await api(`${basePath}${qs}`);
    setPlan(p);
    return p;
  }

  function first() {
    setLoading(true);
    setError(null);
    inited.current = false;
    load()
      .then((p) => {
        // The income lever is the need: the projection takes guaranteed income off it itself.
        const start = { retirement_age: p.retirement_age, longevity_age: p.longevity_age, annual_income: p.income_need ?? p.income_target };
        setCtrl(start);
        setBase(start);
        inited.current = true;
        onLoad?.(p);
      })
      .catch((e: any) => setError(e?.message || "Failed to load"))
      .finally(() => setLoading(false));
  }

  useEffect(() => {
    first();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [basePath, queryKey]);

  // Re-run when a lever moves (debounced). A read: nothing on file changes.
  useEffect(() => {
    if (!inited.current || !ctrl) return;
    setBusy(true);
    const t = setTimeout(() => {
      load(ctrl).catch((e: any) => setError(e?.message || "Failed to load")).finally(() => setBusy(false));
    }, 350);
    return () => clearTimeout(t);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [ctrl?.retirement_age, ctrl?.longevity_age, ctrl?.annual_income]);

  if (error) return <div className="view-container"><Card><ErrorState what="the retirement projection" message={error} onRetry={first} /></Card></div>;
  if (loading || !plan || !ctrl || !base) return <div className="view-container"><SkeletonList count={4} /></div>;

  const ok = !!plan.on_track;
  const successPct = Math.round((plan.success_probability ?? 0) * 100);
  const target = plan.target_success ?? plan.assumptions?.target_success;
  const targetPct = target != null ? Math.round(target * 100) : null;
  const seq = plan.sequence_risk || {};
  const basis = INCOME_BASIS[plan.income_basis];
  const whatIf = ctrl.retirement_age !== base.retirement_age || ctrl.longevity_age !== base.longevity_age || ctrl.annual_income !== base.annual_income;
  const incomeMin = Math.max(5000, Math.round((base.annual_income * 0.5) / 5000) * 5000);
  const incomeMax = Math.max(incomeMin + 5000, Math.round((base.annual_income * 1.6) / 5000) * 5000);
  const tone = ok ? "text-ok" : "text-warn";

  return (
    <div className="view-container space-y-4" data-testid="retirement-planner">
      {/* Headline: likelihood against the target */}
      <Card data-testid="retirement-headline">
        <div className="flex items-start gap-4">
          <span className={`hidden h-11 w-11 shrink-0 items-center justify-center rounded-xl bg-border-soft cq-sm:flex ${tone}`}>
            <PiggyBank size={22} aria-hidden="true" />
          </span>
          <div className="min-w-0 flex-1">
            <div className="flex flex-wrap items-center gap-1.5">
              <span className={`chip ${ok ? "bg-ok-bg text-ok" : "bg-warn-bg text-warn"}`} data-testid="retirement-status">
                {ok ? "on track" : "below target"}
              </span>
              <span className="chip bg-border-soft text-ink-soft" data-testid="retirement-estimate">estimate</span>
              <span className="chip bg-border-soft text-ink-soft" data-testid="retirement-measures">whether the income lasts</span>
              {whatIf && <span className="chip bg-border-soft text-ink-soft" data-testid="retirement-what-if">what-if · nothing saved</span>}
              {busy && <span className="chip bg-border-soft text-ink-muted">updating</span>}
            </div>
            <div className="mt-2 flex flex-wrap gap-1.5 text-xs">
              <span className="chip bg-border-soft text-ink-soft tabular-nums">{money(plan.income_target)}/yr from the portfolio · today&rsquo;s money</span>
              <span className="chip bg-border-soft text-ink-soft tabular-nums">age {plan.retirement_age}–{plan.longevity_age}</span>
              {plan.median_depletion_age && (
                <span className="chip bg-warn-bg text-warn tabular-nums">median depletion · age {plan.median_depletion_age}</span>
              )}
              {basis && <span className={`chip bg-border-soft ${basis[1]}`} data-testid="retirement-income-basis">{basis[0]}</span>}
              {plan.guaranteed_income > 0 && (
                <span className="chip bg-border-soft text-ink-soft tabular-nums">guaranteed income {money(plan.guaranteed_income)}/yr</span>
              )}
              {plan.funding_share != null && !(plan.assumed || []).includes("funding_share") && (
                <span className="chip bg-border-soft text-ink-muted tabular-nums">funding share {pct(plan.funding_share, 0)}</span>
              )}
              {(plan.assumed || []).filter((k: string) => RETIREMENT_ASSUMED[k]).map((k: string) => (
                <span key={k} className="chip bg-warn-bg text-warn tabular-nums" data-testid="retirement-assumed">{RETIREMENT_ASSUMED[k](plan)}</span>
              ))}
            </div>
            {/* likelihood bar with the target marker */}
            <div className="relative mt-3 h-2.5 overflow-hidden rounded-full bg-border-soft">
              <div className={`h-full ${ok ? "bg-ok" : "bg-warn"}`} style={{ width: `${successPct}%` }} />
              {targetPct != null && <div className="absolute inset-y-0 w-px bg-ink-muted" style={{ left: `${targetPct}%` }} />}
            </div>
            {targetPct != null && (
              <div className="relative mt-1 h-3">
                <span className="absolute text-[10px] text-ink-muted" style={{ left: `calc(${targetPct}% - 18px)` }}>target {targetPct}%</span>
              </div>
            )}
          </div>
          <div className="shrink-0 text-right">
            <div className={`font-mono text-3xl font-semibold tabular-nums ${tone}`} data-testid="retirement-likelihood">{successPct}%</div>
            <div className="text-[11px] text-ink-muted">income lasts · estimate</div>
          </div>
        </div>
      </Card>

      {/* Key figures */}
      <div className="grid grid-cols-2 gap-3 cq-md:grid-cols-4" data-testid="retirement-figures">
        <Stat icon={Clock} label="Years to retirement" value={`${plan.years_to_retirement}`} sub={`retire at ${plan.retirement_age}`} />
        <Stat icon={TrendingUp} label="At retirement" value={money(plan.projected_at_retirement)} sub="median · estimate" />
        <Stat icon={Wallet} label="Sustainable income" value={money(plan.sustainable_income)}
          sub={plan.income_gap > 0 ? `${money(plan.income_gap)} below target` : "covers the target"}
          tone={plan.income_gap > 0 ? "text-warn" : "text-ok"} />
        <Stat icon={PiggyBank} label="Income at retirement" value={money(plan.income_at_retirement)} sub="inflation-adjusted" />
      </div>

      <div className="grid gap-4 cq-md:grid-cols-3">
        {/* Fan chart */}
        <Card className="p-4 cq-md:col-span-2">
          <div className="mb-1 flex flex-wrap items-center justify-between gap-2">
            <div className="tile-label">Savings through retirement</div>
            <span className="chip bg-border-soft text-ink-soft">estimate</span>
          </div>
          <div className="mb-2 flex flex-wrap gap-1.5">
            <span className="chip bg-border-soft text-ink-muted">median</span>
            <span className="chip bg-border-soft text-ink-muted">10th–90th percentile</span>
          </div>
          <RetirementFan data={plan.balance_by_age} retirementAge={plan.retirement_age} />
        </Card>

        {/* Levers the engine evaluated */}
        <Card className="p-4">
          <div className="tile-label mb-2 flex items-center gap-1.5"><Sparkles size={13} aria-hidden="true" /> {client ? "What would help" : "Levers"}</div>
          <div className="space-y-2" data-testid="retirement-levers">
            {(plan.levers || []).map((l: any) => {
              const clears = target != null && l.success >= target;
              return (
                <div key={l.key} className={`flex items-center justify-between gap-2 rounded-lg border p-2.5 ${clears ? "border-ok bg-ok-bg" : "border-border"}`}>
                  <span className="text-sm text-ink-soft">{l.label}</span>
                  <span className={`flex items-center gap-1 font-mono text-sm font-semibold tabular-nums ${clears ? "text-ok" : "text-ink"}`}>
                    {clears && <CheckCircle2 size={13} aria-hidden="true" />}{Math.round(l.success * 100)}%
                  </span>
                </div>
              );
            })}
          </div>
        </Card>
      </div>

      {/* Sequence-of-returns risk */}
      <Card className="p-4" data-testid="retirement-sequence">
        <div className="mb-2 flex flex-wrap items-center gap-1.5">
          <span className="tile-label flex items-center gap-1.5"><ShieldAlert size={13} aria-hidden="true" /> Sequence-of-returns risk</span>
          <span className="chip bg-border-soft text-ink-muted tabular-nums">year-1 shock {Math.round((seq.crash_return || 0) * 100)}%</span>
          <span className="chip bg-border-soft text-ink-soft">estimate</span>
        </div>
        <div className="flex flex-wrap items-center gap-4">
          <SeqBar label="Normal markets" pct={Math.round((seq.baseline || 0) * 100)} tone="bg-ok" />
          <SeqBar label="Crash in year 1" pct={Math.round((seq.early_crash || 0) * 100)} tone="bg-crit" />
          <span className="chip bg-crit-bg text-crit tabular-nums">−{Math.round((seq.delta || 0) * 100)} pts</span>
        </div>
      </Card>

      {/* What-if levers: reads only */}
      <Card className="p-4" data-testid="retirement-controls">
        <div className="mb-3 flex flex-wrap items-center justify-between gap-2">
          <div className="flex flex-wrap items-center gap-1.5">
            <span className="tile-label">{client ? "Try a change" : "What if"}</span>
            <span className="chip bg-border-soft text-ink-muted">nothing saved</span>
          </div>
          {whatIf && (
            <button className="btn-ghost px-2 py-1 text-xs" onClick={() => setCtrl(base)} data-testid="retirement-reset">
              <RotateCcw size={12} aria-hidden="true" /> Reset
            </button>
          )}
        </div>
        <div className="grid gap-5 cq-sm:grid-cols-3">
          <Slider label="Retirement age" min={Math.max(plan.current_age + 1, 55)} max={75} value={ctrl.retirement_age}
            onChange={(v: number) => setCtrl({ ...ctrl, retirement_age: v })} fmt={(v: number) => `${v}`} />
          <Slider label="Plan to age" min={85} max={100} value={ctrl.longevity_age}
            onChange={(v: number) => setCtrl({ ...ctrl, longevity_age: v })} fmt={(v: number) => `${v}`} />
          <Slider label="Income need · today's money" min={incomeMin} max={incomeMax} step={5000} value={ctrl.annual_income}
            onChange={(v: number) => setCtrl({ ...ctrl, annual_income: v })} fmt={(v: number) => money(v)} />
        </div>
      </Card>
    </div>
  );
}

function Stat({ icon: Icon, label, value, sub, tone = "text-ink" }: any) {
  return (
    <Card className="p-3">
      <div className="mb-1 flex items-center gap-1.5 text-xs text-ink-muted"><Icon size={13} aria-hidden="true" /> {label}</div>
      <div className={`font-mono text-lg font-semibold tabular-nums ${tone}`}>{value}</div>
      <div className="text-[11px] text-ink-muted">{sub}</div>
    </Card>
  );
}

function SeqBar({ label, pct, tone }: { label: string; pct: number; tone: string }) {
  return (
    <div className="w-32">
      <div className="mb-1 text-xs text-ink-muted">{label}</div>
      <div className="relative h-6 overflow-hidden rounded bg-border-soft">
        <div className={`h-full ${tone}`} style={{ width: `${pct}%` }} />
        <span className="absolute inset-0 flex items-center justify-center font-mono text-xs font-semibold text-ink">{pct}%</span>
      </div>
    </div>
  );
}

function Slider({ label, min, max, step = 1, value, onChange, fmt }: any) {
  return (
    <div>
      <div className="mb-1 flex justify-between gap-2 text-sm">
        <span className="text-ink-soft">{label}</span>
        <span className="font-mono font-semibold tabular-nums text-ink">{fmt(value)}</span>
      </div>
      <input type="range" min={min} max={max} step={step} value={value} aria-label={label}
        onChange={(e) => onChange(Number(e.target.value))} className="w-full accent-[color:var(--color-ink-soft)]" />
    </div>
  );
}
