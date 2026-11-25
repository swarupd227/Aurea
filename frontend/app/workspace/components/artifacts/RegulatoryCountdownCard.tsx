import { money } from "@/lib/format";

// The read_regulatory_countdown card: the dated rule changes tracked for a household's market (US
// estate exemption; UK pensions in the IHT estate, the BPR/APR cap, targeted support), or every
// household with one across the firm. Figures and chips only: the analyses' summaries and planning
// detail stay on the view, and the agent's message carries the words.

export type CountdownType = "us_estate_tax_sunset" | "uk_iht_pension_inclusion" | "uk_bpr_apr_cap" | "uk_fca_targeted_support" | "in_client_dates" | "in_firm_deadlines";

export interface CountdownAction {
  priority: number;
  action: string;
  detail: string;
  estimated_benefit: string;
}

/** A figure fixed in law, with where it comes from and when it was taken (aurea_core/law_figures.py). */
export interface LawFigure {
  key: string;
  label: string;
  value: number | null;
  unit: string;
  source: string;
  as_of: string;
  applies_from: string | null;
  applies_to: string | null;
  status: "in_force" | "upcoming" | "expired" | string;
  /** What counsel said about it (an Indian figure): not reviewed by default. */
  counsel?: { state: string; label: string; reviewer?: string; reviewed_on?: string; note?: string | null };
}

export interface CountdownAnalysis {
  type: CountdownType | string;
  title: string;
  subtitle: string;
  deadline: string;
  /** upcoming: a countdown; in_force or expired: the date has passed and is not counted down. */
  status?: "upcoming" | "in_force" | "expired" | string;
  days_remaining: number;
  urgency: "critical" | "high" | "medium" | string;
  summary: string;
  figures: Record<string, any>;
  actions: CountdownAction[];
  currency: string;
  regulation: string;
  legislative_note?: string;
  law?: LawFigure[];
  assumed?: string[];
}

export interface CountdownHousehold {
  household_id: string;
  household_name: string;
  jurisdiction: string;
  analyses: CountdownAnalysis[];
  message?: string;
}

/** One household's analysis, or (firm-wide) every household with an analysis, and for an Indian firm its own dates. */
export type RegulatoryCountdownPayload =
  | (CountdownHousehold & { message?: string })
  | { results: CountdownHousehold[]; total: number; message?: string; india_firm?: { analysis: CountdownAnalysis } | null };

/** One of India's dates: what, when, where it stands, its source and whether the day is assumed (aurea_core/india_deadlines.py). */
export interface IndiaDate {
  kind: string;
  label: string;
  due_on: string | null;
  days: number | null;
  state: "overdue" | "due_soon" | "upcoming" | "in_force" | "not_dated" | string;
  source: string;
  assumed: string | null;
  detail: string | null;
}

export const DATE_TONE: Record<string, string> = { overdue: "text-crit", due_soon: "text-warn", upcoming: "text-ink-muted", in_force: "text-ink-muted", not_dated: "text-ink-muted" };
export const DATE_TEXT: Record<string, string> = { overdue: "Overdue", due_soon: "Due soon", upcoming: "Upcoming", in_force: "In force", not_dated: "No day" };

export const COUNTDOWN_LABEL: Record<string, string> = {
  in_client_dates: "India: tax, KYC and nominations",
  in_firm_deadlines: "India: the firm's dates",
  us_estate_tax_sunset: "US estate exemption",
  uk_iht_pension_inclusion: "Pensions in IHT estate",
  uk_bpr_apr_cap: "BPR/APR cap",
  uk_fca_targeted_support: "FCA targeted support",
};

export const URGENCY_TONE: Record<string, string> = { critical: "text-crit", high: "text-warn", medium: "text-ink-muted", in_force: "text-ink-muted", expired: "text-ink-muted" };
export const URGENCY_LABEL: Record<string, string> = { critical: "critical", high: "high", medium: "monitor", in_force: "in force", expired: "expired" };

/** The extra tax an analysis puts a figure on, where it has one. */
export function extraTax(a: CountdownAnalysis): number | null {
  const f = a.figures ?? {};
  // The US exemption has no change pending (its sunset was superseded), so it has no extra tax to show.
  const v = a.type === "uk_iht_pension_inclusion" ? f.additional_iht
    : a.type === "uk_bpr_apr_cap" ? f.estimated_additional_iht
    : null;
  return typeof v === "number" && !isNaN(v) ? v : null;
}

/** Days to the deadline while it is still to come; once it has passed, in force or expired, never a countdown. */
export const countdown = (a: CountdownAnalysis) =>
  a.status === "expired" ? "expired" : a.days_remaining > 0 && a.status !== "in_force" ? `${a.days_remaining}d` : "in force";

function isAnalysis(a: any): boolean {
  return !!a
    && typeof a.type === "string" && typeof a.title === "string" && typeof a.deadline === "string"
    && typeof a.days_remaining === "number" && typeof a.urgency === "string" && typeof a.currency === "string"
    && !!a.figures && typeof a.figures === "object";
}

function isHousehold(h: any): boolean {
  return !!h
    && typeof h.household_name === "string" && typeof h.jurisdiction === "string"
    && Array.isArray(h.analyses) && h.analyses.every(isAnalysis);
}

export function isRegulatoryCountdown(r: any): boolean {
  if (!r) return false;
  if (Array.isArray(r.results)) return typeof r.total === "number" && r.results.every(isHousehold);
  return isHousehold(r);
}

const Chip = ({ children, tone = "text-ink-muted" }: { children: React.ReactNode; tone?: string }) => (
  <span className={`inline-block rounded border border-border px-1.5 py-0.5 font-mono text-[11px] ${tone}`}>{children}</span>
);

function Row({ a }: { a: CountdownAnalysis }) {
  const extra = extraTax(a);
  return (
    <tr>
      <td className="px-3 py-1.5 text-ink">{COUNTDOWN_LABEL[a.type] ?? a.title}</td>
      <td className="px-3 py-1.5 font-mono text-xs tabular-nums text-ink-muted whitespace-nowrap">{countdown(a)}</td>
      <td className={`px-3 py-1.5 text-xs ${URGENCY_TONE[a.urgency] ?? "text-ink-muted"}`}>{URGENCY_LABEL[a.urgency] ?? a.urgency}</td>
      <td className={`px-3 py-1.5 text-right font-mono text-xs tabular-nums whitespace-nowrap ${extra ? "text-crit" : "text-ink-muted"}`}>
        {extra == null ? "—" : extra > 0 ? `+${money(extra, a.currency)}` : "none"}
      </td>
    </tr>
  );
}

export function RegulatoryCountdownCard({ result }: { result: RegulatoryCountdownPayload }) {
  if ("results" in result) {
    return (
      <div className="overflow-hidden rounded-lg border border-border bg-surface" data-testid="regulatory-countdown-card">
        <div className="flex items-center justify-between border-b border-border-soft px-3 py-2">
          <div className="text-sm font-medium text-ink">Regulatory countdown</div>
          <div className="font-mono text-xs tabular-nums text-ink-muted">{result.total}</div>
        </div>
        {result.india_firm && (
          <div className="border-b border-border-soft px-3 py-1.5" data-testid="countdown-india-firm">
            <div className="tile-label mb-1">The firm's India dates</div>
            <IndiaDateItems items={result.india_firm.analysis.figures.items} limit={6} />
          </div>
        )}
        {result.results.length === 0 ? (
          <div className="px-3 py-2"><Chip>no household with a tracked deadline</Chip></div>
        ) : (
          <div className="divide-y divide-border-soft">
            {result.results.map((h) => (
              <div key={h.household_id} className="px-3 py-1.5">
                <div className="flex items-center gap-2">
                  <span className="truncate text-sm text-ink">{h.household_name}</span>
                  <Chip>{h.jurisdiction}</Chip>
                </div>
                <div className="mt-1 flex flex-wrap gap-1.5">
                  {h.analyses.map((a, i) => {
                    const extra = extraTax(a);
                    return (
                      <Chip key={i} tone={URGENCY_TONE[a.urgency] ?? "text-ink-muted"}>
                        {COUNTDOWN_LABEL[a.type] ?? a.title} · {countdown(a)}{extra ? ` · +${money(extra, a.currency)}` : ""}
                      </Chip>
                    );
                  })}
                </div>
              </div>
            ))}
          </div>
        )}
      </div>
    );
  }

  return (
    <div className="overflow-hidden rounded-lg border border-border bg-surface" data-testid="regulatory-countdown-card">
      <div className="flex items-center justify-between gap-2 border-b border-border-soft px-3 py-2">
        <div className="truncate text-sm font-medium text-ink">Regulatory countdown · {result.household_name}</div>
        <Chip>{result.jurisdiction}</Chip>
      </div>
      {result.analyses.length === 0 ? (
        <div className="px-3 py-2"><Chip>no deadline tracked for {result.jurisdiction}</Chip></div>
      ) : (
        <table className="w-full text-sm">
          <thead>
            <tr className="text-left font-mono text-[10px] uppercase tracking-wider text-ink-muted">
              <th className="px-3 py-1 font-medium">Change</th>
              <th className="px-3 py-1 font-medium">Due</th>
              <th className="px-3 py-1 font-medium">Urgency</th>
              <th className="px-3 py-1 text-right font-medium">Extra tax</th>
            </tr>
          </thead>
          <tbody className="divide-y divide-border-soft">
            {result.analyses.map((a, i) => <Row key={i} a={a} />)}
          </tbody>
        </table>
      )}
    </div>
  );
}


/** India's dates as rows: each with its state, its day, its source and "assumed" where the day is this build's reading. */
export function IndiaDateItems({ items, limit = 12 }: { items: IndiaDate[]; limit?: number }) {
  return (
    <div className="divide-y divide-border-soft" data-testid="india-dates">
      {items.slice(0, limit).map((i, k) => (
        <div key={k} className="flex flex-wrap items-center gap-1.5 py-1.5" data-testid="india-date">
          <span className="mr-auto min-w-0 flex-1 text-sm text-ink" title={i.detail ?? ""}>{i.label}</span>
          {i.due_on && <span className="chip tabular-nums text-ink-muted">{i.due_on}</span>}
          {i.days != null && i.state !== "in_force" && <span className={`chip tabular-nums ${DATE_TONE[i.state]}`}>{i.days < 0 ? `${-i.days} days over` : `${i.days}d`}</span>}
          <span className={`chip ${DATE_TONE[i.state] ?? "text-ink-muted"}`} data-testid="india-date-state">{DATE_TEXT[i.state] ?? i.state}</span>
          {i.assumed && <span className="chip text-warn" title={i.assumed}>assumed</span>}
          <span className="chip text-ink-muted" title={i.source}>source</span>
        </div>
      ))}
      {items.length > limit && <div className="py-1.5"><span className="chip text-ink-muted">+{items.length - limit} more</span></div>}
    </div>
  );
}
