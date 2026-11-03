import { money, titleCase } from "@/lib/format";
import { reviewDay } from "./ReviewsCard";
import { CardHead, CardShell, Chip, Tile, obj } from "./CanvasCardParts";

// The client's fees (`read_my_fees`): aurea_core/client_view.fees. A confirmed schedule is the rate
// agreed; a segment estimate is labelled one; with neither the rate is not on file and no figure is
// drawn in its place. `ytd_fee` is the annual fee pro-rated to this month - billed is `billed_ytd`.

export type FeeSource = "confirmed_fee_schedule" | "segment_estimate" | "not_on_file";

export interface MyFeesPayload {
  household_id: string;
  aum?: number;
  currency: string | null;
  fee_bps: number | null;
  fee_pct: number | null;
  annual_fee: number | null;
  monthly_fee?: number | null;
  ytd_fee: number | null;
  source: FeeSource;
  fee_schedule_name?: string;
  billed_ytd: number | null;
  invoices: { id: string; number: string; kind: string; status: string; period_start: string; period_end: string; amount: number; currency: string; paid: boolean }[];
  message?: string;
}

const SOURCES: FeeSource[] = ["confirmed_fee_schedule", "segment_estimate", "not_on_file"];

export function isMyFees(r: any): boolean {
  return obj(r)
    && SOURCES.includes(r.source)
    && "fee_bps" in r && "annual_fee" in r && "billed_ytd" in r
    && Array.isArray(r.invoices);
}

const SHOWN = 6;

export function MyFeesCard({ result }: { result: MyFeesPayload }) {
  const r = result;
  const ccy = r.currency ?? undefined;
  const none = r.source === "not_on_file" || r.fee_bps == null;
  const est = r.source === "segment_estimate";
  const shown = r.invoices.slice(0, SHOWN);
  return (
    <CardShell testid="my-fees-card">
      <CardHead title="Fees">
        {r.source === "confirmed_fee_schedule" && <Chip tone="text-ok">agreed schedule{r.fee_schedule_name ? ` · ${r.fee_schedule_name}` : ""}</Chip>}
        {est && <Chip tone="text-warn" testid="my-fees-estimate">estimate</Chip>}
        {none && <Chip tone="text-warn" testid="my-fees-not-on-file">no fee rate on file</Chip>}
      </CardHead>
      <div className="grid grid-cols-3 divide-x divide-border-soft border-b border-border-soft">
        <Tile label="Rate" value={none ? "—" : `${(r.fee_bps! / 100).toFixed(2)}%`} sub={none ? "not on file" : `${r.fee_bps} bps${est ? " · estimate" : ""}`}
          tone={none ? "text-ink-muted" : "text-ink"} />
        <Tile label="Annual" value={none || r.annual_fee == null ? "—" : money(r.annual_fee, ccy)} sub={none ? "not on file" : est ? "estimate" : "worked from the rate"}
          tone={none ? "text-ink-muted" : "text-ink"} />
        <Tile label="Billed this year" value={r.billed_ytd == null ? "—" : money(r.billed_ytd, ccy)} sub={r.billed_ytd == null ? "no bills issued" : "issued bills"}
          tone={r.billed_ytd == null ? "text-ink-muted" : "text-ink"} />
      </div>
      {!none && r.ytd_fee != null && (
        <div className="flex flex-wrap gap-1.5 border-b border-border-soft px-3 py-1.5">
          <Chip>accrued to date {money(r.ytd_fee, ccy)} · pro-rated{est ? " · estimate" : ""}</Chip>
        </div>
      )}
      {shown.length > 0 && (
        <table className="w-full table-fixed text-sm">
          <tbody className="divide-y divide-border-soft">
            {shown.map((i) => (
              <tr key={i.id}>
                <td className="truncate px-3 py-1.5 text-ink">{i.number}<span className="ml-1.5 text-xs text-ink-muted">{reviewDay(i.period_start)} – {reviewDay(i.period_end)}</span></td>
                <td className="w-24 px-3 py-1.5 text-right"><Chip tone={i.paid ? "text-ok" : "text-ink-muted"}>{i.paid ? "paid" : titleCase(i.status)}</Chip></td>
                <td className={`w-28 px-3 py-1.5 text-right font-mono text-xs tabular-nums ${i.amount < 0 ? "text-ok" : "text-ink"}`}>{money(i.amount, i.currency)}</td>
              </tr>
            ))}
          </tbody>
        </table>
      )}
      {r.invoices.length > shown.length && (
        <div className="border-t border-border-soft px-3 py-1.5"><Chip>+{r.invoices.length - shown.length} more</Chip></div>
      )}
    </CardShell>
  );
}
