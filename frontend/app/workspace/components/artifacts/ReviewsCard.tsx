import { firmLocale, money } from "@/lib/format";

export type ReviewStatus = "overdue" | "no_record" | "due_soon" | "on_track";

export interface ReviewRecord {
  id: string;
  held_on: string;
  covered: string[];
  not_covered: string[];
  notes: string | null;
  meeting_id: string | null;
  recorded_by: string;
  recorded_by_role: string;
}

export interface ReviewHousehold {
  household_id: string;
  household: string;
  status: ReviewStatus;
  cadence_months: number;
  cadence: string;
  cadence_set: boolean;
  last_review: ReviewRecord | null;
  next_due: string | null;
  days_to_due: number | null;
  booked: { meeting_id: string; title: string; on: string } | null;
  unrecorded_meeting: { meeting_id: string; title: string; held_on: string } | null;
  fees_since_review: Record<string, number>;
  history: ReviewRecord[];
}

const STATUS: Record<ReviewStatus, [string, string]> = {
  overdue: ["overdue", "text-crit"],
  no_record: ["no review on record", "text-warn"],
  due_soon: ["due soon", "text-warn"],
  on_track: ["on track", "text-ok"],
};

export function ReviewStatusChip({ status }: { status: ReviewStatus }) {
  const [text, tone] = STATUS[status] ?? [status, "text-ink-muted"];
  return <span className={`chip ${tone}`} data-testid={`review-status-${status}`}>{text}</span>;
}

export function reviewDay(iso: string | null | undefined): string {
  if (!iso) return "—";
  return new Date(`${iso}T00:00:00`).toLocaleDateString(firmLocale(), { day: "numeric", month: "short", year: "numeric" });
}

export function reviewFees(fees: Record<string, number>): string {
  const rows = Object.entries(fees ?? {});
  return rows.length ? rows.map(([ccy, amount]) => money(amount, ccy)).join(" · ") : "—";
}

export const topicText = (t: string) => t.replace(/_/g, " ");

function Rows({ rows }: { rows: ReviewHousehold[] }) {
  return (
    <table className="w-full text-sm">
      <thead>
        <tr className="text-left text-[10px] uppercase tracking-wide text-ink-muted">
          <th className="px-3 py-1.5 font-medium">Client</th>
          <th className="px-3 py-1.5 font-medium">Last review</th>
          <th className="px-3 py-1.5 font-medium">Next due</th>
          <th className="px-3 py-1.5 text-right font-medium">Fees since</th>
          <th className="px-3 py-1.5 text-right font-medium">Status</th>
        </tr>
      </thead>
      <tbody className="divide-y divide-border-soft">
        {rows.map((h) => (
          <tr key={h.household_id}>
            <td className="px-3 py-1.5">
              <div className="text-ink">{h.household}</div>
              <div className="text-xs text-ink-muted">{h.cadence}</div>
            </td>
            <td className="px-3 py-1.5 font-mono text-xs text-ink-muted">{reviewDay(h.last_review?.held_on)}</td>
            <td className="px-3 py-1.5 font-mono text-xs text-ink">{reviewDay(h.next_due)}</td>
            <td className="px-3 py-1.5 text-right font-mono text-xs tabular-nums text-ink-muted">{reviewFees(h.fees_since_review)}</td>
            <td className="px-3 py-1.5 text-right"><ReviewStatusChip status={h.status} /></td>
          </tr>
        ))}
      </tbody>
    </table>
  );
}

const SHOWN = 8;

export function ReviewsCard({ result }: { result: any }) {
  if (Array.isArray(result?.households)) {
    const s = result.summary;
    const homes: ReviewHousehold[] = result.households;
    const shown = homes.slice(0, SHOWN);
    return (
      <div className="overflow-hidden rounded-lg border border-border bg-surface" data-testid="review-card">
        <div className="flex flex-wrap items-center justify-between gap-2 border-b border-border-soft px-3 py-2">
          <div className="text-sm font-medium text-ink">Client reviews</div>
          <div className="flex flex-wrap gap-1.5">
            <span className="chip text-crit">{s.overdue} overdue</span>
            <span className="chip text-warn">{s.due_soon} due soon</span>
            <span className="chip text-ok">{s.on_track} on track</span>
            <span className="chip text-ink-muted">{s.no_record} no record</span>
          </div>
        </div>
        <Rows rows={shown} />
        {homes.length > shown.length && (
          <div className="border-t border-border-soft px-3 py-2"><span className="chip text-ink-muted">+{homes.length - shown.length} more</span></div>
        )}
      </div>
    );
  }
  const h = result as ReviewHousehold;
  const last = h.last_review;
  return (
    <div className="overflow-hidden rounded-lg border border-border bg-surface" data-testid="review-card">
      <div className="flex flex-wrap items-center justify-between gap-2 border-b border-border-soft px-3 py-2">
        <div className="text-sm font-medium text-ink">Client review · {h.household}</div>
        <div className="flex flex-wrap gap-1.5">
          <span className="chip text-ink-muted">{h.cadence}</span>
          <ReviewStatusChip status={h.status} />
        </div>
      </div>
      <Rows rows={[h]} />
      <div className="flex flex-wrap gap-1.5 border-t border-border-soft px-3 py-2">
        {last?.covered.map((t) => <span key={t} className="chip text-ink-muted">{topicText(t)}</span>)}
        {last && !last.covered.length && <span className="chip text-warn">coverage not recorded</span>}
        {h.booked && <span className="chip text-ink">booked {reviewDay(h.booked.on)}</span>}
        {h.unrecorded_meeting && <span className="chip text-warn" title={h.unrecorded_meeting.title}>meeting {reviewDay(h.unrecorded_meeting.held_on)} not recorded</span>}
      </div>
    </div>
  );
}
