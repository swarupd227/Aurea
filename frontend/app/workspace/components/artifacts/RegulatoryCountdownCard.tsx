import { money } from "@/lib/format";

// The read_regulatory_countdown card: the dated rule changes tracked for a household's market (US
// estate exemption; UK pensions in the IHT estate, the BPR/APR cap, targeted support), or every
// household with one across the firm. Figures and chips only: the analyses' summaries and planning
// detail stay on the view, and the agent's message carries the words.

export type CountdownType = "us_estate_tax_sunset" | "uk_iht_pension_inclusion" | "uk_bpr_apr_cap" | "uk_fca_targeted_support";

export interface CountdownAction {
  priority: number;
  action: string;
  detail: string;
  estimated_benefit: string;
}

export interface CountdownAnalysis {
  type: CountdownType | string;
  title: string;
  subtitle: string;
  deadline: string;
  days_remaining: number;
  urgency: "critical" | "high" | "medium" | string;
  summary: string;
  figures: Record<string, any>;
  actions: CountdownAction[];
  currency: string;
  regulation: string;
  legislative_note?: string;
}

export interface CountdownHousehold {
  household_id: string;
  household_name: string;
  jurisdiction: string;
  analyses: CountdownAnalysis[];
  message?: string;
}

/** One household's analysis, or (firm-wide) every household with an analysis. */
export type RegulatoryCountdownPayload =
  | (CountdownHousehold & { message?: string })
  | { results: CountdownHousehold[]; total: number; message?: string };

export const COUNTDOWN_LABEL: Record<string, string> = {
  us_estate_tax_sunset: "US estate exemption",
  uk_iht_pension_inclusion: "Pensions in IHT estate",
  uk_bpr_apr_cap: "BPR/APR cap",
  uk_fca_targeted_support: "FCA targeted support",
};

export const URGENCY_TONE: Record<string, string> = { critical: "text-crit", high: "text-warn", medium: "text-ink-muted" };
export const URGENCY_LABEL: Record<string, string> = { critical: "critical", high: "high", medium: "monitor" };

/** The extra tax an analysis puts a figure on, where it has one. */
export function extraTax(a: CountdownAnalysis): number | null {
  const f = a.figures ?? {};
  const v = a.type === "us_estate_tax_sunset" ? f.additional_tax_at_risk
    : a.type === "uk_iht_pension_inclusion" ? f.additional_iht
    : a.type === "uk_bpr_apr_cap" ? f.estimated_additional_iht
    : null;
  return typeof v === "number" && !isNaN(v) ? v : null;
}

/** Days to the deadline, or in force once it has passed. */
export const countdown = (a: CountdownAnalysis) => (a.days_remaining > 0 ? `${a.days_remaining}d` : "in force");

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
