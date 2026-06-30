"use client";
import { useEffect, useRef, useState } from "react";
import { PiggyBank, ShieldAlert, TrendingUp, Clock, Wallet, CheckCircle2, AlertTriangle, Sparkles } from "lucide-react";
import { Spinner } from "./ui";
import { RetirementFan } from "./Charts";
import { api } from "@/lib/api";
import { money } from "@/lib/format";

type Plan = any;

export default function RetirementPlanner({
  basePath,
  client = false,
  query,
}: {
  basePath: string; // e.g. /api/core/households/<id>/retirement  or  /api/canvas/retirement
  client?: boolean;
  query?: Record<string, string | number>; // extra params merged into every request (e.g. household_id)
}) {
  const [plan, setPlan] = useState<Plan | null>(null);
  const [loading, setLoading] = useState(true);
  const [busy, setBusy] = useState(false);
  const [ctrl, setCtrl] = useState<{ retirement_age: number; longevity_age: number; annual_income: number } | null>(null);
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

  useEffect(() => {
    setLoading(true);
    inited.current = false;
    load().then((p) => {
      setCtrl({ retirement_age: p.retirement_age, longevity_age: p.longevity_age, annual_income: p.income_target });
      inited.current = true;
    }).finally(() => setLoading(false));
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [basePath, queryKey]);

  // Re-run when the adviser/client adjusts the levers (debounced).
  useEffect(() => {
    if (!inited.current || !ctrl) return;
    setBusy(true);
    const t = setTimeout(() => { load(ctrl).finally(() => setBusy(false)); }, 350);
    return () => clearTimeout(t);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [ctrl?.retirement_age, ctrl?.longevity_age, ctrl?.annual_income]);

  if (loading || !plan || !ctrl) return <Spinner />;

  const ok = plan.on_track;
  const successPct = Math.round(plan.success_probability * 100);
  const targetPct = Math.round((plan.target_success || 0.85) * 100);
  const seq = plan.sequence_risk || {};

  return (
    <div className="space-y-5">
      {/* Headline */}
      <div className={`rounded-2xl p-5 border ${ok ? "bg-positive/5 border-positive/30" : "bg-caution/5 border-caution/30"}`}>
        <div className="flex items-start gap-4">
          <span className={`h-12 w-12 rounded-xl flex items-center justify-center shrink-0 ${ok ? "bg-positive/15 text-positive" : "bg-caution/15 text-caution"}`}>
            <PiggyBank size={24} />
          </span>
          <div className="flex-1 min-w-0">
            <div className="text-lg font-semibold text-ink">
              {client ? (ok ? "You're on track for the retirement you want." : "You're close — a small adjustment gets you there.")
                      : (ok ? "On track" : "Below target — levers available")}
            </div>
            <p className="text-sm text-ink-soft mt-0.5">
              {client ? <>Drawing <b>{money(plan.income_target)}</b>/yr from age {plan.retirement_age}, your savings are projected to support you </>
                      : <>Income of <b>{money(plan.income_target)}</b>/yr (today's $) from {plan.retirement_age} to {plan.longevity_age}: </>}
              <b className={ok ? "text-positive" : "text-caution"}>{successPct}% probability</b>{" "}
              {plan.median_depletion_age
                ? <>of lasting — at this level funds may run low around <b>age {plan.median_depletion_age}</b>.</>
                : <>of lasting to age {plan.longevity_age}.</>}
            </p>
            {/* probability bar with target marker */}
            <div className="mt-3 relative h-2.5 rounded-full bg-navy-100 overflow-hidden">
              <div className={`h-full ${ok ? "bg-positive" : "bg-caution"}`} style={{ width: `${successPct}%` }} />
            </div>
            <div className="relative mt-1 h-3">
              <div className="absolute -top-[18px] w-px h-3 bg-ink/50" style={{ left: `${targetPct}%` }} />
              <span className="absolute text-[10px] text-ink-muted" style={{ left: `calc(${targetPct}% - 18px)` }}>target {targetPct}%</span>
            </div>
          </div>
          <div className="text-right shrink-0">
            <div className={`text-3xl font-bold tabular-nums ${ok ? "text-positive" : "text-caution"}`}>{successPct}%</div>
            <div className="text-[11px] text-ink-muted">{busy ? "updating…" : "income lasts"}</div>
          </div>
        </div>
      </div>

      {/* Key stats */}
      <div className="grid grid-cols-2 lg:grid-cols-4 gap-3">
        <Stat icon={Clock} label="Years to retirement" value={`${plan.years_to_retirement}`} sub={`retire at ${plan.retirement_age}`} />
        <Stat icon={TrendingUp} label="Projected at retirement" value={money(plan.projected_at_retirement)} sub="median outcome" />
        <Stat icon={Wallet} label="Sustainable income" value={money(plan.sustainable_income)}
          sub={plan.income_gap > 0 ? `${money(plan.income_gap)} below target` : "covers your target"}
          accent={plan.income_gap > 0 ? "caution" : "positive"} />
        <Stat icon={PiggyBank} label="Income at retirement" value={money(plan.income_at_retirement)} sub="inflation-adjusted" />
      </div>

      <div className="grid lg:grid-cols-3 gap-5">
        {/* Fan chart */}
        <div className="lg:col-span-2 card p-4">
          <div className="tile-label mb-1">Projected savings through retirement</div>
          <div className="text-xs text-ink-muted mb-2">Median outcome, with the 10th–90th percentile range. The red line is depletion.</div>
          <RetirementFan data={plan.balance_by_age} retirementAge={plan.retirement_age} />
        </div>

        {/* Levers */}
        <div className="card p-4">
          <div className="tile-label mb-2 flex items-center gap-1.5"><Sparkles size={13} /> {client ? "What would help" : "Levers to close the gap"}</div>
          <div className="space-y-2">
            {(plan.levers || []).map((l: any) => {
              const clears = l.success >= (plan.target_success || 0.85);
              return (
                <div key={l.key} className={`rounded-lg border p-2.5 flex items-center justify-between gap-2 ${clears ? "border-positive/40 bg-positive/5" : "border-navy-100"}`}>
                  <span className="text-sm text-ink-soft">{l.label}</span>
                  <span className={`text-sm font-semibold tabular-nums flex items-center gap-1 ${clears ? "text-positive" : "text-ink"}`}>
                    {clears && <CheckCircle2 size={13} />}{Math.round(l.success * 100)}%
                  </span>
                </div>
              );
            })}
          </div>
        </div>
      </div>

      {/* Sequence-of-returns risk */}
      <div className="card p-4">
        <div className="tile-label mb-1 flex items-center gap-1.5"><ShieldAlert size={13} /> Sequence-of-returns risk</div>
        <p className="text-xs text-ink-muted mb-3">
          A market crash in the <b>first year</b> of retirement is far more damaging than the same crash later — you sell into a down market.
          Here's a GFC-style shock ({Math.round((seq.crash_return || 0) * 100)}%) at retirement.
        </p>
        <div className="flex items-center gap-4">
          <SeqBar label="Normal markets" pct={Math.round((seq.baseline || 0) * 100)} tone="positive" />
          <SeqBar label="Crash in year 1" pct={Math.round((seq.early_crash || 0) * 100)} tone="critical" />
          <div className="text-sm text-ink-soft">
            <AlertTriangle size={15} className="inline text-caution mr-1" />
            Success would fall <b className="text-critical">{Math.round((seq.delta || 0) * 100)} pts</b> — the case for a cash buffer / glide-path near retirement.
          </div>
        </div>
      </div>

      {/* Controls */}
      <div className="card p-4">
        <div className="tile-label mb-1">{client ? "Try a change" : "Adjust the retirement plan"}</div>
        <div className="text-xs text-ink-muted mb-3">
          Move a slider to see how retirement age, longevity and yearly income change the projection above.
        </div>
        <div className="grid sm:grid-cols-3 gap-5">
          <Slider label="Retirement age" min={Math.max(plan.current_age + 1, 55)} max={75} value={ctrl.retirement_age}
            onChange={(v) => setCtrl({ ...ctrl, retirement_age: v })} fmt={(v) => `${v}`} />
          <Slider label="Plan to age" min={85} max={100} value={ctrl.longevity_age}
            onChange={(v) => setCtrl({ ...ctrl, longevity_age: v })} fmt={(v) => `${v}`} />
          <Slider label="Annual income (today's $)" min={Math.round(plan.income_target * 0.5 / 5000) * 5000}
            max={Math.round(plan.income_target * 1.6 / 5000) * 5000} step={5000} value={ctrl.annual_income}
            onChange={(v) => setCtrl({ ...ctrl, annual_income: v })} fmt={(v) => money(v)} />
        </div>
      </div>
    </div>
  );
}

function Stat({ icon: Icon, label, value, sub, accent }: any) {
  const tone = accent === "caution" ? "text-caution" : accent === "positive" ? "text-positive" : "text-ink";
  return (
    <div className="card p-3">
      <div className="flex items-center gap-1.5 text-xs text-ink-muted mb-1"><Icon size={13} /> {label}</div>
      <div className={`text-lg font-semibold tabular-nums ${tone}`}>{value}</div>
      <div className="text-[11px] text-ink-muted">{sub}</div>
    </div>
  );
}

function SeqBar({ label, pct, tone }: { label: string; pct: number; tone: string }) {
  const color = tone === "critical" ? "bg-critical" : "bg-positive";
  return (
    <div className="w-32">
      <div className="text-xs text-ink-muted mb-1">{label}</div>
      <div className="h-6 rounded bg-navy-100 overflow-hidden relative">
        <div className={`h-full ${color}`} style={{ width: `${pct}%` }} />
        <span className="absolute inset-0 flex items-center justify-center text-xs font-semibold text-ink">{pct}%</span>
      </div>
    </div>
  );
}

function Slider({ label, min, max, step = 1, value, onChange, fmt }: any) {
  return (
    <div>
      <div className="flex justify-between text-sm mb-1">
        <span className="text-ink-soft">{label}</span>
        <span className="font-semibold text-ink tabular-nums">{fmt(value)}</span>
      </div>
      <input type="range" min={min} max={max} step={step} value={value}
        onChange={(e) => onChange(Number(e.target.value))} className="w-full accent-gold" />
    </div>
  );
}
