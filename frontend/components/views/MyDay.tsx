import { StatTile } from "@/components/ui";
import { money } from "@/lib/format";

/** `/api/advisers/my-day` and the `read_my_day` tool (backend `aurea_core/advisers.day`). */
export interface MyDay {
  scope: "yours" | "firm";
  currency: string;
  households: number;
  unassigned: number;
  waiting: { count: number; oldest_days: number | null; items: { id: string; title: string; agent_key: string }[] };
  due: { count: number; overdue: number; reviews: number; tasks: number; items: { kind: string; what: string; on: string; overdue: boolean }[] };
  money: { count: number; payments: number; deposits_held: number; value: number; items: { kind: string; what: string; amount: number }[] };
  meetings: { count: number; briefed: number; items: { id: string; title: string; household: string | null; on: string; briefed: boolean }[] };
}

const plural = (n: number, one: string) => `${n} ${one}${n === 1 ? "" : "s"}`;

/** Which households the four figures cover. */
export function scopeLabel(d: MyDay): string {
  if (d.scope === "firm") return `Whole firm · ${plural(d.households, "household")}`;
  if (!d.households) return `No clients assigned to you · ${d.unassigned} unassigned at the firm`;
  return `Your ${plural(d.households, "household")}`;
}

/** The four figures at the top of the cockpit. Each is a count from what is on file; nothing is estimated. */
export function MyDayTiles({ day }: { day: MyDay | null | undefined }) {
  const d = day;
  return (
    <div className="grid grid-cols-2 gap-3 cq-md:grid-cols-4" data-testid="my-day-tiles">
      <StatTile label="Waiting on you" value={d ? d.waiting.count : "—"} accent={d?.waiting.count ? "warn" : undefined}
        hint={d?.waiting.oldest_days != null ? `oldest ${d.waiting.oldest_days}d` : undefined} />
      <StatTile label="Due this week" value={d ? d.due.count : "—"} accent={d?.due.overdue ? "crit" : d?.due.count ? "warn" : undefined}
        hint={d ? `${d.due.overdue} overdue · ${plural(d.due.reviews, "review")} · ${plural(d.due.tasks, "task")}` : undefined} />
      <StatTile label="Money waiting" value={d ? d.money.count : "—"} accent={d?.money.count ? "info" : undefined}
        hint={d && d.money.count ? `${money(d.money.value, d.currency)} · ${plural(d.money.payments, "payment")} · ${d.money.deposits_held} held` : undefined} />
      <StatTile label="Meetings this week" value={d ? d.meetings.count : "—"}
        accent={d && d.meetings.count > d.meetings.briefed ? "warn" : undefined}
        hint={d && d.meetings.count ? `${d.meetings.briefed} of ${d.meetings.count} briefed` : undefined} />
    </div>
  );
}
