import { money } from "@/lib/format";

// The firm's tax book, from `read_tax_book` (the JSON of GET /api/analytics/tax-book, built by
// tax_intelligence.for_firm, plus the agent's `message`). Figures and chips only: the message carries
// the sentences. NZ aggregates are in NZD; every per-household row carries its own currency.

const Chip = ({ children, tone = "text-ink-muted", title }: { children: React.ReactNode; tone?: string; title?: string }) => (
  <span className={`inline-block rounded border border-border px-1.5 py-0.5 font-mono text-[11px] ${tone}`} title={title}>{children}</span>
);

function Tile({ label, value, tone = "text-ink" }: { label: string; value: React.ReactNode; tone?: string }) {
  return (
    <div className="min-w-0 px-3 py-2">
      <div className="truncate font-mono text-[11px] uppercase tracking-wider text-ink-muted">{label}</div>
      <div className={`mt-0.5 truncate font-mono text-lg tabular-nums ${tone}`}>{value}</div>
    </div>
  );
}

interface BookHousehold {
  household_id: string;
  household_name: string;
  total_portfolio_value: number;
  currency: string;
  tax_year?: string | number;
  summary: {
    total_flags?: number;
    harvestable_loss?: number;
    estimated_tax_saving?: number;
    estimated_tax?: number;
    exemption_unused?: number;
    taxpayers?: number;
    rmd_required?: number;
    roth_opportunity?: number;
    cgt_flag?: boolean;
    dividend_flag?: boolean;
  };
}

export interface TaxBookPayload {
  total_households_scanned: number;
  generated_at: string;
  jurisdictions?: { NZ?: number; US?: number; UK?: number; IN?: number; not_supported?: number };
  loss_harvest: { opportunities?: any[]; count: number; total_harvestable_loss: number; estimated_tax_saving: number };
  pie_optimisation: { flags?: any[]; count: number; total_annual_saving: number };
  kiwisaver: { recommendations?: any[]; count: number };
  bright_line: { flags?: any[]; count: number };
  us_households?: BookHousehold[];
  uk_households?: BookHousehold[];
  in_households?: BookHousehold[];
  message?: string;
}

const isNum = (v: any) => typeof v === "number" && Number.isFinite(v);
const isObj = (v: any) => v !== null && typeof v === "object";
const rowsOk = (v: any) =>
  v === undefined || (Array.isArray(v) && v.every((h) => isObj(h) && typeof h.household_name === "string" && isObj(h.summary)));

/** True when the result has what the card reads, so a malformed one renders nothing. */
export function isTaxBook(r: any): boolean {
  return (
    isObj(r) &&
    isNum(r.total_households_scanned) &&
    isObj(r.loss_harvest) && isNum(r.loss_harvest.count) && isNum(r.loss_harvest.total_harvestable_loss) && isNum(r.loss_harvest.estimated_tax_saving) &&
    isObj(r.pie_optimisation) && isNum(r.pie_optimisation.count) && isNum(r.pie_optimisation.total_annual_saving) &&
    isObj(r.kiwisaver) && isNum(r.kiwisaver.count) &&
    isObj(r.bright_line) && isNum(r.bright_line.count) &&
    (r.jurisdictions === undefined || isObj(r.jurisdictions)) &&
    rowsOk(r.us_households) && rowsOk(r.uk_households) && rowsOk(r.in_households)
  );
}

const SHOWN = 6;

function Households({ title, rows, kind }: { title: string; rows: BookHousehold[]; kind: "US" | "UK" | "IN" }) {
  if (!rows.length) return null;
  const shown = rows.slice(0, SHOWN);
  return (
    <div className="border-t border-border-soft">
      <div className="flex items-center justify-between px-3 pt-2 font-mono text-[11px] uppercase tracking-wider text-ink-muted">
        <span>{title}</span>
        <span className="tabular-nums">{rows.length > SHOWN ? `${SHOWN} of ${rows.length}` : rows.length}</span>
      </div>
      <table className="w-full text-sm">
        <tbody className="divide-y divide-border-soft">
          {shown.map((h) => {
            const s = h.summary;
            return (
              <tr key={h.household_id}>
                <td className="max-w-0 truncate px-3 py-1.5 text-ink">{h.household_name}</td>
                <td className="px-3 py-1.5">
                  <div className="flex flex-wrap justify-end gap-1">
                    {kind === "US" && (s.rmd_required ?? 0) > 0 && <Chip tone="text-crit">RMD {s.rmd_required}</Chip>}
                    {kind === "US" && (s.roth_opportunity ?? 0) > 0 && <Chip tone="text-ok">Roth {s.roth_opportunity}</Chip>}
                    {kind === "UK" && s.cgt_flag && <Chip tone="text-warn">CGT</Chip>}
                    {kind === "UK" && s.dividend_flag && <Chip tone="text-warn">Dividend</Chip>}
                    {kind === "IN" && <Chip>est. tax {money(s.estimated_tax, h.currency)}</Chip>}
                    {kind !== "IN" && <Chip tone={(s.total_flags ?? 0) > 0 ? "text-warn" : "text-ink-muted"}>flags {s.total_flags ?? 0}</Chip>}
                  </div>
                </td>
                <td className="whitespace-nowrap px-3 py-1.5 text-right font-mono text-xs tabular-nums text-ink-muted" title="harvestable loss">
                  {money(s.harvestable_loss, h.currency)}
                </td>
              </tr>
            );
          })}
        </tbody>
      </table>
    </div>
  );
}

export function TaxBookCard({ result }: { result: TaxBookPayload }) {
  const j = result.jurisdictions ?? {};
  const lh = result.loss_harvest;
  const pie = result.pie_optimisation;
  const us = result.us_households ?? [];
  const uk = result.uk_households ?? [];
  const inr = result.in_households ?? [];
  const nz = (j.NZ ?? 0) > 0 || lh.count > 0 || pie.count > 0 || result.kiwisaver.count > 0 || result.bright_line.count > 0;
  const nzd = (n: number) => money(n, "NZD");

  return (
    <div className="overflow-hidden rounded-lg border border-border bg-surface" data-testid="tax-book-card">
      <div className="flex items-center justify-between border-b border-border-soft px-3 py-2">
        <div className="text-sm font-medium text-ink">Tax book</div>
        <div className="font-mono text-xs tabular-nums text-ink-muted">{result.total_households_scanned}</div>
      </div>
      <div className="flex flex-wrap gap-1.5 border-b border-border-soft px-3 py-1.5">
        {(["NZ", "US", "UK", "IN"] as const).filter((k) => (j[k] ?? 0) > 0).map((k) => <Chip key={k}>{k} {j[k]}</Chip>)}
        {(j.not_supported ?? 0) > 0 && <Chip tone="text-warn">No tax rules {j.not_supported}</Chip>}
        <Chip tone="text-warn">estimates</Chip>
        {result.generated_at && <Chip>{result.generated_at}</Chip>}
      </div>
      {nz && (
        <div className="grid grid-cols-2 divide-x divide-y divide-border-soft sm:grid-cols-4 sm:divide-y-0">
          <Tile label="NZ harvestable" value={nzd(lh.total_harvestable_loss)} tone={lh.count > 0 ? "text-crit" : "text-ink"} />
          <Tile label="Saving · top PIR" value={nzd(lh.estimated_tax_saving)} tone="text-ok" />
          <Tile label="PIE / yr" value={nzd(pie.total_annual_saving)} tone="text-ok" />
          <Tile label="Bright-line" value={result.bright_line.count} tone={result.bright_line.count > 0 ? "text-warn" : "text-ink"} />
        </div>
      )}
      {nz && (lh.count > 0 || result.kiwisaver.count > 0 || pie.count > 0) && (
        <div className="flex flex-wrap gap-1.5 border-t border-border-soft px-3 py-1.5">
          <Chip>NZ loss lots {lh.count}</Chip>
          <Chip>PIE flags {pie.count}</Chip>
          <Chip>KiwiSaver {result.kiwisaver.count}</Chip>
        </div>
      )}
      <Households title="US households" rows={us} kind="US" />
      <Households title="UK households" rows={uk} kind="UK" />
      <Households title="India households" rows={inr} kind="IN" />
    </div>
  );
}
