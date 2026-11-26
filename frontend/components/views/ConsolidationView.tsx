"use client";

import { Card, Empty, ErrorState, SkeletonList } from "@/components/ui";
import { useApi } from "@/lib/hooks";
import { money } from "@/lib/format";
import { withRef } from "@/lib/refs";
import {
  ConsolidationChips, ExitSchedule, RedeemInstead, RequestChips, decisionTone, statusTone,
} from "@/app/workspace/components/artifacts/ConsolidationCard";
import { reviewDay } from "@/app/workspace/components/artifacts/ReviewsCard";
import { AllClients, ViewHeader } from "./ViewHeader";
import { ViewLink, useViewNav } from "./ViewNav";

const th = "px-3 py-2 font-medium";
const head = "text-left text-[10px] uppercase tracking-wide text-ink-muted";

/** The consolidation workbench, India (B-389): every household with holdings elsewhere, or one household's. Beside the
 * conversation each action is Astra's tool behind its Confirm card; nothing here moves a holding. */
export function ConsolidationView({ household = null }: { household?: string | null }) {
  return household ? <One household={household} /> : <All />;
}

function All() {
  const { data, loading, error, refetch } = useApi<any>("/api/consolidation", []);
  if (error) return <div className="view-container"><Card><ErrorState what="consolidation" message={error} onRetry={refetch} /></Card></div>;
  if (loading || !data) return <div className="view-container"><SkeletonList count={4} /></div>;
  if (!data.applies) return <div className="view-container"><ViewHeader title="Consolidation" /><Empty>Not an Indian firm.</Empty></div>;
  return (
    <div className="view-container">
      <ViewHeader title="Consolidation" />
      <div className="mb-4 grid grid-cols-2 gap-3 sm:grid-cols-4" data-testid="consolidation-firm-summary">
        {[["Households", data.summary.households], ["To classify", data.summary.to_classify], ["Open requests", data.summary.open_items], ["Rejected", data.summary.rejected]].map(([label, n]) => (
          <Card key={label as string}><div className="text-[10px] uppercase tracking-wide text-ink-muted">{label}</div><div className="font-mono text-xl text-ink">{n as number}</div></Card>
        ))}
      </div>
      {data.households.length === 0 ? <Empty>No holdings elsewhere on file.</Empty> : (
        <Card className="overflow-hidden p-0"><div className="overflow-x-auto"><table className="w-full text-sm" data-testid="consolidation-households">
          <thead><tr className={head}>
            <th className={th}>Client</th><th className={`${th} text-right`}>Stated value</th><th className={th}>Folios</th><th className={th}>Fund houses</th>
            <th className={th}>To classify</th><th className={th}>Open</th><th className={th}>Confirmed</th><th className={th}>Rejected</th><th className={th}>Exits</th>
          </tr></thead>
          <tbody className="divide-y divide-border-soft">
            {data.households.map((h: any) => (
              <tr key={h.household_id}>
                <td className="px-3 py-2"><ViewLink href={`/studio/consolidation?household=${h.household_id}`} className="text-ink underline-offset-2 hover:underline" data-testid="consolidation-open">{h.household}</ViewLink></td>
                <td className="px-3 py-2 text-right font-mono text-xs">{money(h.value, "INR")}</td>
                <td className="px-3 py-2 font-mono text-xs">{h.folios}</td>
                <td className="px-3 py-2 font-mono text-xs">{h.fund_houses}</td>
                <td className="px-3 py-2 font-mono text-xs">{h.classified.unclassified}</td>
                <td className="px-3 py-2 font-mono text-xs">{h.open_items}</td>
                <td className="px-3 py-2 font-mono text-xs">{h.confirmed}</td>
                <td className="px-3 py-2 font-mono text-xs">{h.rejected > 0 ? <span className="chip text-crit">{h.rejected}</span> : 0}</td>
                <td className="px-3 py-2 font-mono text-xs">{h.exits_scheduled}</td>
              </tr>
            ))}
          </tbody>
        </table></div></Card>
      )}
    </div>
  );
}

function One({ household }: { household: string }) {
  const nav = useViewNav();
  const { data, loading, error, refetch } = useApi<any>(`/api/consolidation/households/${household}`, [household]);
  if (error) return <div className="view-container"><Card><ErrorState what="consolidation" message={error} onRetry={refetch} /></Card></div>;
  if (loading || !data) return <div className="view-container"><SkeletonList count={4} /></div>;
  if (!data.applies) return <div className="view-container"><ViewHeader title="Consolidation" /><Empty>Not an Indian firm.</Empty></div>;
  // In the pane a button hands the conversation a sentence naming the exact record; the conversation asks for the rest.
  const say = (sentence: string) => nav.say?.(withRef(sentence, "household", data.household_id));

  const action = (h: any) => {
    if (!nav.say) return null;
    const what = `${h.name} at ${h.institution}${h.account_label ? `, ${h.account_label}` : ""} (line ${h.line_id}) for ${data.household}`;
    return (
      <div className="flex justify-end gap-1.5">
        {data.can_classify && (
          <button type="button" className="btn-outline text-xs" data-testid="consolidation-classify"
            onClick={() => say(`Classify ${what}. Ask me whether it is keep and re-map, transfer or exit, and my reason.`)}>Classify</button>
        )}
        {data.can_record_facts && !h.redeem.assessed && (
          <button type="button" className="btn-outline text-xs" data-testid="consolidation-facts"
            onClick={() => say(`Record the cost, acquisition dates and tax class of ${what}. Ask me for them and the document.`)}>Add cost</button>
        )}
        {data.can_track && h.decision && !h.item && (
          <button type="button" className="btn-outline text-xs" data-testid="consolidation-request-open"
            onClick={() => say(`Record a consolidation request for ${what}, which is classified ${h.decision.label.toLowerCase()}. Ask me for its details and the document.`)}>Request</button>
        )}
      </div>
    );
  };

  return (
    <div className="view-container">
      <ViewHeader title={`${data.household}: consolidation`} meta={<><ConsolidationChips summary={data.summary} /><span className="chip text-ink-muted">estimate</span></>}
        actions={<AllClients href="/studio/consolidation" />} />
      <Card className="mb-4 space-y-2"><RequestChips summary={data.summary} /><RedeemInstead trap={data.redeem_instead} /></Card>

      {data.fund_houses.length > 0 && (
        <Card className="mb-4 overflow-hidden p-0"><div className="overflow-x-auto"><table className="w-full text-sm" data-testid="consolidation-fund-houses">
          <thead><tr className={head}><th className={th}>Fund house</th><th className={th}>Folios</th><th className={th}>Holdings</th><th className={th}>To classify</th><th className={`${th} text-right`}>Stated value</th></tr></thead>
          <tbody className="divide-y divide-border-soft">
            {data.fund_houses.map((f: any) => (
              <tr key={f.name}>
                <td className="px-3 py-2 text-ink">{f.name}</td><td className="px-3 py-2 font-mono text-xs">{f.folios}</td>
                <td className="px-3 py-2 font-mono text-xs">{f.lines}</td><td className="px-3 py-2 font-mono text-xs">{f.unclassified}</td>
                <td className="px-3 py-2 text-right font-mono text-xs">{money(f.value, "INR")}</td>
              </tr>
            ))}
          </tbody>
        </table></div></Card>
      )}

      {data.holdings.length === 0 ? <Empty>No holdings elsewhere on file.</Empty> : (
        <Card className="mb-4 overflow-hidden p-0"><div className="overflow-x-auto"><table className="w-full text-sm" data-testid="consolidation-holdings">
          <thead><tr className={head}>
            <th className={th}>Holding</th><th className={th}>Account</th><th className={`${th} text-right`}>Value</th><th className={th}>Classified</th>
            <th className={`${th} text-right`}>If redeemed today</th><th className={th}>Request</th>{nav.say && <th className={th} />}
          </tr></thead>
          <tbody className="divide-y divide-border-soft">
            {data.holdings.map((h: any) => (
              <tr key={h.line_id} data-testid="consolidation-holding">
                <td className="px-3 py-2"><div className="text-ink">{h.name}</div><div className="text-xs text-ink-muted">{h.owner} · stated {reviewDay(h.value_on)}</div></td>
                <td className="px-3 py-2 text-xs text-ink-muted"><div>{h.institution}</div><div>{h.account_label ?? "—"}{h.distributor_code ? ` · ${h.distributor_code}` : ""}</div></td>
                <td className="px-3 py-2 text-right font-mono text-xs">{money(h.value, h.currency)}</td>
                <td className="px-3 py-2">{h.decision
                  ? <span className={`chip ${decisionTone[h.decision.decision] ?? ""}`} title={`${h.decision.reason} (${h.decision.by}, ${h.decision.on})`}>{h.decision.label}</span>
                  : <span className="chip text-ink-muted">to classify</span>}</td>
                <td className="px-3 py-2 text-right text-xs">
                  {h.redeem.assessed ? (
                    <div className="font-mono">
                      <div>gain {money(h.redeem.gain, "INR")}</div>
                      <div className="text-ink-muted">{h.redeem.tax != null ? `tax ${money(h.redeem.tax, "INR")}` : "tax not assessed"}{h.redeem.exit_load ? ` · load ${money(h.redeem.exit_load, "INR")}` : ""}</div>
                    </div>
                  ) : <span className="chip text-warn" title={(h.redeem.missing ?? []).join(", ")}>not assessed</span>}
                  {h.redeem.grandfather_missing && <span className="chip text-warn" title="No 31 January 2018 value on file for a lot bought before 1 February 2018">no 2018 value</span>}
                </td>
                <td className="px-3 py-2">{h.item ? <span className={`chip ${statusTone[h.item.status] ?? ""}`} title={h.item.kind_label}>{h.item.status_label}</span> : <span className="text-ink-muted">—</span>}</td>
                {nav.say && <td className="px-3 py-2">{action(h)}</td>}
              </tr>
            ))}
          </tbody>
        </table></div></Card>
      )}

      {data.items.length > 0 && (
        <Card className="mb-4 overflow-hidden p-0"><div className="overflow-x-auto"><table className="w-full text-sm" data-testid="consolidation-requests">
          <thead><tr className={head}>
            <th className={th}>Request</th><th className={th}>Holdings</th><th className={th}>Status</th><th className={th}>Days</th><th className={th}>Details</th>{nav.say && data.can_track && <th className={th} />}
          </tr></thead>
          <tbody className="divide-y divide-border-soft">
            {data.items.map((i: any) => (
              <tr key={i.item_id}>
                <td className="px-3 py-2"><div className="text-ink">{i.kind_label}</div><div className="text-xs text-ink-muted">{i.institution}{i.account_label ? ` · ${i.account_label}` : ""}</div></td>
                <td className="px-3 py-2 text-xs text-ink-muted">{i.lines.map((l: any) => <div key={l.line_id}>{l.name}</div>)}</td>
                <td className="px-3 py-2"><div className="flex flex-wrap gap-1">
                  <span className={`chip ${statusTone[i.status] ?? ""}`} title={i.steps.map((s: any) => `${s.on} ${s.label}: ${s.evidence}`).join("\n")}>{i.status_label}</span>
                  {i.last_on && <span className="chip text-ink-muted tabular-nums">{reviewDay(i.last_on)}</span>}
                </div></td>
                <td className="px-3 py-2 font-mono text-xs text-ink-muted">{i.days}</td>
                <td className="px-3 py-2"><div className="flex flex-wrap gap-1">
                  {i.details.to_code && <span className="chip text-ink-muted">{i.details.from_code} to {i.details.to_code}</span>}
                  {i.details.reason_code && <span className="chip text-ink-muted" title={i.details.transfer_kind_label}>code {i.details.reason_code}</span>}
                  {i.details.manager_from && <span className="chip text-ink-muted">{i.details.manager_from} to {i.details.manager_to}</span>}
                  {i.details.to_account && <span className="chip text-ink-muted">into {i.details.to_account}</span>}
                  {i.stamp_duty && (i.stamp_duty.estimate == null ? <span className="chip text-warn">stamp duty not assessed</span>
                    : <span className="chip tabular-nums">stamp duty {money(i.stamp_duty.estimate, "INR")}</span>)}
                  {i.reference && <span className="chip text-ink-muted">{i.reference}</span>}
                  {i.reject_reason && <span className="chip text-crit" title={i.reject_reason}>reason on file</span>}
                  {i.classification_changed && <span className="chip text-warn">classification changed</span>}
                  {i.old_line_listed && <span className="chip text-warn">old line still listed</span>}
                  {i.regular_plan && <span className="chip text-ink-muted">regular plan stays</span>}
                </div></td>
                {nav.say && data.can_track && (
                  <td className="px-3 py-2 text-right">{i.open && (
                    <button type="button" className="btn-outline text-xs" data-testid="consolidation-step"
                      onClick={() => say(`Record the next step of the ${i.kind_label.toLowerCase()} at ${i.institution}${i.account_label ? `, ${i.account_label}` : ""} (request ${i.item_id}) for ${data.household}. It is now ${i.status_label.toLowerCase()}. Ask me what happened and for the document.`)}>Record step</button>
                  )}</td>
                )}
              </tr>
            ))}
          </tbody>
        </table></div></Card>
      )}

      {data.schedule.length > 0 && <Card className="mb-4 overflow-hidden p-0"><ExitSchedule groups={data.schedule} /></Card>}

      <div className="flex flex-wrap gap-1.5" data-testid="consolidation-basis">
        {data.law.map((f: any) => (
          <span key={f.key} className="chip text-ink-muted" title={`${f.label}. ${f.source} (as of ${f.as_of})`}>
            {f.key.replace(/^in_/, "").replace(/_/g, " ")} · {f.counsel?.label ?? "Not reviewed"}
          </span>
        ))}
        <span className="chip text-warn" title={data.not_modelled.join("\n")}>{data.not_modelled.length} not modelled</span>
        {data.summary.savings_not_listed > 0 && <span className="chip text-ink-muted">{data.summary.savings_not_listed} savings accounts not listed</span>}
      </div>
    </div>
  );
}
