import { money } from "./ProtectionCard";

/** B-397: who is covered by what health insurance, and an endowment or unit-linked policy laid out as surrender,
 * paid-up and continue. Figures, tables and chips only. */

export interface HealthMember {
  person_id: string | null;
  name: string;
  relationship: string | null;
  group: "family" | "parent";
  on_record: boolean;
  position: "own" | "employer_only" | "top_up_only" | "none_on_file";
  own_cover: number;
  employer_cover: number;
  top_ups_short: { policy_id: string; sum_insured: number; unpaid_below: number }[];
  renewal_passed: number;
  need: { status: "gap" | "no_gap" | "not_assessed"; figure?: number; gap?: number; gap_with_employer?: number; missing?: string[] };
  limits: string[];
}

export interface HealthPolicyRow {
  id: string;
  kind: string;
  kind_label: string;
  provider: string;
  sum_insured: number;
  deductible: number | null;
  members: { person_id: string | null; name: string; relationship: string }[];
  status: "in_force" | "renewal_passed" | "ended";
  renewal_on: string | null;
  days_to_renewal: number | null;
  not_counted: string | null;
  limits: string[];
  source_document: string;
  ended_reason?: string | null;
}

export interface HealthFigure { family_cover: number; parent_cover: number | null; source: string; set_by: string }

export interface HealthHousehold {
  applies: boolean;
  household_id: string;
  household: string;
  why?: string;
  figure: HealthFigure | null;
  members: HealthMember[];
  policies: HealthPolicyRow[];
  summary: { employer_only: number; none_on_file: number; gaps: number; renewals_due: number; renewals_passed: number };
  not_modelled: string[];
}

export const POSITION_TEXT: Record<HealthMember["position"], string> = {
  own: "own policy", employer_only: "employer only", top_up_only: "top-up only", none_on_file: "none on file",
};
const POSITION_TONE: Record<HealthMember["position"], string> = {
  own: "text-ok", employer_only: "text-warn", top_up_only: "text-warn", none_on_file: "text-warn",
};

const words = (keys: string[] | undefined) => (keys ?? []).join(" · ").replace(/_/g, " ");

export function FigureChip({ figure }: { figure: HealthFigure | null }) {
  if (!figure) return <span className="chip text-warn" data-testid="health-figure">health cover figure not set</span>;
  return (
    <span className="chip text-ink-muted" title={`${figure.source} · set by ${figure.set_by}`} data-testid="health-figure">
      firm figure · family {money(figure.family_cover)} · parent {figure.parent_cover ? money(figure.parent_cover) : "not stated"}
    </span>
  );
}

export function RenewalChip({ p }: { p: HealthPolicyRow }) {
  if (p.status === "ended") return <span className="chip text-ink-muted" title={p.ended_reason ?? undefined}>ended</span>;
  if (p.status === "renewal_passed") return <span className="chip text-crit">renewal passed {p.renewal_on}</span>;
  if (!p.renewal_on) return <span className="chip text-ink-muted">renewal date not on file</span>;
  const soon = p.days_to_renewal != null && p.days_to_renewal <= 60;
  return <span className={`chip ${soon ? "text-warn" : "text-ink-muted"}`}>renews {p.renewal_on}{soon ? ` · ${p.days_to_renewal}d` : ""}</span>;
}

function Need({ m }: { m: HealthMember }) {
  const n = m.need;
  if (n.status === "not_assessed") return <span className="chip text-warn">not assessed</span>;
  if (n.status === "no_gap") return <span className="chip text-ok">meets {money(n.figure)}</span>;
  return (
    <>
      <span className="font-mono tabular-nums text-crit">{money(n.gap)}</span>
      {n.gap_with_employer !== n.gap && <div className="mt-0.5"><span className="chip tabular-nums text-ink-muted">with employer {money(n.gap_with_employer)}</span></div>}
    </>
  );
}

export function MemberTable({ rows, showLimits = false }: { rows: { household?: string; m: HealthMember }[]; showLimits?: boolean }) {
  return (
    <div className="overflow-x-auto">
      <table className="w-full text-sm" data-testid="health-members">
        <thead>
          <tr className="text-left text-[10px] uppercase tracking-wide text-ink-muted">
            <th className="px-3 py-1.5 font-medium">Insured</th>
            <th className="px-3 py-1.5 font-medium">Covered by</th>
            <th className="px-3 py-1.5 text-right font-medium">Own cover</th>
            <th className="px-3 py-1.5 text-right font-medium">Employer</th>
            <th className="px-3 py-1.5 text-right font-medium">Short of firm figure</th>
          </tr>
        </thead>
        <tbody className="divide-y divide-border-soft">
          {rows.map(({ household, m }) => (
            <tr key={`${household ?? ""}${m.person_id ?? m.name}`} className="align-top">
              <td className="px-3 py-1.5">
                <div className="text-ink">{m.name}</div>
                <div className="text-xs text-ink-muted">{[household, m.relationship].filter(Boolean).join(" · ")}</div>
                {showLimits && m.limits.length > 0 && (
                  <div className="mt-1 flex flex-wrap gap-1">{m.limits.map((l) => <span key={l} className="chip text-ink-muted">{l}</span>)}</div>
                )}
              </td>
              <td className="px-3 py-1.5">
                <span className={`chip ${POSITION_TONE[m.position]}`} data-testid="health-position">{POSITION_TEXT[m.position]}</span>
                {m.top_ups_short.map((t) => (
                  <div key={t.policy_id} className="mt-0.5">
                    <span className="chip tabular-nums text-warn">top-up {money(t.sum_insured)} not counted · {money(t.unpaid_below)} below it</span>
                  </div>
                ))}
                {m.renewal_passed > 0 && <div className="mt-0.5"><span className="chip text-crit">renewal passed</span></div>}
              </td>
              <td className="px-3 py-1.5 text-right font-mono tabular-nums text-ink">{m.own_cover ? money(m.own_cover) : "—"}</td>
              <td className="px-3 py-1.5 text-right font-mono tabular-nums text-ink-muted">{m.employer_cover ? money(m.employer_cover) : "—"}</td>
              <td className="px-3 py-1.5 text-right"><Need m={m} /></td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

const SHOWN = 10;

export function HealthCoverCard({ result }: { result: any }) {
  const frame = "overflow-hidden rounded-lg border border-border bg-surface";
  const head = "flex flex-wrap items-center justify-between gap-2 border-b border-border-soft px-3 py-2";
  const foot = "flex flex-wrap gap-1.5 border-t border-border-soft px-3 py-2";
  if (Array.isArray(result?.households)) {
    const rows = (result.households as HealthHousehold[]).flatMap((h) => h.members.map((m) => ({ household: h.household, m })));
    const short = rows.filter((r) => r.m.position !== "own" || r.m.need.status === "gap");
    const shown = (short.length ? short : rows).slice(0, SHOWN);
    const s = result.summary ?? {};
    return (
      <div className={frame} data-testid="health-cover-card">
        <div className={head}>
          <div className="text-sm font-medium text-ink">Health cover</div>
          <div className="flex flex-wrap gap-1.5">
            <span className="chip text-warn">{s.people_employer_only ?? 0} employer only</span>
            <span className="chip text-warn">{s.people_none_on_file ?? 0} none on file</span>
            <span className="chip text-crit">{s.with_gap ?? 0} household(s) short</span>
            <span className="chip text-ink-muted">{s.renewals_due ?? 0} renewal(s) due</span>
          </div>
        </div>
        <MemberTable rows={shown} />
        <div className={foot}>
          {rows.length > shown.length && <span className="chip text-ink-muted">+{rows.length - shown.length} more</span>}
          <FigureChip figure={result.figure} />
          <span className="chip text-ink-muted">none on file is not no cover</span>
        </div>
      </div>
    );
  }
  if (Array.isArray(result?.members) && typeof result?.members?.[0] !== "string") {
    const h = result as HealthHousehold;
    return (
      <div className={frame} data-testid="health-cover-card">
        <div className={head}>
          <div className="text-sm font-medium text-ink">Health cover · {h.household}</div>
          <FigureChip figure={h.figure} />
        </div>
        <MemberTable rows={h.members.map((m) => ({ m }))} showLimits />
        <div className={foot}>
          {h.policies.filter((p) => p.status !== "ended").map((p) => (
            <span key={p.id} className="inline-flex flex-wrap items-center gap-1">
              <span className={`chip tabular-nums ${p.not_counted ? "text-warn" : "text-ink-muted"}`} title={[p.not_counted, p.source_document].filter(Boolean).join(" · ")}>
                {p.provider} · {p.kind_label} · {money(p.sum_insured)}{p.deductible != null ? ` above ${money(p.deductible)}` : ""}
              </span>
              <RenewalChip p={p} />
            </span>
          ))}
          <span className="chip text-ink-muted">not modelled: {words(h.not_modelled)}</span>
        </div>
      </div>
    );
  }
  if (result?.applies === false) {
    return <div className="rounded-lg border border-border bg-surface px-3 py-2"><span className="chip text-ink-muted">health cover · Indian households only</span></div>;
  }
  return (
    <div className="flex flex-wrap items-center gap-1.5 rounded-lg border border-border bg-surface px-3 py-2" data-testid="health-record-card">
      {result?.family_cover != null ? (
        <>
          <span className="chip text-ink">health cover figure</span>
          <span className="chip tabular-nums text-ink-muted">family {money(result.family_cover)}</span>
          <span className="chip tabular-nums text-ink-muted">parent {result.parent_cover ? money(result.parent_cover) : "not stated"}</span>
          <span className="chip text-ink-muted">{result.source}</span>
        </>
      ) : (
        <>
          <span className="chip text-ink">{result?.household}</span>
          <span className="chip text-ink-muted">{result?.kind_label} · {result?.provider}</span>
          {result?.sum_insured != null && <span className="chip tabular-nums text-ink-muted">{money(result.sum_insured)}{result.deductible ? ` above ${money(result.deductible)}` : ""}</span>}
          {Array.isArray(result?.members) && <span className="chip text-ink-muted">{result.members.join(", ")}</span>}
          <span className="chip text-ink-muted">{result?.status === "ended" ? "ended" : "on file"}</span>
          {result?.replaced && <span className="chip text-ink-muted">replaces the earlier schedule</span>}
        </>
      )}
    </div>
  );
}

export interface PolicyOption {
  key: "surrender" | "paid_up" | "continue";
  label: string;
  cash_now: number | null;
  premiums_still_to_pay: number | null;
  stated_value: number | null;
  stated_value_is: string | null;
  life_cover: number | null;
  against_premiums_paid: number | null;
}

export interface PolicyValue {
  policy_id: string;
  person: string;
  household?: string;
  household_id?: string;
  provider: string;
  product_label: string;
  sum_assured: number | null;
  status?: "in_force" | "ended";
  statement: { statement_date: string; premiums_paid: number; surrender_value: number | null; paid_up_value: number | null; fund_value: number | null; annual_premium: number | null; maturity_on: string | null; charges_stated: string | null; source_document: string };
  earlier_statements?: number;
  options: PolicyOption[];
  continuing_return: { rate: number | null; missing: string[]; basis: string | null; assumes: string } | null;
  lock_in: { status: "locked" | "passed" | "not_assessed"; until?: string; assumed: boolean } | null;
}

const ROWS: { key: keyof PolicyOption; label: string }[] = [
  { key: "cash_now", label: "Cash now" },
  { key: "premiums_still_to_pay", label: "Premiums still to pay" },
  { key: "stated_value", label: "Stated value" },
  { key: "life_cover", label: "Life cover" },
];

export function PolicyLayout({ p, household = false }: { p: PolicyValue; household?: boolean }) {
  const s = p.statement;
  const against = p.options.find((o) => o.key === "surrender")?.against_premiums_paid;
  return (
    <div className="px-3 py-2" data-testid="policy-values-policy">
      <div className="flex flex-wrap items-center gap-1.5">
        <span className="text-sm text-ink">{p.person}</span>
        {household && p.household && <span className="text-xs text-ink-muted">{p.household}</span>}
        <span className="chip text-ink-muted" title={s.source_document}>{p.product_label} · {p.provider}</span>
        <span className="chip text-ink-muted">statement {s.statement_date}</span>
        <span className="chip tabular-nums text-ink-muted">premiums paid {money(s.premiums_paid)}</span>
        {s.fund_value != null && <span className="chip tabular-nums text-ink-muted">fund {money(s.fund_value)}</span>}
        {s.maturity_on && <span className="chip text-ink-muted">matures {s.maturity_on}</span>}
        {s.charges_stated && <span className="chip text-ink-muted" title={s.charges_stated}>charges stated</span>}
        {p.status === "ended" && <span className="chip text-ink-muted">ended</span>}
      </div>
      {p.options.length > 0 && (
        <div className="mt-1.5 overflow-x-auto">
          <table className="w-full text-sm" data-testid="policy-values-options">
            <thead>
              <tr className="text-[10px] uppercase tracking-wide text-ink-muted">
                <th className="py-1 text-left font-medium" />
                {p.options.map((o) => <th key={o.key} className="px-2 py-1 text-right font-medium">{o.label}</th>)}
              </tr>
            </thead>
            <tbody className="divide-y divide-border-soft">
              {ROWS.map((r) => (
                <tr key={r.key}>
                  <td className="py-1 text-xs text-ink-muted">{r.label}</td>
                  {p.options.map((o) => (
                    <td key={o.key} className="px-2 py-1 text-right font-mono tabular-nums text-ink">
                      {o[r.key] == null ? <span className="text-xs text-ink-muted">not on file</span> : money(o[r.key] as number)}
                      {r.key === "stated_value" && o.stated_value != null && <div className="font-sans text-[10px] text-ink-muted">{o.stated_value_is}</div>}
                    </td>
                  ))}
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
      {p.status !== "ended" && (
        <div className="mt-1.5 flex flex-wrap gap-1.5">
          {against != null && <span className="chip tabular-nums text-ink-muted">surrender less premiums {against < 0 ? "−" : "+"}{money(Math.abs(against))}</span>}
          {p.continuing_return?.rate != null ? (
            <span className="chip tabular-nums text-ink" title={p.continuing_return.assumes} data-testid="policy-values-return">
              continuing implies {(p.continuing_return.rate * 100).toFixed(1)}% a year · {p.continuing_return.basis} · before tax · cover left out
            </span>
          ) : (
            <span className="chip text-warn" data-testid="policy-values-return">implied return not assessed · {words(p.continuing_return?.missing)} not on file</span>
          )}
          {p.lock_in?.status === "locked" && <span className="chip text-warn" data-testid="policy-values-lock">locked to {p.lock_in.until} · assumed rule</span>}
          {p.lock_in?.status === "passed" && <span className="chip text-ink-muted">lock-in passed {p.lock_in.until} · assumed rule</span>}
          {p.lock_in?.status === "not_assessed" && <span className="chip text-warn">lock-in not assessed · start date not on file</span>}
        </div>
      )}
    </div>
  );
}

export function PolicyValuesCard({ result }: { result: any }) {
  // A recorded statement is one policy; a read is a household's or the book's.
  const policies: PolicyValue[] = Array.isArray(result?.policies) ? result.policies : result?.statement ? [result as PolicyValue] : [];
  const shown = policies.slice(0, 4);
  return (
    <div className="overflow-hidden rounded-lg border border-border bg-surface" data-testid="policy-values-card">
      <div className="flex flex-wrap items-center justify-between gap-2 border-b border-border-soft px-3 py-2">
        <div className="text-sm font-medium text-ink">Surrender · paid-up · continue{result?.household && !result?.statement ? ` · ${result.household}` : ""}</div>
        <span className="chip text-ink-muted">figures only · no recommendation</span>
      </div>
      {shown.length === 0
        ? <div className="px-3 py-3 font-mono text-xs text-ink-muted">no statement on file</div>
        : <div className="divide-y divide-border-soft">{shown.map((p) => <PolicyLayout key={p.policy_id} p={p} household={!result?.household} />)}</div>}
      <div className="flex flex-wrap gap-1.5 border-t border-border-soft px-3 py-2">
        {policies.length > shown.length && <span className="chip text-ink-muted">+{policies.length - shown.length} more</span>}
        {Array.isArray(result?.not_modelled) && <span className="chip text-ink-muted">not modelled: {words(result.not_modelled)}</span>}
      </div>
    </div>
  );
}
