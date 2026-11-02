import { money } from "@/lib/format";
import { reviewDay } from "./ReviewsCard";

export interface ExitAccount {
  account_id: string;
  name: string;
  custodian: string | null;
  status: "active" | "closed";
  registration_type: string | null;
  value_then: number | null;
  value_left: number;
  unpriced: string[];
  moved_share: number;
  beneficiaries: { name: string; percentage: number; class: string; paid: boolean }[];
  beneficiary_status: "not_needed" | "survivor" | "estate" | "complete" | "incomplete";
  rmd: { amount: number } | null;
  transfers: { id: string; to: string; stays_at_firm: boolean; share: number; date: string; value: number; basis: string; document: string }[];
}

export interface ClientExit {
  id: string;
  kind: "departure" | "death";
  status: "open" | "complete" | "cancelled";
  household_id: string;
  household: string;
  person: string | null;
  event_date: string;
  reason: string | null;
  receiving_institution: string | null;
  document: string;
  executor: string | null;
  value_then: number | null;
  book_complete: boolean | null;
  accounts: ExitAccount[];
  stayed_at_firm: number;
  left_firm: number;
  checks: {
    accounts_open: number;
    accounts: number;
    open_orders: number;
    open_proposals: number;
    consents_waiting: number;
    billed_through: string | null;
    unbilled_days: number | null;
    trustee_of: string[];
    rmd_due: string[];
    beneficiaries_incomplete: string[];
    through_estate: string[];
    no_executor: boolean;
  };
}

export const whoFor = (e: ClientExit) => (e.kind === "death" ? e.person ?? e.household : e.household);

export function ExitChips({ exit: e }: { exit: ClientExit }) {
  const c = e.checks;
  return (
    <div className="flex flex-wrap gap-1.5" data-testid="exit-checks">
      <span className="chip text-ink-muted">{e.kind === "death" ? "estate" : "departure"}</span>
      {e.status !== "open" && <span className="chip text-ink-muted">{e.status}</span>}
      {e.status === "open" && <span className={`chip ${c.accounts_open ? "text-warn" : "text-ok"}`}>{c.accounts_open} of {c.accounts} accounts open</span>}
      {c.no_executor && <span className="chip text-crit" data-testid="exit-no-executor">no executor</span>}
      {e.book_complete === false && <span className="chip text-warn">value incomplete</span>}
      {c.open_orders > 0 && <span className="chip text-crit">{c.open_orders} open orders</span>}
      {c.open_proposals > 0 && <span className="chip text-warn">{c.open_proposals} open proposals</span>}
      {c.consents_waiting > 0 && <span className="chip text-warn">{c.consents_waiting} awaiting consent</span>}
      {c.beneficiaries_incomplete.length > 0 && <span className="chip text-crit">{c.beneficiaries_incomplete.length} designation incomplete</span>}
      {c.through_estate.length > 0 && <span className="chip text-warn">{c.through_estate.length} through the estate</span>}
      {c.rmd_due.length > 0 && <span className="chip text-warn">RMD due {c.rmd_due.length}</span>}
      {c.trustee_of.length > 0 && <span className="chip text-warn" title={c.trustee_of.join(", ")}>trustee to replace {c.trustee_of.length}</span>}
      {c.unbilled_days != null && <span className="chip text-ink-muted" title={`billed through ${c.billed_through}`}>{c.unbilled_days} days unbilled</span>}
    </div>
  );
}

const SHOWN = 8;

export function ExitsCard({ result }: { result: any }) {
  const rows: ClientExit[] = Array.isArray(result?.exits) ? result.exits : [result];
  const shown = rows.slice(0, SHOWN);
  return (
    <div className="overflow-hidden rounded-lg border border-border bg-surface" data-testid="exits-card">
      <div className="flex flex-wrap items-center justify-between gap-2 border-b border-border-soft px-3 py-2">
        <div className="text-sm font-medium text-ink">Departures and estates</div>
        {result?.summary && (
          <div className="flex flex-wrap gap-1.5">
            <span className="chip text-ink-muted">{result.summary.departures} leaving</span>
            <span className="chip text-ink-muted">{result.summary.estates} estates</span>
            <span className="chip text-ok">{money(result.summary.stayed_at_firm)} stayed</span>
            <span className="chip text-ink-muted">{money(result.summary.left_firm)} left</span>
          </div>
        )}
      </div>
      {shown.length === 0 ? (
        <div className="px-3 py-2"><span className="chip text-ink-muted">none on file</span></div>
      ) : (
        <div className="overflow-x-auto">
          <table className="w-full text-sm">
            <thead>
              <tr className="text-left text-[10px] uppercase tracking-wide text-ink-muted">
                <th className="px-3 py-1.5 font-medium">Client</th>
                <th className="px-3 py-1.5 font-medium">Date</th>
                <th className="px-3 py-1.5 text-right font-medium">Value then</th>
                <th className="px-3 py-1.5 font-medium">Status</th>
              </tr>
            </thead>
            <tbody className="divide-y divide-border-soft">
              {shown.map((e) => (
                <tr key={e.id}>
                  <td className="px-3 py-1.5 text-ink">{whoFor(e)}</td>
                  <td className="px-3 py-1.5 font-mono text-xs text-ink-muted">{reviewDay(e.event_date)}</td>
                  <td className="px-3 py-1.5 text-right font-mono text-xs tabular-nums text-ink-muted">{e.value_then != null ? money(e.value_then) : "—"}</td>
                  <td className="px-3 py-1.5"><ExitChips exit={e} /></td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
      {rows.length > shown.length && (
        <div className="border-t border-border-soft px-3 py-2"><span className="chip text-ink-muted">+{rows.length - shown.length} more</span></div>
      )}
    </div>
  );
}
