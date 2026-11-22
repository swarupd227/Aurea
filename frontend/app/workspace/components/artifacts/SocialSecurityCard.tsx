/** Social Security for a couple (aurea_core/social_security.py, B-184): each pair of claim ages with the yearly benefit
 * while both live, the survivor's, the total to the plan-to ages, the bridge the portfolio carries and the retirement
 * likelihood with those claims. It lays out; the claim is the client's. Tables and chips. */
import { money } from "@/lib/format";

const usd = (v: number | null | undefined) => (v == null ? "—" : money(v, "USD"));
const pct = (v: number | null | undefined) => (v == null ? "—" : `${Math.round(v * 100)}%`);

function Head({ children, right = false }: { children?: React.ReactNode; right?: boolean }) {
  return <th className={`px-3 py-1.5 font-mono text-[11px] font-normal uppercase tracking-wider text-ink-muted ${right ? "text-right" : "text-left"}`}>{children}</th>;
}

export function SocialSecurityTable({ result }: { result: any }) {
  const people: any[] = result.people ?? [];
  const pairs: any[] = result.pairs ?? [];
  const [a, b] = people;
  if (!pairs.length) return null;
  const best = Math.max(...pairs.map((p) => p.retirement_success ?? -1));
  return (
    <div className="overflow-x-auto">
      <table className="w-full text-sm" data-testid="social-security-pairs">
        <thead>
          <tr>
            <Head>Claim at</Head><Head right>Both alive /yr</Head>
            {b && <Head right>If {a.name} dies</Head>}{b && <Head right>If {b.name} dies</Head>}
            <Head right>To age {result.plan_to_age}</Head><Head right>Discounted</Head><Head right>Bridge</Head><Head right>Retirement</Head>
          </tr>
        </thead>
        <tbody className="divide-y divide-border-soft">
          {pairs.map((p, i) => (
            <tr key={i} data-testid="social-security-pair">
              <td className="px-3 py-1.5 whitespace-nowrap">
                <span className="font-mono tabular-nums text-ink">{a.name.split(" ")[0]} {p.claim[a.id]}{b ? ` · ${b.name.split(" ")[0]} ${p.claim[b.id]}` : ""}</span>
                {p.on_file && <span className="chip ml-2 text-ink-muted">on file</span>}
              </td>
              <td className="px-3 py-1.5 text-right font-mono tabular-nums">{usd(p.both)}</td>
              {b && <td className="px-3 py-1.5 text-right font-mono tabular-nums">{usd(p.survivor[`if_${a.id}_dies`])}</td>}
              {b && <td className="px-3 py-1.5 text-right font-mono tabular-nums">{usd(p.survivor[`if_${b.id}_dies`])}</td>}
              <td className="px-3 py-1.5 text-right font-mono tabular-nums">{usd(p.lifetime_today)}</td>
              <td className="px-3 py-1.5 text-right font-mono tabular-nums text-ink-muted">{usd(p.lifetime_pv)}</td>
              <td className="px-3 py-1.5 text-right font-mono tabular-nums text-ink-muted">{usd(p.bridge_today)}</td>
              <td className={`px-3 py-1.5 text-right font-mono tabular-nums ${p.retirement_success === best ? "text-ink" : "text-ink-muted"}`}>{pct(p.retirement_success)}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

export function SocialSecurityCard({ result }: { result: any }) {
  const people: any[] = result.people ?? [];
  return (
    <div className="overflow-hidden rounded-lg border border-border bg-surface" data-testid="social-security-card">
      <div className="flex flex-wrap items-center gap-2 border-b border-border-soft px-3 py-2">
        <div className="text-sm font-medium text-ink">Social Security · {result.household}</div>
        <span className="chip text-ink-muted">today&rsquo;s money</span>
        <span className="chip text-ink-muted">lays out · the claim is the client&rsquo;s</span>
        {result.status === "not_assessed" && <span className="chip text-warn">not assessed</span>}
        {result.status === "one_statement" && <span className="chip text-warn">one statement · spousal and survivor not assessed</span>}
        {(result.without_statement ?? []).map((n: string) => <span key={n} className="chip text-warn">no statement · {n}</span>)}
        {result.discount_rate != null && (
          <span className="chip tabular-nums text-ink-muted">{(result.discount_rate * 100).toFixed(2)}% {result.discount_whose === "firm" ? "firm rate" : "cash return"}</span>
        )}
      </div>
      {people.length > 0 && (
        <div className="flex flex-wrap gap-1.5 border-b border-border-soft px-3 py-2">
          {people.map((p) => (
            <span key={p.id} className="chip tabular-nums text-ink" title={p.document}>
              {p.name} · {p.age} · full age {p.full_retirement_age} · {usd(p.pia)} at full age · claims {p.claim_on_file}
            </span>
          ))}
        </div>
      )}
      <SocialSecurityTable result={result} />
      {(result.not_modelled ?? []).length > 0 && (
        <div className="flex flex-wrap gap-1 border-t border-border-soft px-3 py-2">
          {result.not_modelled.map((k: string) => <span key={k} className="chip text-ink-muted">{k.replace(/_/g, " ")} not modelled</span>)}
        </div>
      )}
    </div>
  );
}
