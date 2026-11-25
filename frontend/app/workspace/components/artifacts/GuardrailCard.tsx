/** Guardrail spending beside fixed spending (aurea_core/guardrails.py, B-180): the portfolio's first draw, the likelihood it
 * lasts, how often cuts come and how low income falls, on the same random returns. A run; nothing changes spending by itself.
 * Tables and chips. */
import { money } from "@/lib/format";

const usd = (v: number | null | undefined) => (v == null ? "—" : money(v, "USD"));
const pct = (v: number | null | undefined, d = 0) => (v == null ? "—" : `${(v * 100).toFixed(d)}%`);

function Head({ children, right = false }: { children?: React.ReactNode; right?: boolean }) {
  return <th className={`px-3 py-1.5 font-mono text-[11px] font-normal uppercase tracking-wider text-ink-muted ${right ? "text-right" : "text-left"}`}>{children}</th>;
}
function Num({ children, muted = false }: { children: React.ReactNode; muted?: boolean }) {
  return <td className={`whitespace-nowrap px-3 py-1.5 text-right font-mono text-xs tabular-nums ${muted ? "text-ink-muted" : "text-ink"}`}>{children}</td>;
}

export function GuardrailChips({ result }: { result: any }) {
  const t = result.terms;
  return (
    <>
      <span className="chip text-ink-muted">today&rsquo;s money</span>
      <span className="chip text-ink-muted">a run beside fixed spending · changes nothing</span>
      {result.status !== "assessed" && <span className="chip text-warn" title={result.reason}>not assessed</span>}
      {t && <span className="chip tabular-nums text-ink-muted" title={t.start_rate_derived ? "the plan's own first draw over the pot at retirement" : "as the terms say"}>
        start {pct(t.start_rate, 2)}{t.start_rate_derived ? " · plan's own" : ""}</span>}
      {t && <span className="chip tabular-nums text-ink-muted">cut {pct(t.cut)} past +{pct(t.upper_band)}</span>}
      {t && <span className="chip tabular-nums text-ink-muted">raise {pct(t.raise)} past −{pct(t.lower_band)}</span>}
      {t?.skip_inflation && <span className="chip text-ink-muted">no inflation after a down year</span>}
      {result.terms_from && <span className="chip text-ink-muted">{result.terms_from === "ips" ? `IPS v${result.ips?.ips_version}` : "terms as said"}</span>}
    </>
  );
}

export function GuardrailTable({ result }: { result: any }) {
  if (result.status !== "assessed") return null;
  return (
    <div className="overflow-x-auto">
      <table className="w-full text-sm" data-testid="guardrail-table">
        <thead><tr><Head></Head><Head right>Fixed spending</Head><Head right>Guardrails</Head></tr></thead>
        <tbody className="divide-y divide-border-soft">
          <tr><td className="px-3 py-1.5 text-ink">Portfolio draw, first year</td><Num muted>{usd(result.fixed_draw_today)}</Num><Num>{usd(result.start_draw_today)}</Num></tr>
          <tr><td className="px-3 py-1.5 text-ink">Likelihood the portfolio lasts</td><Num muted>{pct(result.fixed_success)}</Num><Num>{pct(result.success)}</Num></tr>
          <tr><td className="px-3 py-1.5 text-ink">Futures with a cut</td><Num muted>0%</Num><Num>{pct(result.paths_with_a_cut)}</Num></tr>
          <tr><td className="px-3 py-1.5 text-ink">Cuts, median · worst tenth</td><Num muted>0</Num><Num>{result.median_cuts} · {result.worst_tenth_cuts}</Num></tr>
          <tr><td className="px-3 py-1.5 text-ink">Years cut, of years lived</td><Num muted>0%</Num><Num>{pct(result.share_of_years_cut)}</Num></tr>
          <tr><td className="px-3 py-1.5 text-ink">Lowest real income, median · worst tenth</td><Num muted>100%</Num><Num>{pct(result.lowest_income_median)} · {pct(result.lowest_income_worst_tenth)}</Num></tr>
          <tr><td className="px-3 py-1.5 text-ink">Futures with a raise</td><Num muted>0%</Num><Num>{pct(result.paths_with_a_raise)}</Num></tr>
        </tbody>
      </table>
    </div>
  );
}

export function GuardrailCard({ result }: { result: any }) {
  return (
    <div className="overflow-hidden rounded-lg border border-border bg-surface" data-testid="guardrail-card">
      <div className="flex flex-wrap items-center gap-2 border-b border-border-soft px-3 py-2">
        <div className="text-sm font-medium text-ink">Guardrail spending · {result.household}</div>
        <GuardrailChips result={result} />
      </div>
      <GuardrailTable result={result} />
      {(result.not_modelled ?? []).length > 0 && (
        <div className="flex flex-wrap gap-1 border-t border-border-soft px-3 py-2">
          {result.not_modelled.map((k: string) => <span key={k} className="chip text-ink-muted">{k.replace(/_/g, " ")} not modelled</span>)}
        </div>
      )}
    </div>
  );
}
