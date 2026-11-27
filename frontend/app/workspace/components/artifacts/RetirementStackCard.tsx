import { money } from "@/lib/format";
import { returnsLabel } from "@/lib/planBasis";
import { CounselChip } from "./GovernanceCard";

/** B-395: India's retirement stack inside the projection. The answer with and without it, the named inputs behind it, NPS
 * at exit, the income floor, the liquid defence and the drawing year by year. Figures, tables and chips only. */

const pc = (v: number | null | undefined) => (v == null ? "—" : `${Math.round(v * 100)}%`);
const SCHEME: Record<string, string> = { epf: "EPF", ppf: "PPF", nps: "NPS", scss: "SCSS", eps: "EPS pension", annuity: "annuity" };
const th = "px-3 py-1.5 font-medium";
const num = "px-3 py-1.5 text-right font-mono text-xs tabular-nums text-ink";
const muted = "px-3 py-1.5 text-xs text-ink-muted";
const head = "text-left text-[10px] uppercase tracking-wide text-ink-muted";

function Section({ title, chips, children, testid }: { title: string; chips?: React.ReactNode; children?: React.ReactNode; testid: string }) {
  return (
    <div className="border-t border-border-soft" data-testid={testid}>
      <div className="flex flex-wrap items-center gap-1.5 px-3 pt-2">
        <div className="mr-1 text-xs font-medium uppercase tracking-wide text-ink-muted">{title}</div>
        {chips}
      </div>
      {children}
    </div>
  );
}

function Table({ cols, children }: { cols: (string | [string, "right"])[]; children: React.ReactNode }) {
  return (
    <div className="overflow-x-auto py-1">
      <table className="w-full text-sm">
        <thead><tr className={head}>{cols.map((c) => (Array.isArray(c) ? <th key={c[0]} className={`${th} text-right`}>{c[0]}</th> : <th key={c} className={th}>{c}</th>))}</tr></thead>
        <tbody className="divide-y divide-border-soft">{children}</tbody>
      </table>
    </div>
  );
}

export function RetirementStackChips({ result }: { result: any }) {
  const s = result.stack;
  return (
    <>
      <span className="chip text-ink-muted">estimates</span>
      <span className="chip text-ink-muted">held elsewhere · in no managed or billed total</span>
      <span className="chip text-warn">rules assumed · not reviewed</span>
      {result.assumptions_source && <span className="chip text-ink-muted">{returnsLabel(result.assumptions_source)}{result.assumptions_version != null ? ` v${result.assumptions_version}` : ""}</span>}
      {result.what_if && <span className="chip text-info" data-testid="stack-what-if">what-if · nothing on file changed</span>}
      {s && <span className="chip tabular-nums text-ink">{money(s.value_on_file)} on file · {money(s.monthly_contributions)}/month recorded</span>}
    </>
  );
}

function WithAndWithout({ result }: { result: any }) {
  const { stack: s, funded: f, retirement: r } = result;
  const rows: [string, string, string][] = [
    ["Retirement success", pc(s.with.success_probability), pc(s.without.success_probability)],
    ["Funded ratio", f?.ratio != null ? f.ratio.toFixed(2) : "—", f?.ratio_without_stack != null ? f.ratio_without_stack.toFixed(2) : "—"],
    ["Pot at retirement, today's money", money(s.with.projected_at_retirement_today), money(s.without.projected_at_retirement_today)],
    ["Sustainable a year, today's money", money(s.with.sustainable_income), money(s.without.sustainable_income)],
    ["Guaranteed a year at retirement", money(s.with.guaranteed_at_retirement), money(s.without.guaranteed_at_retirement)],
    ["Runs out at (median of failures)", String(s.with.median_depletion_age ?? "—"), String(s.without.median_depletion_age ?? "—")],
  ];
  return (
    <Section title={`Retirement at ${r.retirement_age}`} testid="stack-compare"
      chips={<span className={`chip tabular-nums ${s.change_points > 0 ? "text-ok" : "text-ink-muted"}`}>{s.change_points > 0 ? "+" : ""}{s.change_points} points</span>}>
      <Table cols={["Figure", ["With the stack", "right"], ["Without", "right"]]}>
        {rows.map(([label, a, b]) => (
          <tr key={label}><td className={muted}>{label}</td><td className={num}>{a}</td><td className={`${num} text-ink-muted`}>{b}</td></tr>
        ))}
      </Table>
    </Section>
  );
}

function Inputs({ result }: { result: any }) {
  const s = result.stack;
  return (
    <Section title="Named inputs" testid="stack-inputs"
      chips={<>
        {(s.assumed ?? []).map((a: string) => <span key={a} className="chip text-warn">{a}</span>)}
        {(s.check ?? []).map((a: string) => <span key={a} className="chip text-warn" title={a}>check · goal contribution beside these</span>)}
        {(result.not_in_stack ?? []).map((n: any) => <span key={n.item_id} className="chip text-warn" title="A pension total on net worth with no statement line is not read by the projection">not counted · {n.name} {money(n.amount)}</span>)}
      </>}>
      <Table cols={["Input", ["Value", "right"], ["A month", "right"], ["Stated", "right"], "Source", ""]}>
        {s.items.map((i: any, k: number) => (
          <tr key={`${i.label}-${i.type}-${k}`}>
            <td className="px-3 py-1.5 text-ink">{i.label}{i.type === "contribution" ? <span className="ml-1 text-xs text-ink-muted">{i.payer === "employer" ? "employer pays" : "own"}</span> : null}</td>
            <td className={num}>{i.type === "corpus" ? money(i.value) : i.type === "pension" ? `${money(i.annual)}/yr from ${i.start_age}` : "—"}</td>
            <td className={num}>{i.type === "contribution" ? money(i.monthly) : "—"}</td>
            <td className={`${num} text-ink-muted`}>{i.value_on}</td>
            <td className={muted}><span title={i.document}>{i.institution ?? i.document}</span></td>
            <td className="px-3 py-1.5">{!i.counted && <span className="chip text-warn" title={i.why}>not counted</span>}</td>
          </tr>
        ))}
      </Table>
      {s.excluded.length > 0 && (
        <div className="flex flex-wrap gap-1.5 px-3 pb-2">{s.excluded.map((e: any) => <span key={e.label} className="chip text-warn">{e.label}: {e.why}</span>)}</div>
      )}
      {s.accounts.length > 0 && (
        <Table cols={["Account", ["Return", "right"], ["Joins the portfolio", "right"], ["Then, today's money", "right"]]}>
          {s.accounts.map((a: any) => (
            <tr key={a.label}>
              <td className="px-3 py-1.5 text-ink">{a.label}</td>
              <td className={num} title={Object.entries(a.returns).map(([c, w]: any) => `${c.replace(/_/g, " ")} ${Math.round(w * 100)}%`).join(", ")}>{(a.expected_return * 100).toFixed(1)}%</td>
              <td className={num}>{a.release_in_years == null ? "not in the plan" : a.release_in_years === 0 ? "now" : `in ${a.release_in_years} years`}</td>
              <td className={num}>{money(a.at_release_today)}</td>
            </tr>
          ))}
        </Table>
      )}
    </Section>
  );
}

function NpsExit({ result }: { result: any }) {
  const rows: any[] = result.nps_exit ?? [];
  if (!rows.length) return null;
  return (
    <Section title="NPS at exit" testid="stack-nps"
      chips={<>
        <span className="chip tabular-nums text-ink-muted">{Math.round(rows[0].lump_share * 100)}% lump sum · {Math.round(rows[0].annuity_share * 100)}% annuity</span>
        <span className="chip text-ink-muted">lump sum exempt · assumed</span>
        <span className="chip text-ink-muted">annuity income taxed at slab · assumed</span>
        <span className="chip text-ink-muted">tax not worked out</span>
      </>}>
      <Table cols={["Member", ["Today · lump / annuity", "right"], ["At exit", "right"], ["Lump sum", "right"], ["Buys annuity", "right"], ["Annuity a year", "right"]]}>
        {rows.map((n) => (
          <tr key={n.label}>
            <td className="px-3 py-1.5 text-ink">{n.person}{n.exit_age != null && <span className="ml-1 text-xs text-ink-muted">at {n.exit_age}</span>}</td>
            <td className={num}>{money(n.lump_today)} / {money(n.annuity_today)}</td>
            <td className={num} title="Median of the same paths, in the money of that year">{money(n.corpus_at_exit)}</td>
            <td className={num}>{money(n.lump_at_exit)}</td>
            <td className={num}>{money(n.annuity_purchase_at_exit)}</td>
            <td className={num}>
              {n.annuity_a_year != null
                ? <span title={`${money(n.annuity_a_year_today_money)} in today's money`}>{money(n.annuity_a_year)} <span className="text-ink-muted">at {n.annuity_rate}% {n.annuity_rate_basis === "what_if" ? "what-if" : "quote"}</span></span>
                : <span className="chip text-warn" title={n.annuity_not_assessed ?? undefined}>not assessed</span>}
            </td>
          </tr>
        ))}
      </Table>
    </Section>
  );
}

function Floor({ result }: { result: any }) {
  const f = result.floor;
  return (
    <Section title="Income floor against needs" testid="stack-floor"
      chips={<>
        <span className="chip text-ink-muted">today&rsquo;s money · before tax</span>
        {f.status === "assessed"
          ? <span className={`chip tabular-nums ${f.coverage >= 1 ? "text-ok" : "text-warn"}`}>{pc(f.coverage)} of {money(f.needs)} a year · short {money(f.short_by)}</span>
          : <span className="chip text-warn">not assessed · {f.why}</span>}
        {f.other_spending != null && <span className="chip tabular-nums text-ink-muted">other spending {money(f.other_spending)} a year</span>}
        {f.income_planned_for != null && <span className="chip tabular-nums text-ink-muted">plan&rsquo;s income {money(f.income_planned_for)} a year</span>}
      </>}>
      <Table cols={["Guaranteed income", ["Stated a year", "right"], ["At retirement, today's money", "right"]]}>
        {f.sources.map((s: any, k: number) => (
          <tr key={`${s.source}-${k}`}><td className="px-3 py-1.5 text-ink">{s.source}{s.person && <span className="ml-1 text-xs text-ink-muted">{s.person}</span>}</td><td className={num}>{money(s.stated)}</td><td className={num}>{money(s.a_year)}</td></tr>
        ))}
        {f.starting_later.map((s: any, k: number) => (
          <tr key={`later-${s.source}-${k}`}><td className="px-3 py-1.5 text-ink">{s.source}<span className="ml-1 text-xs text-ink-muted">{s.person}</span></td><td className={num}>{money(s.stated)}</td><td className={`${num} text-ink-muted`}>starts in {s.starts_in_years} years</td></tr>
        ))}
        {f.not_counted.map((s: any) => (
          <tr key={s.source}><td className="px-3 py-1.5 text-ink">{s.source}</td><td className={num}>—</td><td className="px-3 py-1.5 text-right"><span className="chip text-warn">not counted · {s.why}</span></td></tr>
        ))}
        {!f.sources.length && !f.starting_later.length && !f.not_counted.length && <tr><td className={muted} colSpan={3}>none on file</td></tr>}
      </Table>
    </Section>
  );
}

function Liquidity({ result }: { result: any }) {
  const l = result.liquidity;
  return (
    <Section title="Liquid defence" testid="stack-liquid"
      chips={<>
        {l.status === "assessed"
          ? <span className={`chip tabular-nums ${l.years >= l.convention_years[0] ? "text-ok" : "text-warn"}`}>{l.years} years · {money(l.liquid)} against {money(l.yearly_need)} a year</span>
          : <span className="chip text-warn">not assessed · {l.why}</span>}
        <span className="chip text-ink-muted">planning convention {l.convention_years[0]} to {l.convention_years[1]} years</span>
        <span className="chip text-ink-muted" title={l.not_counted.join("; ")}>locked and invested money not counted</span>
      </>}>
      <div className="flex flex-wrap gap-1.5 px-3 py-2">{l.parts.map((p: any) => <span key={p.source} className="chip tabular-nums text-ink-muted">{p.source} {money(p.amount)}</span>)}</div>
    </Section>
  );
}

function Drawing({ result }: { result: any }) {
  const w = result.withdrawals;
  const rows: any[] = w.years ?? [];
  const shown = rows.length > 11 ? [...rows.slice(0, 10), rows[rows.length - 1]] : rows;
  return (
    <Section title="Drawing, year by year" testid="stack-drawing"
      chips={<>
        <span className="chip text-ink-muted">illustration · {(w.return * 100).toFixed(1)}% a year, steady</span>
        <span className="chip text-ink-muted">{w.basis === "monthly_amount_stated" ? `${money(w.monthly_stated_today)} a month stated, today's money` : "the plan's income less guaranteed income"}</span>
        {w.first_monthly != null && <span className="chip tabular-nums text-ink">first {money(w.first_monthly)} a month</span>}
        {w.runs_out_at != null ? <span className="chip tabular-nums text-warn">short from {w.runs_out_at}</span> : <span className="chip text-ok">lasts the plan</span>}
      </>}>
      <Table cols={["Age", ["At the start", "right"], ["Joins", "right"], ["Annuity", "right"], ["Drawn a month", "right"], ["Return", "right"], ["At the end", "right"]]}>
        {shown.map((r: any) => (
          <tr key={r.year}>
            <td className="px-3 py-1.5 font-mono text-xs tabular-nums text-ink">{r.age}</td>
            <td className={num}>{money(r.opening)}</td><td className={num}>{r.released ? money(r.released) : "—"}</td>
            <td className={num}>{r.annuity ? money(r.annuity) : "—"}</td><td className={num}>{money(r.monthly)}</td>
            <td className={num}>{money(r.return)}</td><td className={num}>{money(r.closing)}</td>
          </tr>
        ))}
      </Table>
      <div className="flex flex-wrap gap-1.5 px-3 pb-2">
        <span className="chip text-ink-muted">money of each year</span>
        {w.not_modelled.map((n: string) => <span key={n} className="chip text-ink-muted">not modelled · {n}</span>)}
      </div>
    </Section>
  );
}

export function RetirementStackBody({ result }: { result: any }) {
  if (result.status !== "on_file") {
    return (
      <div className="flex flex-wrap gap-1.5 border-t border-border-soft px-3 py-2.5" data-testid="stack-empty">
        <span className="chip text-ink-muted">{result.applies === false ? "for an Indian household" : "no EPF, PPF, NPS, SCSS or EPS on file · does not mean none"}</span>
        {(result.not_in_stack ?? []).map((n: any) => <span key={n.item_id} className="chip text-warn">not counted · {n.name} {money(n.amount)}</span>)}
      </div>
    );
  }
  return (
    <>
      <WithAndWithout result={result} />
      <Inputs result={result} />
      <NpsExit result={result} />
      <Floor result={result} />
      <Liquidity result={result} />
      <Drawing result={result} />
      <Section title="Rules read" testid="stack-rules">
        <div className="divide-y divide-border-soft py-1">
          {(result.figures ?? []).map((f: any) => (
            <div key={f.key} className="flex flex-wrap items-center gap-1.5 px-3 py-1" title={f.source}>
              <span className="mr-auto text-xs text-ink-muted">{f.label}</span>
              <span className="chip text-warn">assumed</span>
              <CounselChip counsel={f.counsel} />
            </div>
          ))}
        </div>
        <div className="flex flex-wrap gap-1.5 px-3 pb-2">{(result.not_modelled ?? []).map((n: string) => <span key={n} className="chip text-ink-muted">not modelled · {n}</span>)}</div>
      </Section>
    </>
  );
}

export function RetirementStackCard({ result }: { result: any }) {
  return (
    <div className="overflow-hidden rounded-lg border border-border bg-surface" data-testid="retirement-stack-card">
      <div className="flex flex-wrap items-center gap-1.5 px-3 py-2">
        <div className="mr-1 text-sm font-medium text-ink">Retirement stack · {result.household}</div>
        {result.record_id && <span className="chip text-ok">recorded</span>}
        <RetirementStackChips result={result} />
      </div>
      <RetirementStackBody result={result} />
    </div>
  );
}
