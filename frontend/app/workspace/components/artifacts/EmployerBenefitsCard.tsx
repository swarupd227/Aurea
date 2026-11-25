/** What an employer's plan gives each person (aurea_core/employer_benefits.py, B-193): a retirement match and deferred
 * compensation elections, from the plan document. Tables and chips. None of it moves a plan figure. */
import { money } from "@/lib/format";

const usd = (v: number | null | undefined) => (v == null ? "—" : money(v, "USD"));
const said = (k: string) => k.replace(/_/g, " ");

function Head({ children, right = false }: { children?: React.ReactNode; right?: boolean }) {
  return <th className={`px-3 py-1.5 font-mono text-[11px] font-normal uppercase tracking-wider text-ink-muted ${right ? "text-right" : "text-left"}`}>{children}</th>;
}

export function BenefitChips({ result }: { result: any }) {
  const s = result.summary ?? {};
  return (
    <>
      <span className="chip tabular-nums text-ink-muted">{s.matches ?? 0} matches</span>
      <span className="chip tabular-nums text-ink-muted">{s.deferrals ?? 0} deferrals</span>
      {s.most_matches_add_a_year > 0 && <span className="chip tabular-nums text-ink">matches add up to {usd(s.most_matches_add_a_year)} a year</span>}
      {s.matches_not_assessed > 0 && <span className="chip tabular-nums text-warn">{s.matches_not_assessed} not assessed · no income on file</span>}
      {(result.adults_with_none ?? []).length > 0 && <span className="chip text-ink-muted" title={result.not_on_file}>none on file · {result.adults_with_none.join(", ")}</span>}
      <span className="chip text-ink-muted">moves no figure</span>
    </>
  );
}

export function BenefitsTable({ result }: { result: any }) {
  const rows: any[] = result.benefits ?? [];
  if (!rows.length) return <div className="px-3 py-3"><span className="chip text-ink-muted">none on file</span></div>;
  return (
    <div className="overflow-x-auto">
      <table className="w-full text-sm" data-testid="employer-benefits">
        <thead><tr><Head>Person</Head><Head>Benefit</Head><Head right>Terms</Head><Head right>Up to a year</Head></tr></thead>
        <tbody className="divide-y divide-border-soft">
          {rows.map((b) => (
            <tr key={b.id} data-testid="employer-benefit" className={b.active ? "" : "opacity-60"} title={b.document}>
              <td className="px-3 py-1.5 text-ink">{b.person}</td>
              <td className="px-3 py-1.5">
                <span className="text-ink">{said(b.kind)}</span>
                <span className="ml-2 text-xs text-ink-muted">{b.employer}</span>
                {!b.active && <span className="chip ml-2 text-ink-muted" title={b.ended_reason}>ended</span>}
              </td>
              <td className="whitespace-nowrap px-3 py-1.5 text-right font-mono text-xs tabular-nums text-ink-muted">
                {b.kind === "retirement_match"
                  ? `${Math.round(b.match_rate * 100)}% up to ${(b.match_up_to * 100).toFixed(1)}% of pay${b.vesting ? ` · ${b.vesting}` : ""}`
                  : `${usd(b.annual_deferral)} deferred · from ${b.payout_start_age} · ${said(b.payout_form)}${b.payout_years ? ` over ${b.payout_years}y` : ""}`}
              </td>
              <td className="whitespace-nowrap px-3 py-1.5 text-right font-mono text-xs tabular-nums text-ink">
                {b.kind === "retirement_match" ? (b.figures === "assessed" ? usd(b.most_it_adds_a_year) : <span className="chip text-warn" title={b.figures_reason}>not assessed</span>) : "—"}
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

export function EmployerBenefitsCard({ result }: { result: any }) {
  return (
    <div className="overflow-hidden rounded-lg border border-border bg-surface" data-testid="employer-benefits-card">
      <div className="flex flex-wrap items-center gap-2 border-b border-border-soft px-3 py-2">
        <div className="text-sm font-medium text-ink">Employer benefits · {result.household}</div>
        <BenefitChips result={result} />
      </div>
      <BenefitsTable result={result} />
      {(result.not_modelled ?? []).length > 0 && (
        <div className="flex flex-wrap gap-1 border-t border-border-soft px-3 py-2">
          {result.not_modelled.map((k: string) => <span key={k} className="chip text-ink-muted">{said(k)} not modelled</span>)}
        </div>
      )}
    </div>
  );
}
