/** Giving, laid out (aurea_core/giving.py, B-192): from an IRA (who may, the limit, the RMD it could satisfy), stock held
 * over a year with the largest gains, and a donor-advised fund bunching year against giving each year. Figures only; what
 * to give is the client's, and what they decide goes on the advice file. Tables and chips. */
import { money } from "@/lib/format";

const usd = (v: number | null | undefined) => (v == null ? "—" : money(v, "USD"));
const pct = (v: number | null | undefined) => (v == null ? "—" : `${Math.round(v * 100)}%`);

function Head({ children, right = false }: { children?: React.ReactNode; right?: boolean }) {
  return <th className={`px-3 py-1.5 font-mono text-[11px] font-normal uppercase tracking-wider text-ink-muted ${right ? "text-right" : "text-left"}`}>{children}</th>;
}
function Num({ children, muted = false }: { children: React.ReactNode; muted?: boolean }) {
  return <td className={`whitespace-nowrap px-3 py-1.5 text-right font-mono text-xs tabular-nums ${muted ? "text-ink-muted" : "text-ink"}`}>{children}</td>;
}
function Label({ children }: { children: React.ReactNode }) {
  return <div className="border-b border-border-soft px-3 pb-1.5 pt-3 text-[10px] font-medium uppercase tracking-wide text-ink-muted">{children}</div>;
}

export function GivingSections({ result }: { result: any }) {
  const q = result.qcd ?? { people: [] };
  const st = result.stock ?? { lots: [] };
  const b = result.bunching ?? { status: "not_assessed", rows: [] };
  return (
    <div data-testid="giving-sections">
      <Label>From an IRA · {q.tax_year}</Label>
      {q.people.length === 0 ? <div className="px-3 py-2 text-xs text-ink-muted">No traditional IRA on file</div> : (
        <div className="overflow-x-auto">
          <table className="w-full text-sm" data-testid="giving-qcd">
            <thead><tr><Head>Owner</Head><Head right>IRA</Head><Head right>Limit</Head><Head right>RMD this year</Head><Head right>Satisfies</Head></tr></thead>
            <tbody className="divide-y divide-border-soft">
              {q.people.map((p: any) => (
                <tr key={p.person}>
                  <td className="whitespace-nowrap px-3 py-1.5 text-ink">
                    {p.person}
                    <span className={`chip ml-2 ${p.eligible_now ? "text-ok" : "text-ink-muted"}`}>{p.eligible_now ? "eligible" : `from ${p.eligible_from}`}</span>
                  </td>
                  <Num muted>{usd(p.ira_balance)}</Num>
                  <Num>{usd(p.limit)}{p.next_year_limit ? <span className="ml-1 text-ink-muted">· {usd(p.next_year_limit)} next</span> : null}</Num>
                  <Num muted>{p.rmd_this_year == null ? "none yet" : usd(p.rmd_this_year)}</Num>
                  <Num>{p.satisfies_rmd_up_to == null ? "—" : usd(p.satisfies_rmd_up_to)}</Num>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}

      <Label>Stock held over a year · largest gain first</Label>
      <div className="flex flex-wrap gap-1.5 px-3 py-2">
        <span className="chip tabular-nums text-ink-muted">{st.long_held_with_gain ?? 0} long-held with a gain</span>
        {st.short_held_with_gain > 0 && <span className="chip tabular-nums text-ink-muted">{st.short_held_with_gain} held a year or less · deducted at cost</span>}
        {st.deduction_cap != null ? <span className="chip tabular-nums text-ink-muted">deduction cap {usd(st.deduction_cap)} · 30% of AGI</span>
          : <span className="chip text-warn">cap not assessed · no return on file</span>}
      </div>
      {st.lots.length > 0 && (
        <div className="overflow-x-auto">
          <table className="w-full text-sm" data-testid="giving-lots">
            <thead><tr><Head>Lot</Head><Head right>Value</Head><Head right>Gain</Head><Head right>Gain share</Head><Head right>Held</Head></tr></thead>
            <tbody className="divide-y divide-border-soft">
              {st.lots.map((l: any, i: number) => (
                <tr key={i}>
                  <td className="max-w-0 px-3 py-1.5" title={l.instrument}>
                    <span className="font-mono text-xs text-ink">{l.symbol}</span>
                    <span className="ml-2 truncate text-xs text-ink-muted">{l.account}</span>
                  </td>
                  <Num muted>{usd(l.value)}</Num><Num>{usd(l.gain)}</Num><Num muted>{pct(l.gain_share)}</Num><Num muted>{l.held_days}d</Num>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}

      <Label>Donor-advised fund · bunching</Label>
      {b.status !== "assessed" ? (
        <div className="flex flex-wrap gap-1.5 px-3 py-2"><span className="chip text-warn" title={b.reason}>not assessed</span></div>
      ) : (
        <>
          <div className="flex flex-wrap gap-1.5 px-3 py-2">
            <span className="chip tabular-nums text-ink-muted">{usd(b.annual_giving)} a year, as said</span>
            <span className="chip tabular-nums text-ink-muted">AGI {usd(b.agi)} · {b.return_year} return</span>
            <span className="chip tabular-nums text-ink-muted">standard {usd(b.standard_with_aged)}</span>
            <span className="chip tabular-nums text-ink-muted">floor {usd(b.floor)}</span>
            {b.deduction_worth_rate != null && <span className="chip tabular-nums text-ink-muted">worth {pct(b.deduction_worth_rate)} a dollar</span>}
          </div>
          <div className="overflow-x-auto">
            <table className="w-full text-sm" data-testid="giving-bunching">
              <thead><tr><Head>Years in one</Head><Head right>Put in</Head><Head right>Bunched</Head><Head right>Each year</Head><Head right>Difference</Head><Head right>Worth</Head></tr></thead>
              <tbody className="divide-y divide-border-soft">
                {b.rows.map((r: any) => (
                  <tr key={r.years}>
                    <td className="whitespace-nowrap px-3 py-1.5 font-mono text-xs tabular-nums text-ink">
                      {r.years}{r.over_cash_limit && <span className="chip ml-2 text-warn">over the 60% limit</span>}
                    </td>
                    <Num muted>{usd(r.put_in)}</Num><Num>{usd(r.deductions_bunched)}</Num><Num muted>{usd(r.deductions_each_year)}</Num>
                    <Num>{usd(r.extra_deduction)}</Num><Num muted>{usd(r.worth)}</Num>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </>
      )}
    </div>
  );
}

export function GivingCard({ result }: { result: any }) {
  return (
    <div className="overflow-hidden rounded-lg border border-border bg-surface" data-testid="giving-card">
      <div className="flex flex-wrap items-center gap-2 border-b border-border-soft px-3 py-2">
        <div className="text-sm font-medium text-ink">Giving · {result.household}</div>
        <span className="chip text-ink-muted">lays out · what to give is the client&rsquo;s</span>
        {result.status !== "assessed" && <span className="chip text-warn" title={result.reason}>not assessed</span>}
        {(result.donor_advised_funds_on_file ?? []).map((n: string) => <span key={n} className="chip text-ink-muted">fund on file · {n}</span>)}
      </div>
      {result.status === "assessed" && <GivingSections result={result} />}
      {(result.not_modelled ?? []).length > 0 && (
        <div className="flex flex-wrap gap-1 border-t border-border-soft px-3 py-2">
          {result.not_modelled.map((k: string) => <span key={k} className="chip text-ink-muted">{k.replace(/_/g, " ")} not modelled</span>)}
        </div>
      )}
    </div>
  );
}
