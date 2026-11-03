import { MyDayTiles, scopeLabel, type MyDay } from "@/components/views/MyDay";
import { money } from "@/lib/format";
import { reviewDay } from "./ReviewsCard";

/** read_my_day: the cockpit's four figures, and the first few items behind each. */
export function MyDayCard({ result }: { result: MyDay }) {
  const rows: [string, string, string, string][] = [
    ...result.waiting.items.map((i) => ["Waiting", i.title, "", ""] as [string, string, string, string]),
    ...result.due.items.map((i) => ["Due", i.what, reviewDay(i.on), i.overdue ? "overdue" : ""] as [string, string, string, string]),
    ...result.money.items.map((i) => ["Money", i.what, money(i.amount, result.currency), ""] as [string, string, string, string]),
    ...result.meetings.items.map((i) => ["Meeting", i.household && !i.title.includes(i.household) ? `${i.title} · ${i.household}` : i.title, reviewDay(i.on), i.briefed ? "briefed" : "no brief"] as [string, string, string, string]),
  ];
  return (
    <div className="overflow-hidden rounded-lg border border-border bg-surface" data-testid="my-day-card">
      <div className="flex flex-wrap items-center justify-between gap-2 border-b border-border-soft px-3 py-2">
        <span className="text-sm font-medium text-ink">Your day</span>
        <span className="chip">{scopeLabel(result)}</span>
      </div>
      <div className="p-3"><MyDayTiles day={result} /></div>
      {rows.length > 0 && (
        <table className="w-full border-t border-border-soft text-xs"><tbody className="divide-y divide-border-soft">
          {rows.map(([kind, what, when, tag], i) => (
            <tr key={i}>
              <td className="px-3 py-1.5 text-ink-muted">{kind}</td>
              <td className="px-3 py-1.5 text-ink">{what}</td>
              <td className="px-3 py-1.5 font-mono text-ink-soft">{when}</td>
              <td className="px-3 py-1.5">{tag && <span className={`chip ${tag === "overdue" || tag === "no brief" ? "text-warn" : "text-ok"}`}>{tag}</span>}</td>
            </tr>
          ))}
        </tbody></table>
      )}
    </div>
  );
}
