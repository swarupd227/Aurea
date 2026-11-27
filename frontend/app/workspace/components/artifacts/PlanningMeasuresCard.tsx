import { money } from "@/lib/format";

/** B-400: India's planning measures. The construction-first order (protection, emergency fund, will and nominations,
 * investing) with each measure's figures, for one household or the whole book. Figures, tables and chips only. */

const pc = (v: number | null | undefined) => (v == null ? "—" : `${Math.round(v * 100)}%`);
const TONE: Record<string, string> = { done: "text-ok", short: "text-warn", building: "text-info", not_started: "text-warn", not_assessed: "text-ink-muted" };
const WILL: Record<string, string> = { current: "current", not_checked: "on file · never checked", to_check: "on file · to check", not_on_file: "not on file" };
const POSITION: Record<string, string> = { own: "own cover", employer_only: "employer only", top_up_only: "top-up only", none_on_file: "none on file" };
const KIND: Record<string, string> = { account: "account", outside: "held outside", held_away: "held elsewhere", policy: "policy" };
const th = "px-3 py-1.5 font-medium";
const num = "px-3 py-1.5 text-right font-mono text-xs tabular-nums text-ink";
const muted = "px-3 py-1.5 text-xs text-ink-muted";
const head = "text-left text-[10px] uppercase tracking-wide text-ink-muted";

function Section({ title, chips, children, testid }: { title: string; chips?: React.ReactNode; children?: React.ReactNode; testid: string }) {
  return (
    <div className="border-t border-border-soft" data-testid={testid}>
      <div className="flex flex-wrap items-center gap-1.5 px-3 py-2">
        <div className="mr-1 text-xs font-medium uppercase tracking-wide text-ink-muted">{title}</div>
        {chips}
      </div>
      {children}
    </div>
  );
}

function Table({ cols, children }: { cols: (string | [string, "right"])[]; children: React.ReactNode }) {
  return (
    <div className="overflow-x-auto pb-1">
      <table className="w-full text-sm">
        <thead><tr className={head}>{cols.map((c) => (Array.isArray(c) ? <th key={c[0]} className={`${th} text-right`}>{c[0]}</th> : <th key={c} className={th}>{c}</th>))}</tr></thead>
        <tbody className="divide-y divide-border-soft">{children}</tbody>
      </table>
    </div>
  );
}

function Order({ order, next }: { order: any[]; next: string | null }) {
  return (
    <ol className="flex flex-wrap items-stretch gap-1.5 border-t border-border-soft px-3 py-2" data-testid="measures-order">
      {order.map((s) => (
        <li key={s.key} className={`flex items-center gap-1.5 rounded-md border px-2 py-1 ${s.key === next ? "border-border" : "border-border-soft"}`}>
          <span className="font-mono text-xs tabular-nums text-ink-muted">{s.step}</span>
          <span className="text-xs text-ink">{s.label}</span>
          <span className={`chip ${TONE[s.state] ?? "text-ink-muted"}`}>{s.chip}</span>
          {s.key === next && <span className="chip text-ink">next</span>}
        </li>
      ))}
    </ol>
  );
}

function Household({ result }: { result: any }) {
  const { protection: p, emergency_fund: e, documents: d, sip_health: s } = result;
  return (
    <>
      <Order order={result.order} next={result.next} />
      <Section title="Protection" testid="measures-protection"
        chips={<>
          <span className="chip tabular-nums text-ink-muted">term met {p.term.met} of {p.term.assessed} assessed</span>
          {p.term.gap > 0 && <span className="chip tabular-nums text-warn">term gap {money(p.term.gap)}</span>}
          {p.term.not_assessed > 0 && <span className="chip text-ink-muted">{p.term.not_assessed} not assessed</span>}
          <span className="chip tabular-nums text-ink-muted">own health cover {p.health.own} of {p.health.members}</span>
        </>}>
        <Table cols={["Person", ["Need", "right"], ["Cover", "right"], ["Gap", "right"]]}>
          {p.term.rows.map((r: any) => (
            <tr key={r.person}>
              <td className="px-3 py-1.5 text-ink">{r.person}{r.status === "not_assessed" && <span className="ml-1 chip text-ink-muted">not assessed</span>}</td>
              <td className={num}>{money(r.need)}</td><td className={num}>{money(r.cover)}</td><td className={num}>{r.gap ? money(r.gap) : "—"}</td>
            </tr>
          ))}
        </Table>
        <div className="flex flex-wrap gap-1.5 px-3 pb-2">
          {p.health.rows.map((r: any) => <span key={r.person} className={`chip ${r.position === "own" ? "text-ink-muted" : "text-warn"}`}>{r.person} · {POSITION[r.position] ?? r.position}</span>)}
        </div>
      </Section>
      <Section title="Emergency fund" testid="measures-emergency"
        chips={e.target == null
          ? <span className="chip text-ink-muted">none on file · does not mean none</span>
          : <>
              <span className={`chip tabular-nums ${e.state === "done" ? "text-ok" : "text-info"}`}>{pc(e.share)} · {money(e.held)} of {money(e.target)}</span>
              {e.gap > 0 && <span className="chip tabular-nums text-warn">to go {money(e.gap)}</span>}
              {e.months_covered != null
                ? <span className="chip tabular-nums text-ink-muted">{e.months_covered} of {e.target_months} months of essential spending</span>
                : <span className="chip text-ink-muted">months not assessed · no essential spending on file</span>}
              {e.complete_by && <span className="chip tabular-nums text-ink-muted">by {e.complete_by}</span>}
              <span className="chip tabular-nums text-ink-muted" title={e.document}>statement {e.statement_date}</span>
              {e.held_in && <span className="chip text-ink-muted">{e.held_in}</span>}
            </>} />
      <Section title="Wills and nominations" testid="measures-documents"
        chips={<>
          <span className={`chip tabular-nums ${d.wills.share === 1 ? "text-ok" : "text-warn"}`}>current wills {d.wills.current} of {d.wills.adults}</span>
          <span className={`chip tabular-nums ${d.nominations.share === 1 ? "text-ok" : "text-warn"}`}>
            {d.nominations.assets ? `nominations ${d.nominations.covered} of ${d.nominations.assets} · ${pc(d.nominations.share)}` : "no asset that takes a nomination on file"}
          </span>
        </>}>
        <div className="flex flex-wrap gap-1.5 px-3 pb-2">
          {d.wills.people.map((w: any) => <span key={w.person_id} className={`chip ${w.state === "current" ? "text-ink-muted" : "text-warn"}`}>{w.person} · will {WILL[w.state]}{w.signed_on ? ` · ${w.signed_on}` : ""}</span>)}
        </div>
        {d.nominations.items.length > 0 && (
          <Table cols={["Asset", "Holder", "Nomination"]}>
            {d.nominations.items.map((i: any) => (
              <tr key={i.key}>
                <td className="px-3 py-1.5 text-ink">{i.label}<span className="ml-1 text-xs text-ink-muted">{KIND[i.category] ?? i.category}</span></td>
                <td className={muted}>{i.person}</td>
                <td className="px-3 py-1.5">
                  <span className={`chip ${i.covered ? "text-ink-muted" : "text-warn"}`}>{!i.covered ? "not on file" : i.how === "opted_out" ? "opted out" : "nominee"}</span>
                </td>
              </tr>
            ))}
          </Table>
        )}
      </Section>
      <Section title="SIPs against plan" testid="measures-sips"
        chips={<>
          {s.planned_monthly != null
            ? <span className={`chip tabular-nums ${s.share >= 1 ? "text-ok" : "text-warn"}`}>{money(s.registered_monthly)} of {money(s.planned_monthly)} a month · {pc(s.share)}</span>
            : <span className="chip tabular-nums text-ink-muted">{money(s.registered_monthly)} a month · no planned saving on file</span>}
          <span className="chip tabular-nums text-ink-muted">step-up in the mandate {s.step_up_registered} of {s.active}</span>
          <span className="chip tabular-nums text-ink-muted">mandate in force {s.mandate_in_force} of {s.active}</span>
          <span className={`chip tabular-nums ${s.failures.open ? "text-warn" : "text-ink-muted"}`}>failures {s.failures.ever} · put right {s.failures.recovered} · open {s.failures.open}</span>
          <span className={`chip tabular-nums ${s.missed_last_year ? "text-warn" : "text-ink-muted"}`}>missed in a year {s.missed_last_year}</span>
        </>}>
        {s.goals.length > 0 && (
          <Table cols={["Goal", ["Planned a month", "right"], ["Registered", "right"], ["Share", "right"]]}>
            {s.goals.map((g: any) => (
              <tr key={g.goal_id}><td className="px-3 py-1.5 text-ink">{g.goal}</td><td className={num}>{money(g.planned_monthly)}</td><td className={num}>{money(g.registered_monthly)}</td><td className={num}>{pc(g.share)}</td></tr>
            ))}
          </Table>
        )}
        {s.sips.length > 0 && (
          <Table cols={["SIP", ["A month", "right"], "Step-up", "Mandate"]}>
            {s.sips.map((i: any) => (
              <tr key={i.id}>
                <td className="px-3 py-1.5 text-ink">{i.fund}<span className="ml-1 text-xs text-ink-muted">{i.account}</span></td>
                <td className={num}>{money(i.amount)}</td><td className={muted}>{i.step_up ?? "—"}</td><td className={muted}>{i.mandate ?? "—"}</td>
              </tr>
            ))}
          </Table>
        )}
      </Section>
    </>
  );
}

const SHARES: [string, string][] = [
  ["protection", "Protection in place"], ["emergency_fund", "Emergency fund complete"], ["wills", "Current will, every adult"],
  ["nominations", "Nominations on assets that take one"], ["will_and_nominations", "Will and nominations both"], ["step_up", "SIPs with a step-up"],
  ["mandate_recovery", "Failed mandates put right"],
];

function Firm({ result }: { result: any }) {
  const m = result.measures;
  const sip = m.sip_against_plan;
  return (
    <>
      <Section title="Across the book" testid="measures-firm">
        <Table cols={["Measure", ["Met", "right"], ["Of", "right"], ["Share", "right"]]}>
          {SHARES.map(([key, label]) => (
            <tr key={key}><td className="px-3 py-1.5 text-ink">{label}</td><td className={num}>{m[key].met}</td><td className={num}>{m[key].of}</td><td className={num}>{pc(m[key].share)}</td></tr>
          ))}
          <tr><td className="px-3 py-1.5 text-ink">SIPs against planned saving</td><td className={num}>{money(sip.registered_monthly)}</td><td className={num}>{money(sip.planned_monthly)}</td><td className={num}>{pc(sip.share)}</td></tr>
        </Table>
        <div className="flex flex-wrap gap-1.5 px-3 pb-2">
          {m.protection.term_gap > 0 && <span className="chip tabular-nums text-warn">term gap {money(m.protection.term_gap)}</span>}
          <span className="chip tabular-nums text-ink-muted">emergency fund building {m.emergency_fund.building} · none on file {m.emergency_fund.not_on_file}</span>
        </div>
      </Section>
      <Section title="By household" testid="measures-households">
        <Table cols={["Household", ...result.steps.map((s: any) => s.label), ["SIPs against plan", "right"]]}>
          {result.households.map((h: any) => (
            <tr key={h.household_id}>
              <td className="px-3 py-1.5 text-ink">{h.household}</td>
              {h.order.map((s: any) => <td key={s.key} className="px-3 py-1.5"><span className={`chip ${TONE[s.state] ?? "text-ink-muted"}`}>{s.chip}</span></td>)}
              <td className={num}>{pc(h.sip_share)}</td>
            </tr>
          ))}
        </Table>
      </Section>
    </>
  );
}

export function PlanningMeasuresBody({ result }: { result: any }) {
  if (result.applies === false) {
    return <div className="border-t border-border-soft px-3 py-2.5" data-testid="measures-empty"><span className="chip text-ink-muted">for an Indian household</span></div>;
  }
  return result.scope === "firm" ? <Firm result={result} /> : <Household result={result} />;
}

export function PlanningMeasuresChips({ result }: { result: any }) {
  return (
    <>
      <span className="chip text-ink-muted">from what is on file</span>
      {result.as_of && <span className="chip tabular-nums text-ink-muted">{result.as_of}</span>}
      {result.in_place != null && <span className="chip tabular-nums text-ink">{result.in_place} of {result.order.length} in place</span>}
    </>
  );
}

export function PlanningMeasuresCard({ result }: { result: any }) {
  return (
    <div className="overflow-hidden rounded-lg border border-border bg-surface" data-testid="planning-measures-card">
      <div className="flex flex-wrap items-center gap-1.5 px-3 py-2">
        <div className="mr-1 text-sm font-medium text-ink">Planning measures{result.household ? ` · ${result.household}` : ""}</div>
        <PlanningMeasuresChips result={result} />
      </div>
      <PlanningMeasuresBody result={result} />
    </div>
  );
}
