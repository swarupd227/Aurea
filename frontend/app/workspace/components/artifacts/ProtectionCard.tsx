import { firmLocale } from "@/lib/format";

export interface ProtectionScenario {
  status: "gap" | "no_gap" | "not_assessed" | "past_retirement" | "not_yet";
  missing?: string[];
  need?: number;
  cover?: number;
  assets?: number;
  gap?: number;
  gap_before_assets?: number;
}

export interface ProtectionPerson {
  person_id: string;
  name: string;
  age: number | null;
  income: number | null;
  retirement_age: number;
  years_to_retirement: number | null;
  other_income_missing: boolean;
  death: ProtectionScenario;
  disability: ProtectionScenario;
  care: ProtectionScenario;
}

export interface ProtectionPolicy {
  id: string;
  person: string | null;
  kind: string;
  provider: string;
  sum_insured: number | null;
  annual_benefit: number | null;
  benefit_to_age: number | null;
  benefit_years: number | null;
  currency: string;
  expires_on: string | null;
  status: string;
  counted: boolean;
  not_counted?: string | null;
  cover_basis?: string;
  rider_of?: string | null;
  group_cover?: boolean;
  payee?: { to: string | null; relationship?: string | null; means: string | null };
  source_document: string;
}

export interface ProtectionHousehold {
  household_id: string;
  household: string;
  currency: string | null;
  people: ProtectionPerson[];
  policies: ProtectionPolicy[];
  assets: { managed: number; held_away: number; retirement_accounts: number; total: number };
  summary: { people: number; gaps: number; largest_gap: number; not_assessed: number; policies_counted: number };
  care_funding?: CareFunding;
}

/** B-186: how a household means to pay for care, as its adviser recorded it. None is "no decision", never self-fund. */
export interface CareFunding {
  status?: "decided" | "no_decision";
  choice: string | null;
  earmark_amount: number | null;
  reason?: string;
  decided_on?: string;
  recorded_by?: string;
  superseded?: number | string | null;
}

export const CARE_FUNDING_TEXT: Record<string, string> = {
  self_fund: "self-fund", care_insurance: "care insurance", hybrid: "hybrid", medicaid_planning: "Medicaid planning", undecided: "undecided",
};

export function CareFundingChips({ funding, testid = "care-funding" }: { funding: CareFunding | undefined; testid?: string }) {
  if (!funding || !funding.choice) {
    return <span className="chip text-warn" data-testid={testid}>care paid by · no decision on file</span>;
  }
  return (
    <span className="inline-flex flex-wrap gap-1.5" data-testid={testid}>
      <span className="chip text-ink" title={funding.reason}>care paid by · {CARE_FUNDING_TEXT[funding.choice] ?? funding.choice}</span>
      {funding.earmark_amount != null && <span className="chip text-ink-muted">earmarked {money(funding.earmark_amount)}</span>}
      {funding.decided_on && <span className="chip text-ink-muted">{funding.decided_on}</span>}
    </span>
  );
}

interface Book {
  households: ProtectionHousehold[];
  summary: { households: number; with_gap: number; clear: number; not_assessed: number; no_people: number; people_not_assessed: number };
  not_modelled: string[];
}

export function money(v: number | null | undefined) {
  if (v == null) return "—";
  return v.toLocaleString(firmLocale(), { maximumFractionDigits: 0 });
}

export const KIND_TEXT: Record<string, string> = {
  life: "life", tpd: "TPD", income_protection: "income", long_term_care: "care", critical_illness: "critical illness", personal_accident: "personal accident", umbrella: "umbrella", property: "property",
};

/** Who a policy pays, as chip text: the nominee as named, or a Married Women's Property Act trust. */
export function payeeText(p: ProtectionPolicy): string | null {
  if (!p.payee) return null;
  return p.payee.to ? `to ${p.payee.to}${p.payee.relationship ? ` (${p.payee.relationship})` : ""}` : "no nominee on file";
}

export const SCENARIO_TEXT: Record<string, string> = { death: "if they die", disability: "if they cannot work", care: "if they need care" };

export function Scenario({ s }: { s: ProtectionScenario }) {
  if (s.status === "gap") return <span className="font-mono tabular-nums text-crit">{money(s.gap)}</span>;
  if (s.status === "no_gap") {
    return (
      <span className="chip text-ok" title={s.gap_before_assets ? `${money(s.gap_before_assets)} before counting assets` : undefined}>
        {s.gap_before_assets ? "self-insured" : s.need ? "covered" : "no need"}
      </span>
    );
  }
  if (s.status === "past_retirement") return <span className="chip text-ink-muted">past retirement</span>;
  if (s.status === "not_yet") return <span className="chip text-ink-muted">not yet</span>;
  return <span className="chip text-warn">{(s.missing ?? []).join(" · ").replace(/_/g, " ")} not on file</span>;
}

function PeopleTable({ rows, currency }: { rows: { household?: string; p: ProtectionPerson }[]; currency?: string | null }) {
  return (
    <div className="overflow-x-auto">
    <table className="w-full text-sm">
      <thead>
        <tr className="text-left text-[10px] uppercase tracking-wide text-ink-muted">
          <th className="px-3 py-1.5 font-medium">Person</th>
          <th className="px-3 py-1.5 text-right font-medium">Income</th>
          <th className="px-3 py-1.5 text-right font-medium">If they die{currency ? ` · ${currency}` : ""}</th>
          <th className="px-3 py-1.5 text-right font-medium">If they cannot work</th>
          <th className="px-3 py-1.5 text-right font-medium">If they need care</th>
        </tr>
      </thead>
      <tbody className="divide-y divide-border-soft">
        {rows.map(({ household, p }) => (
          <tr key={p.person_id}>
            <td className="px-3 py-1.5">
              <div className="text-ink">{p.name}</div>
              {household && <div className="text-xs text-ink-muted">{household}</div>}
            </td>
            <td className="px-3 py-1.5 text-right font-mono tabular-nums text-ink-muted">{money(p.income)}</td>
            <td className="px-3 py-1.5 text-right"><Scenario s={p.death} /></td>
            <td className="px-3 py-1.5 text-right"><Scenario s={p.disability} /></td>
            <td className="px-3 py-1.5 text-right"><Scenario s={p.care} /></td>
          </tr>
        ))}
      </tbody>
    </table>
    </div>
  );
}

const SHOWN = 8;

export function ProtectionCard({ result }: { result: any }) {
  if (Array.isArray(result?.households)) {
    const b = result as Book;
    const rows = b.households.flatMap((h) => h.people.map((p) => ({ household: h.household, p })));
    const ranked = rows.filter((r) => [r.p.death, r.p.disability, r.p.care].some((s) => s.status === "gap"));
    const shown = (ranked.length ? ranked : rows).slice(0, SHOWN);
    return (
      <div className="overflow-hidden rounded-lg border border-border bg-surface" data-testid="protection-card">
        <div className="flex flex-wrap items-center justify-between gap-2 border-b border-border-soft px-3 py-2">
          <div className="text-sm font-medium text-ink">Protection</div>
          <div className="flex flex-wrap gap-1.5">
            <span className="chip text-crit">{b.summary.with_gap} with a gap</span>
            <span className="chip text-ok">{b.summary.clear} clear</span>
            <span className="chip text-warn">{b.summary.not_assessed} not assessed</span>
          </div>
        </div>
        <PeopleTable rows={shown} />
        <div className="flex flex-wrap gap-1.5 border-t border-border-soft px-3 py-2">
          {rows.length > shown.length && <span className="chip text-ink-muted">+{rows.length - shown.length} more</span>}
          <span className="chip text-ink-muted">not modelled: {b.not_modelled.join(" · ").replace(/_/g, " ")}</span>
        </div>
      </div>
    );
  }
  if (Array.isArray(result?.people)) {
    const h = result as ProtectionHousehold & { not_modelled: string[] };
    return (
      <div className="overflow-hidden rounded-lg border border-border bg-surface" data-testid="protection-card">
        <div className="flex flex-wrap items-center justify-between gap-2 border-b border-border-soft px-3 py-2">
          <div className="text-sm font-medium text-ink">Protection · {h.household}</div>
          <div className="flex flex-wrap gap-1.5">
            <span className="chip text-ink-muted">assets {money(h.assets.total)}</span>
            <span className="chip text-ink-muted">{h.summary.policies_counted} polic{h.summary.policies_counted === 1 ? "y" : "ies"} on file</span>
          </div>
        </div>
        {h.people.length === 0
          ? <div className="px-3 py-3 font-mono text-xs text-ink-muted">no one to assess</div>
          : <PeopleTable rows={h.people.map((p) => ({ p }))} currency={h.currency} />}
        <div className="flex flex-wrap gap-1.5 border-t border-border-soft px-3 py-2">
          {h.policies.filter((p) => p.counted).map((p) => (
            <span key={p.id} className="chip text-ink-muted" title={[p.source_document, p.payee?.means].filter(Boolean).join(" · ")}>
              {p.person} · {KIND_TEXT[p.kind] ?? p.kind} · {money(p.sum_insured ?? p.annual_benefit)}{payeeText(p) ? ` · ${payeeText(p)}` : ""}
            </span>
          ))}
          {h.policies.filter((p) => p.status === "in_force" && p.not_counted).map((p) => (
            <span key={p.id} className="chip text-warn" title={p.not_counted ?? undefined} data-testid="protection-not-counted">
              {p.person} · {KIND_TEXT[p.kind] ?? p.kind} · not counted
            </span>
          ))}
          <CareFundingChips funding={h.care_funding} />
          <span className="chip text-ink-muted">not modelled: {h.not_modelled.join(" · ").replace(/_/g, " ")}</span>
        </div>
      </div>
    );
  }
  return (
    <div className="flex flex-wrap items-center gap-1.5 rounded-lg border border-border bg-surface px-3 py-2" data-testid="protection-record-card">
      {result?.person && <span className="chip text-ink">{result.person}</span>}
      {result?.kind && <span className="chip text-ink-muted">{KIND_TEXT[result.kind] ?? result.kind}</span>}
      {result?.provider && <span className="chip text-ink-muted">{result.provider}</span>}
      {result?.status && <span className="chip text-ink-muted">{result.status}</span>}
      {(result?.sum_insured ?? result?.annual_benefit ?? result?.annual_income) != null && (
        <span className="chip text-ink-muted">{money(result.sum_insured ?? result.annual_benefit ?? result.annual_income)}</span>
      )}
    </div>
  );
}
