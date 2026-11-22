/** A plan scenario (aurea_core/scenarios.py, B-179): the plan as it stands and with the levers, the change in success
 * and funded ratio, and what it gives up in today's money; and a household's saved scenarios, ranked. Tables and chips. */
import { money } from "@/lib/format";

const pct = (v: number | null | undefined) => (v == null ? "—" : `${Math.round(v * 100)}%`);
const pts = (v: number | null | undefined) => (v == null ? "—" : `${v > 0 ? "+" : ""}${v.toFixed(1)} pts`);
const ratio = (v: number | null | undefined) => (v == null ? "—" : v.toFixed(2));
const tone = (v: number | null | undefined) => (v == null || v === 0 ? "text-ink-muted" : v > 0 ? "text-ok" : "text-warn");

function Head({ children, right = false }: { children?: React.ReactNode; right?: boolean }) {
  return <th className={`px-3 py-1.5 font-mono text-[11px] font-normal uppercase tracking-wider text-ink-muted ${right ? "text-right" : "text-left"}`}>{children}</th>;
}

export function leverChips(lv: Record<string, any>, goals?: { id: string; name: string }[]): string[] {
  const goal = goals?.find((g) => g.id === lv.goal_id)?.name ?? "goal";
  const out: string[] = [];
  if (lv.retire_at != null) out.push(`retire at ${lv.retire_at}`);
  if (lv.spending != null) out.push(`spending ${money(lv.spending)}/yr`);
  if (lv.claim_age != null) out.push(`claim at ${lv.claim_age}`);
  if (lv.goal_years_delta != null) out.push(`${goal} ${lv.goal_years_delta > 0 ? "+" : ""}${lv.goal_years_delta}y`);
  if (lv.goal_target != null) out.push(`${goal} target ${money(lv.goal_target)}`);
  if (lv.extra_contribution != null) out.push(`save ${money(lv.extra_contribution)}/yr more`);
  return out;
}

export function ScenarioCard({ result }: { result: any }) {
  const r = result.result ?? result;
  const before = r.before ?? {};
  const after = r.after ?? {};
  const change = r.change ?? {};
  const parts: any[] = r.given_up?.parts ?? [];
  return (
    <div className="overflow-hidden rounded-lg border border-border bg-surface" data-testid="scenario-card">
      <div className="flex flex-wrap items-center gap-2 border-b border-border-soft px-3 py-2">
        <div className="text-sm font-medium text-ink">{result.name ? `Scenario · ${result.name}` : "Scenario"} · {r.household}</div>
        {leverChips(r.levers ?? {}, before.goals).map((c) => <span key={c} className="chip text-ink">{c}</span>)}
        <span className="chip text-ink-muted">estimates</span>
        {r.assumptions_version != null && <span className="chip tabular-nums text-ink-muted">assumptions v{r.assumptions_version}</span>}
        {result.id && <span className="chip text-ok">saved</span>}
      </div>
      <div className="grid grid-cols-2 gap-3 px-3 py-2.5 sm:grid-cols-4">
        {[
          ["Success points", pts(change.points), tone(change.points)],
          ["Funded ratio", change.funded_ratio == null ? "—" : `${ratio(before.funded_ratio)} → ${ratio(after.funded_ratio)}`, tone(change.funded_ratio)],
          ["Given up today", r.given_up ? money(r.given_up.today) : "—", "text-ink"],
          ["Per 10k given up", r.points_per_10k == null ? "—" : pts(r.points_per_10k), "text-ink"],
        ].map(([label, v, t]) => (
          <div key={label as string}>
            <div className="tile-label">{label}</div>
            <div className={`font-mono text-sm tabular-nums ${t}`}>{v}</div>
          </div>
        ))}
      </div>
      <div className="overflow-x-auto border-t border-border-soft">
        <table className="w-full text-sm">
          <thead><tr><Head /><Head right>Now</Head><Head right>Scenario</Head><Head right>Change</Head></tr></thead>
          <tbody className="divide-y divide-border-soft">
            {before.retirement_success != null && (
              <tr>
                <td className="px-3 py-1.5 text-ink">Retirement{after.retirement_age != null ? ` at ${after.retirement_age}` : ""}</td>
                <td className="px-3 py-1.5 text-right font-mono tabular-nums">{pct(before.retirement_success)}</td>
                <td className="px-3 py-1.5 text-right font-mono tabular-nums">{pct(after.retirement_success)}</td>
                <td className={`px-3 py-1.5 text-right font-mono tabular-nums ${tone(change.retirement_points)}`}>{pts(change.retirement_points)}</td>
              </tr>
            )}
            {(change.goals ?? []).map((g: any) => (
              <tr key={g.goal}>
                <td className="px-3 py-1.5 text-ink">{g.goal}</td>
                <td className="px-3 py-1.5 text-right font-mono tabular-nums">{pct(g.before)}</td>
                <td className="px-3 py-1.5 text-right font-mono tabular-nums">{pct(g.after)}</td>
                <td className={`px-3 py-1.5 text-right font-mono tabular-nums ${tone(g.points)}`}>{pts(g.points)}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
      {parts.length > 0 && (
        <div className="flex flex-wrap gap-1.5 border-t border-border-soft px-3 py-2" data-testid="scenario-given-up">
          {parts.map((p) => (
            <span key={p.lever + p.basis} className="chip tabular-nums text-ink-muted">{money(p.amount)} · {p.basis}</span>
          ))}
        </div>
      )}
    </div>
  );
}

export function ScenariosTable({ rows, compact = false }: { rows: any[]; compact?: boolean }) {
  return (
    <div className="overflow-x-auto">
      <table className="w-full text-sm">
        <thead>
          <tr><Head>#</Head><Head>Scenario</Head><Head right>Points</Head><Head right>Funded</Head><Head right>Given up</Head><Head right>Per 10k</Head>{!compact && <Head>Saved</Head>}</tr>
        </thead>
        <tbody className="divide-y divide-border-soft">
          {rows.map((s) => (
            <tr key={s.id} data-testid="scenario-row">
              <td className="px-3 py-1.5 font-mono tabular-nums text-ink-muted">{s.rank}</td>
              <td className="px-3 py-1.5">
                <div className="text-ink">{s.name}</div>
                <div className="flex flex-wrap gap-1 pt-0.5">
                  {leverChips(s.levers ?? {}).map((c) => <span key={c} className="chip text-ink-muted">{c}</span>)}
                  {s.earlier_assumptions && <span className="chip text-warn">earlier assumptions</span>}
                </div>
              </td>
              <td className={`px-3 py-1.5 text-right font-mono tabular-nums ${tone(s.points)}`}>{pts(s.points)}</td>
              <td className={`px-3 py-1.5 text-right font-mono tabular-nums ${tone(s.funded_change)}`}>{s.funded_change == null ? "—" : `${s.funded_change > 0 ? "+" : ""}${s.funded_change.toFixed(2)}`}</td>
              <td className="px-3 py-1.5 text-right font-mono tabular-nums">{s.given_up == null ? "—" : money(s.given_up)}</td>
              <td className="px-3 py-1.5 text-right font-mono tabular-nums">{s.points_per_10k == null ? "—" : pts(s.points_per_10k)}</td>
              {!compact && <td className="px-3 py-1.5 whitespace-nowrap text-xs text-ink-muted">{s.by}</td>}
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

export function ScenariosCard({ result }: { result: any }) {
  const rows: any[] = result.scenarios ?? [];
  return (
    <div className="overflow-hidden rounded-lg border border-border bg-surface" data-testid="scenarios-card">
      <div className="flex flex-wrap items-center gap-2 border-b border-border-soft px-3 py-2">
        <div className="text-sm font-medium text-ink">Scenarios · {result.household}</div>
        <span className="chip tabular-nums text-ink-muted">{rows.length} saved</span>
        <span className="chip text-ink-muted">ranked by points per 10k given up</span>
      </div>
      {rows.length > 0 && <ScenariosTable rows={rows} />}
    </div>
  );
}
