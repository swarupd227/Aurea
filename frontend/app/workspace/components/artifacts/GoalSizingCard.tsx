"use client";
/** Goals sized and funded (aurea_core/goal_sizing.py, B-394): the monthly saving each goal needs, level and stepped up,
 * beside what is saved on it, its likelihood and funded ratio; the same first amount compared year by year; and the
 * vehicle categories for its horizon. Figures, tables, chips and one chart. */
import { useState } from "react";
import { CartesianGrid, Line, LineChart, ReferenceLine, ResponsiveContainer, Tooltip, XAxis, YAxis } from "recharts";
import { TierChip } from "@/components/GoalTiers";
import { money, moneyShort } from "@/lib/format";
import { returnsLabel } from "@/lib/planBasis";

const pct = (v: number | null | undefined, d = 0) => (v == null ? "—" : `${(v * 100).toFixed(d)}%`);
const words = (v: string) => v.replace(/_/g, " ");
const SAVING_BASIS: Record<string, string> = { sip_registration: "SIP registration", recorded: "recorded", not_on_file: "not on file" };
const WHY: Record<string, string> = { no_funding_share: "no share recorded", no_need: "no target", no_holdings: "no managed holdings", no_target: "no target" };

function Head({ children, right = false }: { children?: React.ReactNode; right?: boolean }) {
  return <th className={`px-3 py-1.5 font-mono text-[11px] font-normal uppercase tracking-wider text-ink-muted ${right ? "text-right" : "text-left"}`}>{children}</th>;
}
const num = "px-3 py-1.5 text-right align-top font-mono text-xs tabular-nums text-ink";

/** Each goal: target, what is saved on it, what it needs level and stepped up, likelihood and funded ratio. */
export function GoalSizingTable({ result, selected, onSelect }: { result: any; selected?: string; onSelect?: (id: string) => void }) {
  const ccy: string | undefined = result.currency || undefined;
  const step = result.step_up_percent;
  return (
    <div className="overflow-x-auto">
      <table className="w-full text-sm" data-testid="goal-sizing-table">
        <thead>
          <tr>
            <Head>Goal</Head><Head right>Target at date</Head><Head right>Saved a month</Head><Head right>Level a month</Head>
            {step != null && <Head right>Step-up {step}% · first</Head>}
            <Head right>Likelihood</Head><Head right>Funded</Head>
          </tr>
        </thead>
        <tbody className="divide-y divide-border-soft">
          {result.goals.map((g: any) => {
            const z = g.sizing;
            const f = g.funded ?? {};
            return (
              <tr key={g.goal_id} data-testid="goal-sizing-row" className={selected === g.goal_id ? "bg-border-soft" : ""}>
                <td className="px-3 py-1.5 align-top">
                  {onSelect
                    ? <button type="button" className="text-left text-ink hover:underline" aria-pressed={selected === g.goal_id} onClick={() => onSelect(g.goal_id)} data-testid="goal-sizing-select">{g.name}</button>
                    : <div className="text-ink">{g.name}</div>}
                  <div className="mt-0.5 flex flex-wrap gap-1">
                    <TierChip tier={g.tier} />
                    <span className="chip tabular-nums text-ink-muted">{g.years}y{g.horizon === "assumed" ? " assumed" : ""}</span>
                    {g.retirement_age != null && <span className="chip tabular-nums text-ink-muted">at {g.retirement_age}</span>}
                  </div>
                </td>
                <td className={num}>
                  {money(g.target, ccy)}
                  <div className="text-[11px] text-ink-muted">
                    {g.target_basis === "today" ? `${money(g.target_today, ccy)} today · ${words(g.index ?? "cpi")} ${pct(g.inflation, 1)}` : "as stated"}
                  </div>
                </td>
                <td className={num}>
                  {g.saving_monthly != null ? money(g.saving_monthly, ccy) : "—"}
                  <div className="text-[11px] text-ink-muted">{SAVING_BASIS[g.saving_basis] ?? words(g.saving_basis ?? "")}{g.registered_step_up ? " · step-up" : ""}</div>
                </td>
                <td className={num}>
                  {z.status === "assessed" ? money(z.level_monthly, ccy) : <span className="chip text-ink-muted">{WHY[z.why] ?? "not assessed"}</span>}
                  {g.short_by_monthly > 0 && <div className="text-[11px] text-warn">short {money(g.short_by_monthly, ccy)}</div>}
                </td>
                {step != null && (
                  <td className={num}>
                    {z.step_up ? money(z.step_up.first_monthly, ccy) : "—"}
                    {z.step_up && <div className="text-[11px] text-ink-muted">last {money(z.step_up.last_monthly, ccy)}</div>}
                  </td>
                )}
                <td className={`${num} ${g.on_track ? "text-ok" : "text-warn"}`}>{pct(g.likelihood)}</td>
                <td className={num}>
                  {f.ratio != null ? f.ratio.toFixed(2) : <span className="chip text-ink-muted">{WHY[f.why] ?? "not assessed"}</span>}
                  {f.ratio != null && <div className="text-[11px] text-ink-muted">{moneyShort(f.assets, ccy)} of {moneyShort(f.pv_need, ccy)}{f.need_basis === "retirement_spending" ? " retirement spending" : " today"}</div>}
                  {f.ratio_to_target != null && <div className="text-[11px] text-ink-muted" data-testid="goal-sizing-ratio-to-target">{f.ratio_to_target.toFixed(2)} of this target</div>}
                </td>
              </tr>
            );
          })}
        </tbody>
      </table>
    </div>
  );
}

function Key({ slot, label, value, dashed = false }: { slot?: 1 | 2; label: string; value: string; dashed?: boolean }) {
  return (
    <span className="inline-flex items-center gap-1.5 text-xs text-ink-soft">
      <span aria-hidden className="inline-block w-4" style={{ borderTop: `2px ${dashed ? "dashed" : "solid"} ${slot ? `var(--series-${slot})` : "var(--color-ink-muted)"}` }} />
      {label}<span className="font-mono tabular-nums text-ink">{value}</span>
    </span>
  );
}

/** The same first monthly amount, level against stepped up, beside the target. */
export function StepUpChart({ goal, currency }: { goal: any; currency?: string }) {
  const [table, setTable] = useState(false);
  const c = goal.comparison;
  if (!c) return null;
  const stepped = `Stepped up ${c.percent}% a year`;
  const top = Math.max(c.target, c.stepped_end, c.level_end);
  return (
    <div className="goal-chart px-3 py-2.5" data-testid="goal-sizing-chart">
      <div className="mb-1.5 flex flex-wrap items-center gap-1.5">
        <div className="mr-1 text-sm font-medium text-ink">{goal.name}</div>
        <span className="chip tabular-nums text-ink-muted">both start at {money(c.first_monthly, currency)} a month</span>
        <span className="chip text-ink-muted">{c.first_monthly_basis === "saved_on_the_goal" ? "saved on it today" : "stepped amount that reaches the target"}</span>
        <button type="button" className="chip text-ink-soft" aria-pressed={table} onClick={() => setTable((v) => !v)} data-testid="goal-sizing-chart-table-toggle">{table ? "chart" : "table"}</button>
      </div>
      <div className="mb-1 flex flex-wrap gap-x-4 gap-y-1">
        <Key slot={1} label="Level" value={moneyShort(c.level_end, currency)} />
        <Key slot={2} label={stepped} value={moneyShort(c.stepped_end, currency)} />
        <Key dashed label="Target" value={moneyShort(c.target, currency)} />
      </div>
      {table ? (
        <div className="max-h-56 overflow-auto">
          <table className="w-full text-sm">
            <thead><tr><Head>Year</Head><Head right>Level</Head><Head right>{stepped}</Head></tr></thead>
            <tbody className="divide-y divide-border-soft">
              {c.points.map((p: any) => (
                <tr key={p.year}><td className="px-3 py-1 font-mono text-xs text-ink-muted">{p.year}</td><td className={num}>{money(p.level, currency)}</td><td className={num}>{money(p.stepped, currency)}</td></tr>
              ))}
            </tbody>
          </table>
        </div>
      ) : (
        <ResponsiveContainer width="100%" height={200}>
          <LineChart data={c.points} margin={{ left: 4, right: 8, top: 8, bottom: 0 }}>
            <CartesianGrid vertical={false} stroke="var(--color-border-soft)" />
            <XAxis dataKey="year" fontSize={11} stroke="var(--color-ink-faint)" tickLine={false} minTickGap={16} tickFormatter={(y) => `${y}y`} />
            <YAxis fontSize={11} stroke="var(--color-ink-faint)" tickLine={false} axisLine={false} width={64} domain={[0, Math.ceil(top * 1.05)]} tickFormatter={(v) => moneyShort(v, currency)} />
            <Tooltip
              contentStyle={{ background: "var(--color-surface)", border: "1px solid var(--color-border)", borderRadius: 8, fontSize: 12 }}
              itemStyle={{ color: "var(--color-ink)" }} labelStyle={{ color: "var(--color-ink-muted)" }}
              formatter={(v: any, n: any) => [money(Number(v), currency), n]} labelFormatter={(y) => `Year ${y}`} />
            <ReferenceLine y={c.target} stroke="var(--color-ink-muted)" strokeDasharray="4 4" />
            <Line dataKey="level" name="Level" stroke="var(--series-1)" strokeWidth={2} dot={false} activeDot={{ r: 4 }} isAnimationActive={false} />
            <Line dataKey="stepped" name={stepped} stroke="var(--series-2)" strokeWidth={2} dot={false} activeDot={{ r: 4 }} isAnimationActive={false} />
          </LineChart>
        </ResponsiveContainer>
      )}
    </div>
  );
}

/** The vehicle categories for a goal's horizon, each with its reason: categories, never a product. */
export function VehiclesTable({ goal }: { goal: any }) {
  const v = goal.vehicles;
  if (!v) return null;
  return (
    <div data-testid="goal-vehicles">
      <div className="flex flex-wrap items-center gap-1.5 px-3 pt-2.5">
        <div className="mr-1 text-sm font-medium text-ink">{goal.name}</div>
        <span className="chip text-ink-muted">{v.label}</span>
        <span className="chip text-ink-muted">{v.note}</span>
        <span className="chip text-ink-muted" title={v.source}>convention · not reviewed</span>
        {(v.law ?? []).map((l: any) => <span key={l.key} className="chip text-warn" title={`${l.label} · ${l.source}`}>figure not reviewed</span>)}
      </div>
      <div className="overflow-x-auto">
        <table className="w-full text-sm">
          <thead><tr><Head>Category</Head><Head>Reason</Head><Head>Condition</Head></tr></thead>
          <tbody className="divide-y divide-border-soft">
            {v.rows.map((r: any) => (
              <tr key={r.category}>
                <td className="px-3 py-1.5 text-ink">{r.category}</td>
                <td className="px-3 py-1.5 text-xs text-ink-muted">{r.reason}</td>
                <td className="px-3 py-1.5 text-xs text-ink-muted">{r.condition ?? "—"}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </div>
  );
}

export function GoalSizingChips({ result }: { result: any }) {
  const f = result.household_funded ?? {};
  return (
    <>
      <span className="chip text-ink-muted">estimates</span>
      <span className="chip text-ink-muted">{returnsLabel(result.assumptions_source)}{result.assumptions_version != null ? ` v${result.assumptions_version}` : ""}</span>
      {result.expected && (
        <span className="chip tabular-nums text-ink-muted" title={Object.entries(result.expected.mix).map(([k, v]: any) => `${words(k)} ${pct(v)}`).join(", ")}>
          current mix {pct(result.expected.compound, 1)} a year compound
        </span>
      )}
      <span className="chip text-ink-muted" title={result.basis}>about an even chance</span>
      {result.step_up_percent == null && <span className="chip text-ink-muted">step-up not stated</span>}
      <span className="chip tabular-nums text-ink" data-testid="goal-sizing-household-funded">household funded {f.ratio != null ? f.ratio.toFixed(2) : "not assessed"}</span>
    </>
  );
}

/** The table with the chart and the categories of whichever goal is chosen. */
export function GoalSizingBody({ result }: { result: any }) {
  const first = result.goals.find((g: any) => g.comparison)?.goal_id ?? result.goals[0]?.goal_id;
  const [chosen, setChosen] = useState<string | undefined>(undefined);
  const goal = result.goals.find((g: any) => g.goal_id === (chosen ?? first)) ?? result.goals[0];
  if (!result.goals.length) return <div className="px-3 py-2.5"><span className="chip text-ink-muted">no goal on file</span></div>;
  return (
    <>
      <GoalSizingTable result={result} selected={goal?.goal_id} onSelect={result.goals.length > 1 ? setChosen : undefined} />
      {goal?.comparison && <div className="border-t border-border-soft"><StepUpChart goal={goal} currency={result.currency || undefined} /></div>}
      {goal?.vehicles && <div className="border-t border-border-soft"><VehiclesTable goal={goal} /></div>}
      <div className="flex flex-wrap gap-1.5 border-t border-border-soft px-3 py-2" data-testid="goal-sizing-not-counted">
        <span className="chip text-ink-muted">goal ratios count no {(result.not_counted_by_goal ?? []).map(words).join(", ")}</span>
        {result.shares_scaled && <span className="chip text-warn">shares past the whole · scaled</span>}
      </div>
    </>
  );
}

export function GoalSizingCard({ result }: { result: any }) {
  return (
    <div className="overflow-hidden rounded-lg border border-border bg-surface" data-testid="goal-sizing-card">
      <div className="flex flex-wrap items-center gap-1.5 border-b border-border-soft px-3 py-2">
        <div className="mr-1 text-sm font-medium text-ink">Goals · {result.household}</div>
        {result.recorded && <span className="chip text-ok">recorded</span>}
        <GoalSizingChips result={result} />
      </div>
      <GoalSizingBody result={result} />
    </div>
  );
}
