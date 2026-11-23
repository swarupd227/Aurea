/** Healthcare a US retirement plan funds (aurea_core/healthcare.py, B-191): each year, each adult's phase and cost in
 * today's money (before Medicare, the firm's stated cost; on Medicare, the firm's cost beyond Part B, Part B and the
 * IRMAA surcharge), and the year's total. Not assessed until the firm states a cost. Tables and chips. */
import { money } from "@/lib/format";

const usd = (v: number | null | undefined) => (v == null ? "—" : money(v, "USD"));

function Head({ children, right = false }: { children?: React.ReactNode; right?: boolean }) {
  return <th className={`px-3 py-1.5 font-mono text-[11px] font-normal uppercase tracking-wider text-ink-muted ${right ? "text-right" : "text-left"}`}>{children}</th>;
}

export function HealthcareTable({ result }: { result: any }) {
  const years: any[] = result.years ?? [];
  if (!years.length) return null;
  const people = Object.keys(years[0].people ?? {});
  return (
    <div className="max-h-96 overflow-auto">
      <table className="w-full text-sm" data-testid="healthcare-years">
        <thead className="sticky top-0 bg-surface">
          <tr><Head>Year</Head>{people.map((n) => <Head key={n} right>{n.split(" ")[0]}</Head>)}<Head right>Total</Head></tr>
        </thead>
        <tbody className="divide-y divide-border-soft">
          {years.map((y) => (
            <tr key={y.year} data-testid="healthcare-year">
              <td className="px-3 py-1.5 font-mono text-xs tabular-nums text-ink">{y.year}</td>
              {people.map((n) => {
                const p = y.people[n] ?? {};
                const title = p.phase === "medicare"
                  ? `stated ${usd(p.stated)} · Part B ${usd(p.part_b)} · IRMAA ${p.irmaa == null ? "not assessed" : usd(p.irmaa)}${p.irmaa_read ? ` (${String(p.irmaa_read).replace(/_/g, " ")})` : ""}`
                  : p.phase === "before_medicare" ? "before Medicare · firm's stated cost" : undefined;
                return (
                  <td key={n} className="whitespace-nowrap px-3 py-1.5 text-right font-mono text-xs tabular-nums" title={title}>
                    <span className="mr-1.5 text-[11px] text-ink-muted">{p.age} · {p.phase === "medicare" ? "Medicare" : "pre-65"}</span>
                    {p.status === "not_assessed" ? <span className="text-warn">not assessed</span> : <span className="text-ink">{usd(p.amount_today)}</span>}
                    {p.phase === "medicare" && p.irmaa > 0 && <span className="chip ml-1 text-warn">IRMAA</span>}
                  </td>
                );
              })}
              <td className="whitespace-nowrap px-3 py-1.5 text-right font-mono text-xs tabular-nums text-ink">{usd(y.total_today)}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

export function HealthcareChips({ result }: { result: any }) {
  const c = result.costs ?? {};
  return (
    <>
      <span className="chip text-ink-muted">today&rsquo;s money</span>
      {result.status === "not_assessed" && <span className="chip text-warn" title={result.reason}>not assessed</span>}
      {result.status === "partly_assessed" && <span className="chip text-warn">partly assessed</span>}
      {c.pre65 != null && <span className="chip tabular-nums text-ink-muted">pre-65 {usd(c.pre65)}/yr stated</span>}
      {c.medicare_beyond_part_b != null && <span className="chip tabular-nums text-ink-muted">Medicare beyond Part B {usd(c.medicare_beyond_part_b)}/yr stated</span>}
      {result.first_year_today != null && <span className="chip tabular-nums text-ink">first year {usd(result.first_year_today)}</span>}
      {result.total_today != null && result.status !== "not_assessed" && <span className="chip tabular-nums text-ink">in all {usd(result.total_today)}</span>}
      {result.inflation?.healthcare != null && <span className="chip tabular-nums text-ink-muted">healthcare inflation {(result.inflation.healthcare * 100).toFixed(1)}%</span>}
      {(result.not_assessed ?? []).map((a: string) => <span key={a} className="chip text-warn">{a}</span>)}
      {(result.assumed ?? []).map((a: string) => <span key={a} className="chip text-warn">{a.replace(/_/g, " ")}</span>)}
    </>
  );
}

export function HealthcareCard({ result }: { result: any }) {
  return (
    <div className="overflow-hidden rounded-lg border border-border bg-surface" data-testid="healthcare-card">
      <div className="flex flex-wrap items-center gap-2 border-b border-border-soft px-3 py-2">
        <div className="text-sm font-medium text-ink">Healthcare in retirement · {result.household}</div>
        <HealthcareChips result={result} />
      </div>
      <HealthcareTable result={result} />
      {(result.not_modelled ?? []).length > 0 && (
        <div className="flex flex-wrap gap-1 border-t border-border-soft px-3 py-2">
          {result.not_modelled.map((k: string) => <span key={k} className="chip text-ink-muted">{k.replace(/_/g, " ")} not modelled</span>)}
        </div>
      )}
    </div>
  );
}
