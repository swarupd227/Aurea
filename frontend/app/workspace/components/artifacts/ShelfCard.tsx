import { money } from "@/lib/format";

export interface Product {
  symbol: string;
  name: string;
  asset_class: string;
  status: "approved" | "watch" | "closed" | "not_reviewed";
  mandates: number;
  value_held: number;
  reviewed_on: string | null;
  next_review_on: string | null;
  review_overdue: boolean;
  minimum_class: string | null;
  family: string | null;
  share_class: string | null;
  expense_ratio: number | null;
  diversified: boolean;
  dearer_class: boolean;
  rationale: string | null;
  document: string | null;
}

const TONE: Record<Product["status"], string> = { approved: "text-ok", watch: "text-warn", closed: "text-crit", not_reviewed: "text-ink-muted" };

export function ProductChips({ product: p }: { product: Product }) {
  return (
    <div className="flex flex-wrap gap-1.5" data-testid="product-chips">
      <span className={`chip ${TONE[p.status]}`} title={p.rationale ?? undefined}>{p.status === "not_reviewed" ? "no decision" : p.status}</span>
      {p.status === "closed" && p.mandates > 0 && <span className="chip text-crit">held</span>}
      {p.review_overdue && <span className="chip text-warn">review overdue</span>}
      {p.minimum_class && <span className="chip text-ink-muted">{p.minimum_class.replace("_", " ")} only</span>}
      {p.dearer_class && <span className="chip text-warn">dearer class</span>}
    </div>
  );
}

const SHOWN = 8;

export function ShelfCard({ result }: { result: any }) {
  const rows: Product[] = result.products;
  const s = result.summary;
  return (
    <div className="overflow-hidden rounded-lg border border-border bg-surface" data-testid="shelf-card">
      <div className="flex flex-wrap items-center justify-between gap-2 border-b border-border-soft px-3 py-2">
        <div className="text-sm font-medium text-ink">Product shelf</div>
        <div className="flex flex-wrap gap-1.5">
          <span className="chip text-ok">{s.approved} approved</span>
          <span className="chip text-warn">{s.watch} watch</span>
          <span className="chip text-crit">{s.closed} closed</span>
          <span className="chip text-ink-muted">{s.not_reviewed} no decision</span>
          <span className="chip text-ink-muted">approved only {result.approved_products_only ? "on" : "off"}</span>
        </div>
      </div>
      <div className="overflow-x-auto">
        <table className="w-full text-sm">
          <tbody className="divide-y divide-border-soft">
            {rows.slice(0, SHOWN).map((p) => (
              <tr key={p.symbol}>
                <td className="px-3 py-1.5 font-mono text-xs text-ink">{p.symbol}</td>
                <td className="px-3 py-1.5 text-right font-mono text-xs tabular-nums text-ink-muted">{money(p.value_held)}</td>
                <td className="px-3 py-1.5"><ProductChips product={p} /></td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
      {rows.length > SHOWN && <div className="border-t border-border-soft px-3 py-2"><span className="chip text-ink-muted">+{rows.length - SHOWN} more</span></div>}
    </div>
  );
}
