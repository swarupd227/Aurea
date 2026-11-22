/** Filed US returns on file for a household, as a person recorded them from the documents (aurea_core/tax_returns.py,
 * B-183): year, filing status, who is on it, AGI, taxable income, rates, carryforwards, 8606 basis, state. Tables and chips. */
import { money } from "@/lib/format";

const usd = (v: number | null | undefined) => (v == null ? "—" : money(v, "USD"));
const rate = (v: number | null | undefined) => (v == null ? "—" : `${(v * 100).toFixed(1)}%`);

function Head({ children, right = false }: { children?: React.ReactNode; right?: boolean }) {
  return <th className={`px-3 py-1.5 font-mono text-[11px] font-normal uppercase tracking-wider text-ink-muted ${right ? "text-right" : "text-left"}`}>{children}</th>;
}

export function TaxReturnsCard({ result }: { result: any }) {
  const rows: any[] = Array.isArray(result.returns) ? result.returns : [result];
  return (
    <div className="overflow-hidden rounded-lg border border-border bg-surface" data-testid="tax-returns-card">
      <div className="flex flex-wrap items-center gap-2 border-b border-border-soft px-3 py-2">
        <div className="text-sm font-medium text-ink">Tax returns{result.household ? ` · ${result.household}` : ""}</div>
        <span className="chip tabular-nums text-ink-muted">{rows.length} on file</span>
        <span className="chip text-ink-muted">from the documents</span>
      </div>
      {rows.length > 0 && (
        <div className="overflow-x-auto">
          <table className="w-full text-sm">
            <thead>
              <tr><Head>Year</Head><Head>Return</Head><Head right>AGI</Head><Head right>Taxable</Head><Head right>Marginal</Head><Head right>Effective</Head><Head>Also</Head></tr>
            </thead>
            <tbody className="divide-y divide-border-soft">
              {rows.map((r) => (
                <tr key={r.id} data-testid="tax-return-row">
                  <td className="px-3 py-1.5 font-mono tabular-nums text-ink">{r.tax_year}</td>
                  <td className="px-3 py-1.5">
                    <div className="text-ink">{(r.people ?? []).join(" & ")}</div>
                    <div className="flex flex-wrap gap-1 pt-0.5">
                      <span className="chip text-ink-muted">{r.filing_label}</span>
                      {r.standing === false && <span className="chip text-ink-muted">superseded</span>}
                      {r.state && <span className="chip text-ink-muted">{r.state}</span>}
                    </div>
                  </td>
                  <td className="px-3 py-1.5 text-right font-mono tabular-nums">{usd(r.agi)}</td>
                  <td className="px-3 py-1.5 text-right font-mono tabular-nums">{usd(r.taxable_income)}</td>
                  <td className="px-3 py-1.5 text-right font-mono tabular-nums">{rate(r.marginal_rate)}</td>
                  <td className="px-3 py-1.5 text-right font-mono tabular-nums">{rate(r.effective_rate)}</td>
                  <td className="px-3 py-1.5">
                    <div className="flex flex-wrap gap-1">
                      {r.qbi_deduction != null && <span className="chip tabular-nums text-ink-muted">QBI {usd(r.qbi_deduction)}</span>}
                      {(r.carryforwards ?? []).map((c: any) => (
                        <span key={c.kind} className="chip tabular-nums text-ink-muted">{c.kind.replace(/_/g, " ")} c/f {usd(c.amount)}</span>
                      ))}
                      {Object.entries(r.basis_8606_by_name ?? {}).map(([name, amt]: any) => (
                        <span key={name} className="chip tabular-nums text-ink-muted">8606 {name} {usd(amt)}</span>
                      ))}
                      <span className="chip text-ink-muted" title={r.document}>{r.recorded_by} · {r.as_of}</span>
                    </div>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </div>
  );
}
