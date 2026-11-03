import { money } from "@/lib/format";
import { kindText } from "./BalanceSheetCard";
import { reviewDay } from "./ReviewsCard";
import { CardHead, CardShell, Chip, Tile, obj } from "./CanvasCardParts";

// The client's own net worth (`read_my_net_worth`): the balance sheet as its client reads it - what
// each line is, how much and as of when; no source document, no cash flow, no other household.

export interface MyNetWorthItem {
  id: string;
  side: "asset" | "liability" | "expense";
  kind: string;
  name: string;
  amount: number;
  currency: string;
  person: string | null;
  as_of: string;
}

export interface MyNetWorthPayload {
  household_id: string;
  currency: string | null;
  net_worth: number;
  basis: "assets_only" | "assets_and_liabilities";
  assets: { managed: number; held_away?: number; total: number } & Record<string, number>;
  liabilities: { total: number; by_kind?: Record<string, number> };
  items: MyNetWorthItem[];
  not_converted: MyNetWorthItem[];
  message?: string;
}

export function isMyNetWorth(r: any): boolean {
  return obj(r)
    && typeof r.net_worth === "number"
    && (r.basis === "assets_only" || r.basis === "assets_and_liabilities")
    && obj(r.assets) && typeof r.assets.total === "number"
    && obj(r.liabilities) && typeof r.liabilities.total === "number"
    && Array.isArray(r.items)
    && Array.isArray(r.not_converted);
}

const SHOWN = 10;

export function MyNetWorthCard({ result }: { result: MyNetWorthPayload }) {
  const r = result;
  const ccy = r.currency ?? undefined;
  const lines = r.items.filter((i) => i.side !== "expense");
  const shown = lines.slice(0, SHOWN);
  const elsewhere = r.assets.total - (r.assets.managed ?? 0);
  return (
    <CardShell testid="my-net-worth-card">
      <CardHead title="Net worth">
        {r.basis === "assets_only"
          ? <Chip tone="text-warn" testid="my-net-worth-no-debts">no debts on file</Chip>
          : <Chip tone="text-ok">assets and debts</Chip>}
        {r.not_converted.length > 0 && <Chip tone="text-warn">{r.not_converted.length} not converted</Chip>}
      </CardHead>
      <div className="grid grid-cols-3 divide-x divide-border-soft border-b border-border-soft">
        <Tile label="Net worth" value={money(r.net_worth, ccy)} />
        <Tile label="Assets" value={money(r.assets.total, ccy)} sub={`managed ${money(r.assets.managed, ccy)} · elsewhere ${money(elsewhere, ccy)}`} />
        <Tile label="Owed" value={r.basis === "assets_only" ? "—" : money(r.liabilities.total, ccy)} tone={r.basis === "assets_only" ? "text-ink-muted" : "text-ink"} />
      </div>
      {shown.length > 0 && (
        <table className="w-full table-fixed text-sm">
          <tbody className="divide-y divide-border-soft">
            {shown.map((i) => (
              <tr key={i.id}>
                <td className="truncate px-3 py-1.5 text-ink">{i.name}<span className="ml-1.5 text-xs text-ink-muted">{kindText(i.kind)}{i.person ? ` · ${i.person}` : ""}</span></td>
                <td className={`w-28 px-3 py-1.5 text-right font-mono text-xs tabular-nums ${i.side === "liability" ? "text-crit" : "text-ink"}`}>
                  {i.side === "liability" ? "−" : ""}{money(i.amount, i.currency)}
                </td>
                <td className="w-24 px-3 py-1.5 text-right font-mono text-[11px] text-ink-muted">{reviewDay(i.as_of)}</td>
              </tr>
            ))}
          </tbody>
        </table>
      )}
      {(lines.length > shown.length || r.not_converted.length > 0) && (
        <div className="flex flex-wrap gap-1.5 border-t border-border-soft px-3 py-1.5">
          {lines.length > shown.length && <Chip>+{lines.length - shown.length} more</Chip>}
          {r.not_converted.map((i) => (
            <Chip key={i.id} tone="text-warn">{i.name} · {money(i.amount, i.currency)} · not in the total</Chip>
          ))}
        </div>
      )}
    </CardShell>
  );
}
