import { money } from "@/lib/format";
import { reviewDay } from "./ReviewsCard";

export interface BalanceItem {
  id: string;
  side: "asset" | "liability" | "expense";
  kind: string;
  name: string;
  amount: number;
  currency: string;
  person_id: string | null;
  person: string | null;
  annual_payment: number | null;
  interest_rate: number | null;
  ends_on: string | null;
  as_of: string;
  source_document: string;
}

export interface BalanceSheet {
  household_id: string;
  household: string;
  currency: string | null;
  people: { person_id: string; name: string }[];
  assets: { managed: number; held_away: number; retirement_accounts: number; cash: number; property: number; business: number; other: number; total: number };
  liquid: number;
  liabilities: { total: number; by_kind: Record<string, number> };
  net_worth: number;
  basis: "assets_only" | "assets_and_liabilities";
  cash_flow: {
    status: "assessed" | "not_assessed";
    missing: string[];
    income: number | null;
    income_partial: boolean;
    expenses: number | null;
    debt_payments: number;
    payments_missing: number;
    surplus: number | null;
  };
  items: BalanceItem[];
  not_converted: BalanceItem[];
}

export const kindText = (k: string) => k.replace(/_/g, " ");
export const outsideBook = (b: BalanceSheet) => b.assets.total - b.assets.managed;

export function BasisChip({ basis }: { basis: BalanceSheet["basis"] }) {
  return basis === "assets_only"
    ? <span className="chip text-warn" data-testid="balance-basis-assets-only">assets only</span>
    : <span className="chip text-ok" data-testid="balance-basis-full">assets and debts</span>;
}

export function CashFlowChip({ flow }: { flow: BalanceSheet["cash_flow"] }) {
  if (flow.status !== "assessed") {
    return <span className="chip text-ink-muted" title={`missing ${flow.missing.join(", ")}`} data-testid="balance-cashflow-missing">cash flow not assessed</span>;
  }
  const short = (flow.surplus ?? 0) < 0;
  return <span className={`chip ${short ? "text-crit" : "text-ok"}`} data-testid="balance-cashflow">{short ? "short" : "surplus"} {money(Math.abs(flow.surplus ?? 0))}/yr</span>;
}

function Rows({ rows }: { rows: BalanceSheet[] }) {
  return (
    <table className="w-full text-sm">
      <thead>
        <tr className="text-left text-[10px] uppercase tracking-wide text-ink-muted">
          <th className="px-3 py-1.5 font-medium">Client</th>
          <th className="px-3 py-1.5 text-right font-medium">Managed</th>
          <th className="px-3 py-1.5 text-right font-medium">Outside the book</th>
          <th className="px-3 py-1.5 text-right font-medium">Owed</th>
          <th className="px-3 py-1.5 text-right font-medium">Net worth</th>
          <th className="px-3 py-1.5 text-right font-medium">Basis</th>
        </tr>
      </thead>
      <tbody className="divide-y divide-border-soft">
        {rows.map((b) => (
          <tr key={b.household_id}>
            <td className="px-3 py-1.5 text-ink">{b.household}</td>
            <td className="px-3 py-1.5 text-right font-mono text-xs tabular-nums text-ink-muted">{money(b.assets.managed)}</td>
            <td className="px-3 py-1.5 text-right font-mono text-xs tabular-nums text-ink-muted">{money(outsideBook(b))}</td>
            <td className="px-3 py-1.5 text-right font-mono text-xs tabular-nums text-ink-muted">{b.basis === "assets_only" ? "—" : money(b.liabilities.total)}</td>
            <td className="px-3 py-1.5 text-right font-mono text-xs tabular-nums text-ink">{money(b.net_worth)}</td>
            <td className="px-3 py-1.5 text-right"><BasisChip basis={b.basis} /></td>
          </tr>
        ))}
      </tbody>
    </table>
  );
}

const SHOWN = 8;

export function BalanceSheetCard({ result }: { result: any }) {
  if (Array.isArray(result?.households)) {
    const s = result.summary;
    const rows: BalanceSheet[] = result.households;
    const shown = rows.slice(0, SHOWN);
    return (
      <div className="overflow-hidden rounded-lg border border-border bg-surface" data-testid="balance-card">
        <div className="flex flex-wrap items-center justify-between gap-2 border-b border-border-soft px-3 py-2">
          <div className="text-sm font-medium text-ink">Net worth</div>
          <div className="flex flex-wrap gap-1.5">
            <span className="chip text-ok">{s.with_liabilities} with debts on file</span>
            <span className="chip text-warn">{s.assets_only} assets only</span>
            <span className="chip text-ink-muted">{s.cash_flow_assessed} cash flow assessed</span>
          </div>
        </div>
        <div className="overflow-x-auto"><Rows rows={shown} /></div>
        {rows.length > shown.length && (
          <div className="border-t border-border-soft px-3 py-2"><span className="chip text-ink-muted">+{rows.length - shown.length} more</span></div>
        )}
      </div>
    );
  }
  const b = result as BalanceSheet;
  return (
    <div className="overflow-hidden rounded-lg border border-border bg-surface" data-testid="balance-card">
      <div className="flex flex-wrap items-center justify-between gap-2 border-b border-border-soft px-3 py-2">
        <div className="text-sm font-medium text-ink">Net worth · {b.household}</div>
        <div className="flex flex-wrap gap-1.5">
          <BasisChip basis={b.basis} />
          <CashFlowChip flow={b.cash_flow} />
        </div>
      </div>
      <div className="overflow-x-auto"><Rows rows={[b]} /></div>
      {(b.items.length > 0 || b.not_converted.length > 0) && (
        <div className="flex flex-wrap gap-1.5 border-t border-border-soft px-3 py-2">
          {b.items.map((i) => (
            <span key={i.id} className={`chip ${i.side === "liability" ? "text-crit" : "text-ink-muted"}`} title={i.source_document}>
              {kindText(i.kind)} · {i.name} · {money(i.amount, i.currency)} · {reviewDay(i.as_of)}
            </span>
          ))}
          {b.not_converted.length > 0 && <span className="chip text-warn">{b.not_converted.length} not converted</span>}
        </div>
      )}
    </div>
  );
}
