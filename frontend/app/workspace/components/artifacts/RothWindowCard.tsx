/** The Roth window, year by year (aurea_core/roth_window.py, B-190): from retirement to the year before the first RMD,
 * each year's taxable income, its bracket and the conversion that reaches the bracket's top with the tax it adds, the
 * room to the 22% and 24% tops, Medicare's IRMAA tier two years on and the room before its next line, and the draw the
 * plan needs with how much a traditional IRA could carry inside the room. Today's money. It lays the window out; the
 * conversion is the client's. Tables and chips. */
import { money } from "@/lib/format";

const usd = (v: number | null | undefined) => (v == null ? "—" : money(v, "USD"));
const rate = (v: number | null | undefined) => (v == null ? "—" : `${Math.round(v * 100)}%`);

function Head({ children, right = false, hide = "" }: { children?: React.ReactNode; right?: boolean; hide?: string }) {
  return <th className={`px-3 py-1.5 font-mono text-[11px] font-normal uppercase tracking-wider text-ink-muted ${right ? "text-right" : "text-left"} ${hide}`}>{children}</th>;
}

function Cell({ children, muted = false, hide = "", tone = "" }: { children: React.ReactNode; muted?: boolean; hide?: string; tone?: string }) {
  return <td className={`whitespace-nowrap px-3 py-1.5 text-right font-mono text-xs tabular-nums ${tone || (muted ? "text-ink-muted" : "text-ink")} ${hide}`}>{children}</td>;
}

export function RothWindowTable({ result }: { result: any }) {
  const years: any[] = result.years ?? [];
  if (!years.length) return null;
  const hideSm = "hidden cq-md:table-cell";
  return (
    <div className="overflow-x-auto">
      <table className="w-full text-sm" data-testid="roth-window-years">
        <thead>
          <tr>
            <Head>Year</Head><Head right hide={hideSm}>Income</Head><Head right>Taxable</Head><Head right>Bracket</Head>
            <Head right>Room to top</Head><Head right hide={hideSm}>Tax on it</Head><Head right hide={hideSm}>To 22%</Head>
            <Head right hide={hideSm}>To 24%</Head><Head right>IRMAA +2y</Head><Head right hide={hideSm}>Draw · in room</Head>
          </tr>
        </thead>
        <tbody className="divide-y divide-border-soft">
          {years.map((y) => {
            const room = y.room_in_bracket;
            const ir = y.irmaa ?? {};
            return (
              <tr key={y.year} data-testid="roth-window-year">
                <td className="whitespace-nowrap px-3 py-1.5">
                  <span className="font-mono text-xs tabular-nums text-ink">{y.year}</span>
                  <span className="ml-2 font-mono text-[11px] tabular-nums text-ink-muted">
                    {Object.entries(y.ages ?? {}).map(([n, a]) => `${String(n).split(" ")[0]} ${a}`).join(" · ")}
                  </span>
                  {y.brackets_held && <span className="chip ml-2 text-ink-muted" title={`${y.bracket_year} brackets in today's money`}>held</span>}
                  {y.senior_deduction > 0 && <span className="chip ml-1 text-ink-muted" title={`deduction for seniors ${usd(y.senior_deduction)}`}>senior deduction</span>}
                </td>
                <Cell muted hide={hideSm}>{usd((y.social_security ?? 0) + (y.other_income ?? 0))}</Cell>
                <Cell>{usd(y.taxable_income)}</Cell>
                <Cell muted>{rate(y.bracket)}</Cell>
                <Cell tone={room ? "text-ink" : "text-ink-muted"}>{room ? usd(room.room) : "top"}</Cell>
                <Cell muted hide={hideSm}>{room ? `${usd(room.tax)} · ${rate(room.effective_rate)}` : "—"}</Cell>
                <Cell muted hide={hideSm}>{y.room_to?.["22"]?.already_above ? "above" : usd(y.room_to?.["22"]?.room)}</Cell>
                <Cell muted hide={hideSm}>{y.room_to?.["24"]?.already_above ? "above" : usd(y.room_to?.["24"]?.room)}</Cell>
                <Cell tone={y.bracket_room_crosses_irmaa ? "text-warn" : "text-ink-muted"}>
                  {(ir.on_medicare ?? []).length === 0 ? "—" : `T${ir.tier} · ${ir.room == null ? "top" : usd(ir.room)}`}
                </Cell>
                <Cell muted hide={hideSm}>{y.draw == null ? "—" : `${usd(y.draw)} · ${usd(y.draw_within_room)}`}</Cell>
              </tr>
            );
          })}
        </tbody>
      </table>
    </div>
  );
}

export function RothWindowChips({ result }: { result: any }) {
  const w = result.window;
  const t = result.totals;
  return (
    <>
      <span className="chip text-ink-muted">today&rsquo;s money</span>
      <span className="chip text-ink-muted">lays out · the conversion is the client&rsquo;s</span>
      {result.status === "not_assessed" && <span className="chip text-warn" title={result.reason}>not assessed</span>}
      {result.status === "closed" && <span className="chip text-ink-muted" title={result.reason}>window closed · RMDs started</span>}
      {w && <span className="chip tabular-nums text-ink-muted" title={w.ends}>{w.from_year}–{w.to_year}</span>}
      {result.filing_label && (
        <span className={`chip ${String(result.filing_basis ?? "").startsWith("assumed") ? "text-warn" : "text-ink-muted"}`}>
          {result.filing_label} · {result.filing_basis}
        </span>
      )}
      {t && <span className="chip tabular-nums text-ink">room {usd(t.room_in_brackets)} · IRA {usd(t.ira_balance)}</span>}
      {t?.years_crossing_irmaa > 0 && <span className="chip tabular-nums text-warn">crosses an IRMAA line · {t.years_crossing_irmaa}y</span>}
      {(result.assumed ?? []).map((a: string) => <span key={a} className="chip text-warn">{a.replace(/_/g, " ")}</span>)}
    </>
  );
}

export function RothWindowCard({ result }: { result: any }) {
  return (
    <div className="overflow-hidden rounded-lg border border-border bg-surface" data-testid="roth-window-card">
      <div className="flex flex-wrap items-center gap-2 border-b border-border-soft px-3 py-2">
        <div className="text-sm font-medium text-ink">Roth window · {result.household}</div>
        <RothWindowChips result={result} />
      </div>
      <RothWindowTable result={result} />
      {(result.not_modelled ?? []).length > 0 && (
        <div className="flex flex-wrap gap-1 border-t border-border-soft px-3 py-2">
          {result.not_modelled.map((k: string) => <span key={k} className="chip text-ink-muted">{k.replace(/_/g, " ")} not modelled</span>)}
        </div>
      )}
    </div>
  );
}
