import { money } from "@/lib/format";
import { reviewDay } from "./ReviewsCard";

/** The consolidation workbench, India (B-389): figures, tables and chips only. Nothing it shows has moved. */

export const decisionTone: Record<string, string> = { keep_remap: "text-ok", transfer: "text-info", exit: "text-warn" };
export const statusTone: Record<string, string> = {
  requested: "text-ink-muted", consented: "text-ink-muted", submitted: "text-info", confirmed: "text-ok", rejected: "text-crit",
  withdrawn: "text-ink-faint", scheduled: "text-ink-muted",
};
const SHOWN = 6;

const statusWords = (counts: Record<string, number> | undefined) =>
  Object.entries(counts ?? {}).map(([status, n]) => ({ status, n }));

/** The headline counts of one household. */
export function ConsolidationChips({ summary }: { summary: any }) {
  const c = summary.classified;
  return (
    <div className="flex flex-wrap items-center gap-1.5" data-testid="consolidation-summary">
      <span className="chip text-ink-muted tabular-nums">{summary.folios} folios · {summary.fund_houses} fund houses</span>
      {summary.demat_accounts > 0 && <span className="chip text-ink-muted tabular-nums">{summary.demat_accounts} demat</span>}
      <span className="chip tabular-nums">{money(summary.value, "INR")}</span>
      <span className="chip text-ok tabular-nums">{c.keep_remap} keep</span>
      <span className="chip text-info tabular-nums">{c.transfer} transfer</span>
      <span className="chip text-warn tabular-nums">{c.exit} exit</span>
      {c.unclassified > 0 && <span className="chip text-ink-muted tabular-nums">{c.unclassified} to classify</span>}
    </div>
  );
}

/** The tracked requests by kind and where they stand. */
export function RequestChips({ summary }: { summary: any }) {
  const kinds: [string, string][] = [["code_changes", "Code changes"], ["demat_transfers", "Demat transfers"], ["pms_transitions", "PMS"], ["exits", "Exits"]];
  const rows = kinds.filter(([k]) => Object.keys(summary[k] ?? {}).length > 0);
  if (rows.length === 0) return <span className="chip text-ink-muted">no requests recorded</span>;
  return (
    <div className="flex flex-wrap items-center gap-x-4 gap-y-1.5" data-testid="consolidation-requests-counts">
      {rows.map(([key, label]) => (
        <div key={key} className="flex flex-wrap items-center gap-1.5">
          <span className="tile-label">{label}</span>
          {statusWords(summary[key]).map(({ status, n }) => (
            <span key={status} className={`chip tabular-nums ${statusTone[status] ?? ""}`}>{n} {status}</span>
          ))}
        </div>
      ))}
    </div>
  );
}

/** What redeeming and reinvesting the keep and transfer holdings would crystallise: the trap, as figures. */
export function RedeemInstead({ trap }: { trap: any }) {
  if (!trap || trap.holdings === 0) return null;
  return (
    <div className="flex flex-wrap items-center gap-1.5" data-testid="consolidation-redeem-instead">
      <span className="tile-label">Redeem instead</span>
      {trap.assessed > 0 ? (
        <>
          <span className="chip tabular-nums" title="Gain realised, estimate">gain {money(trap.gain, "INR")}</span>
          <span className="chip text-crit tabular-nums" title="Estimated capital gains tax">tax {money(trap.tax, "INR")}</span>
          {trap.exit_load_not_recorded < trap.assessed && <span className="chip tabular-nums" title="Exit load recorded from scheme documents">exit load {money(trap.exit_load, "INR")}</span>}
          {trap.exit_load_not_recorded > 0 && <span className="chip text-ink-muted tabular-nums">exit load not recorded on {trap.exit_load_not_recorded}</span>}
        </>
      ) : null}
      {trap.not_assessed > 0 && <span className="chip text-warn tabular-nums">{trap.not_assessed} not assessed</span>}
      <span className="chip text-ok" title="A code change and a transfer between one person's accounts are taken as no sale (assumed rule)">code change and own transfer: no sale</span>
    </div>
  );
}

/** One taxpayer's exits in one tax year against the gain budget. */
export function ExitSchedule({ groups, limit }: { groups: any[]; limit?: number }) {
  if (!groups?.length) return null;
  return (
    <div className="divide-y divide-border-soft" data-testid="consolidation-schedule">
      {groups.map((g) => (
        <div key={`${g.person_id ?? g.entity_id}-${g.tax_year}`} className="space-y-1.5 px-3 py-2">
          <div className="flex flex-wrap items-center gap-1.5">
            <span className="mr-auto text-sm text-ink">{g.taxpayer} · {g.tax_year}</span>
            {g.budget ? <span className="chip tabular-nums" title={g.budget.source}>budget {money(g.budget.amount, "INR")}</span>
              : <span className="chip text-warn">no gain budget on file</span>}
            {g.realised_book != null && <span className="chip text-ink-muted tabular-nums" title="Realised in the managed book this year">realised {money(g.realised_book, "INR")}</span>}
            <span className="chip tabular-nums">exits {money(g.exit_gain, "INR")}</span>
            {g.budget && (g.over > 0 ? <span className="chip text-crit tabular-nums">over by {money(g.over, "INR")}</span>
              : g.left != null && <span className="chip text-ok tabular-nums">{money(g.left, "INR")} left</span>)}
            {g.estimated_tax != null && <span className="chip tabular-nums">tax {money(g.estimated_tax, "INR")} est.</span>}
            {g.not_assessed > 0 && <span className="chip text-warn tabular-nums">{g.not_assessed} not assessed</span>}
            {(g.incomplete ?? []).map((i: string) => <span key={i} className="chip text-warn">{i.replace(/_/g, " ")} not on file</span>)}
          </div>
          <div className="overflow-x-auto">
            <table className="w-full text-xs">
              <tbody className="divide-y divide-border-soft">
                {g.rows.slice(0, limit ?? g.rows.length).map((r: any) => (
                  <tr key={`${r.line_id}-${r.planned_on}`}>
                    <td className="py-1 pr-2 text-ink" title={r.reason}>{r.name}</td>
                    <td className="py-1 pr-2 tabular-nums text-ink-muted">{reviewDay(r.planned_on)}</td>
                    <td className="py-1 pr-2"><div className="flex flex-wrap gap-1">
                      <span className={`chip ${statusTone[r.status] ?? ""}`}>{r.status_label}</span>
                      {r.overdue && <span className="chip text-warn">day passed</span>}
                      {r.product_reason_label && <span className="chip text-ink-muted" title={r.reason}>{r.product_reason_label}</span>}
                    </div></td>
                    <td className="py-1 pr-2 text-right font-mono">{r.assessed ? `gain ${money(r.gain, "INR")}` : <span className="chip text-warn" title={(r.missing ?? []).join(", ")}>not assessed</span>}</td>
                    <td className="py-1 pr-2 text-right font-mono">{r.tax != null ? `tax ${money(r.tax, "INR")}` : ""}</td>
                    <td className="py-1 text-right">
                      {r.exit_load != null && r.exit_load > 0 && <span className="chip tabular-nums">load {money(r.exit_load, "INR")}</span>}
                      {r.long_term_from && <span className="chip text-ink-muted tabular-nums" title="Some units are short-term on the planned day">long-term from {reviewDay(r.long_term_from)}</span>}
                      {r.within_budget === false && <span className="chip text-crit">over budget</span>}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </div>
      ))}
    </div>
  );
}

/** One request as a row of chips. */
function RequestRow({ item }: { item: any }) {
  const duty = item.stamp_duty;
  return (
    <div className="space-y-1 px-3 py-2" data-testid="consolidation-request">
      <div className="flex flex-wrap items-center gap-1.5">
        <span className="mr-auto text-sm text-ink">{item.kind_label} · {item.institution}{item.account_label ? ` · ${item.account_label}` : ""}</span>
        <span className={`chip ${statusTone[item.status] ?? ""}`}>{item.status_label}</span>
        <span className="chip text-ink-muted tabular-nums">{item.days} days</span>
        {item.reference && <span className="chip text-ink-muted">{item.reference}</span>}
        {item.reject_reason && <span className="chip text-crit" title={item.reject_reason}>reason on file</span>}
        {item.classification_changed && <span className="chip text-warn">classification changed</span>}
        {item.old_line_listed && <span className="chip text-warn" title="Nothing here moves a holding: the old statement line still stands">old line still listed</span>}
        {item.regular_plan && <span className="chip text-ink-muted" title="A code change does not change the plan">regular plan stays</span>}
        {item.details?.to_code && <span className="chip text-ink-muted">{item.details.from_code} to {item.details.to_code}</span>}
        {item.details?.reason_code && <span className="chip text-ink-muted" title={item.details.transfer_kind_label}>reason code {item.details.reason_code}</span>}
        {duty && (duty.estimate == null ? <span className="chip text-warn">stamp duty not assessed</span>
          : <span className="chip tabular-nums" title="Estimate from an assumed rule">stamp duty {money(duty.estimate, "INR")}{duty.stated != null ? ` · stated ${money(duty.stated, "INR")}` : ""}</span>)}
      </div>
    </div>
  );
}

function Firm({ result }: { result: any }) {
  return (
    <div className="overflow-hidden rounded-lg border border-border bg-surface" data-testid="consolidation-card">
      <div className="flex flex-wrap items-center justify-between gap-2 border-b border-border-soft px-3 py-2">
        <div className="text-sm font-medium text-ink">Consolidation</div>
        <div className="flex flex-wrap gap-1.5">
          <span className="chip tabular-nums">{result.households.length} households</span>
          {result.summary?.rejected > 0 && <span className="chip text-crit tabular-nums">{result.summary.rejected} rejected</span>}
        </div>
      </div>
      {result.households.length > 0 && (
        <table className="w-full text-xs">
          <tbody className="divide-y divide-border-soft">
            {result.households.slice(0, SHOWN).map((h: any) => (
              <tr key={h.household_id}>
                <td className="px-3 py-1.5 text-ink">{h.household}</td>
                <td className="px-3 py-1.5 text-right font-mono">{money(h.value, "INR")}</td>
                <td className="px-3 py-1.5 text-ink-muted tabular-nums">{h.folios} folios</td>
                <td className="px-3 py-1.5 tabular-nums text-ink-muted">{h.classified.unclassified} to classify</td>
                <td className="px-3 py-1.5 tabular-nums">{h.open_items} open{h.rejected > 0 ? <span className="chip text-crit">{h.rejected} rejected</span> : null}</td>
              </tr>
            ))}
          </tbody>
        </table>
      )}
    </div>
  );
}

/** What a read or a recorded step shows beside the answer. */
export function ConsolidationCard({ result }: { result: any }) {
  if (result.applies === false) {
    return <div className="rounded-lg border border-border bg-surface px-3 py-2" data-testid="consolidation-card"><span className="chip text-ink-muted">Not an Indian firm</span></div>;
  }
  if (Array.isArray(result.households)) return <Firm result={result} />;
  // A change just recorded shows the request it touched; a read shows what still waits first.
  const items: any[] = result.items ?? [];
  const touched = result.item_id ? items.filter((i) => i.item_id === result.item_id) : [];
  const waiting = [...items].filter((i) => i.open).sort((a, b) => Number(b.status === "rejected") - Number(a.status === "rejected"));
  const shown = touched.length ? touched : waiting.slice(0, SHOWN);
  return (
    <div className="overflow-hidden rounded-lg border border-border bg-surface" data-testid="consolidation-card">
      <div className="space-y-1.5 border-b border-border-soft px-3 py-2">
        <div className="flex flex-wrap items-center justify-between gap-2">
          <div className="text-sm font-medium text-ink">{result.household}, consolidation</div>
          <div className="flex flex-wrap gap-1.5">
            {result.recorded && <span className="chip text-ok">recorded</span>}
            <span className="chip text-ink-muted">estimate</span>
          </div>
        </div>
        <ConsolidationChips summary={result.summary} />
        <RequestChips summary={result.summary} />
        <RedeemInstead trap={result.redeem_instead} />
      </div>
      {shown.length > 0 && <div className="divide-y divide-border-soft border-b border-border-soft">{shown.map((i) => <RequestRow key={i.item_id} item={i} />)}</div>}
      {!touched.length && waiting.length > SHOWN && <div className="px-3 py-1.5"><span className="chip text-ink-muted">+{waiting.length - SHOWN} more open</span></div>}
      <ExitSchedule groups={result.schedule} limit={SHOWN} />
    </div>
  );
}
