"use client";

import { money } from "@/lib/format";
import { reviewDay } from "./ReviewsCard";

// India money in and bank rules (B-388): the mandate queue, credits by Indian rails, registered banks and their cooling-off, payer
// exceptions, inward remittances and the payer check. Figures and chips only: the agent's message carries the sentences, and every
// reason and source is a chip's title.

const rupees = (v: number) => money(v, "INR");
const QUEUE_TONE: Record<string, string> = { requested: "text-warn", unconfirmed: "text-warn", none: "text-warn", paused: "text-warn", over_limit: "text-warn" };

/** Everything that would let a SIP die silently, with its age, owner and task. */
export function MandateQueueRows({ queue }: { queue: any[] }) {
  return (
    <div className="divide-y divide-border-soft" data-testid="mandate-queue">
      {queue.map((e) => (
        <div key={`${e.account_id}-${e.state}-${e.reference ?? e.sips?.[0]?.sip_id}`} className="flex flex-wrap items-center gap-1.5 px-3 py-1.5" data-testid="mandate-queue-item" title={e.reason ?? e.title}>
          <span className="mr-auto min-w-0 text-sm text-ink">{e.account}{e.reference ? <span className="ml-1.5 text-xs text-ink-muted">{e.reference}</span> : null}</span>
          {e.rail_label && <span className="chip text-ink-muted">{e.rail_label}</span>}
          {e.monthly > 0 && <span className="chip tabular-nums text-ink-muted" title={e.sips.map((s: any) => s.fund).join(", ")}>{rupees(e.monthly)} a month</span>}
          <span className={`chip ${QUEUE_TONE[e.state] ?? "text-crit"}`} data-testid="mandate-queue-state">{e.label}</span>
          <span className={`chip tabular-nums ${e.overdue ? "text-crit" : "text-ink-muted"}`} title={`Since ${e.since}; chased after ${e.chase_after_days} days`}>{e.age_days} d</span>
          <span className={`chip ${e.owner ? "text-ink-muted" : "text-warn"}`}>{e.owner ?? "No owner"}</span>
          <span className={`chip ${e.task === "open" ? "text-ink" : "text-ink-muted"}`}>{e.task ? `Task ${e.task}` : "No task yet"}</span>
        </div>
      ))}
    </div>
  );
}

function Heading({ children }: { children: React.ReactNode }) {
  return <div className="tile-label border-t border-border-soft px-3 pb-0.5 pt-2">{children}</div>;
}

export function CreditRows({ deposits }: { deposits: any[] }) {
  return (
    <div className="divide-y divide-border-soft" data-testid="india-credits">
      {deposits.map((d) => (
        <div key={d.id} className="flex flex-wrap items-center gap-1.5 px-3 py-1.5" data-testid="india-credit">
          <span className="mr-auto min-w-0 text-sm text-ink">{d.client}<span className="ml-1.5 text-xs text-ink-muted">{d.account}</span></span>
          <span className="chip text-ink-muted" title={d.reference}>{d.rail_label ?? String(d.method).toUpperCase()}</span>
          <span className="chip tabular-nums text-ink">{rupees(d.amount)}</span>
          <span className="chip tabular-nums text-ink-muted">{reviewDay(d.received_on)}</span>
          {d.basis && <span className={`chip ${d.basis === "investor" ? "text-ink-muted" : "text-warn"}`} title={`${d.payer}, ending ${d.payer_last4}`} data-testid="india-credit-basis">{d.basis_label}</span>}
          {d.returned_on ? <span className="chip text-crit" title={d.return_reason ?? undefined}>Returned</span>
            : d.on_hold ? <span className="chip text-warn">held to {reviewDay(d.hold_until)}</span> : null}
        </div>
      ))}
    </div>
  );
}

export function BankRows({ banks }: { banks: any[] }) {
  return (
    <div className="divide-y divide-border-soft" data-testid="india-banks">
      {banks.map((b) => (
        <div key={b.id} className="flex flex-wrap items-center gap-1.5 px-3 py-1.5" data-testid="india-bank" title={b.said ?? undefined}>
          <span className="mr-auto min-w-0 text-sm text-ink">{b.client}<span className="ml-1.5 text-xs text-ink-muted">{b.owner}</span></span>
          <span className="chip text-ink-muted">{b.bank} ending {b.last4}</span>
          {!b.registered ? <span className="chip text-warn" data-testid="india-bank-state">{b.status === "verified" ? "Not paid to" : b.status}</span>
            : b.payable ? <span className="chip text-ok" data-testid="india-bank-state">{b.changed ? "Changed, payable" : "Payable"}</span>
              : <span className="chip text-warn" data-testid="india-bank-state">Cooling off to {reviewDay(b.payable_from)}</span>}
        </div>
      ))}
    </div>
  );
}

export function ExceptionRows({ items }: { items: any[] }) {
  return (
    <div className="divide-y divide-border-soft" data-testid="payer-exceptions">
      {items.map((e) => (
        <div key={e.id} className="flex flex-wrap items-center gap-1.5 px-3 py-1.5" data-testid="payer-exception" title={e.evidence}>
          <span className="mr-auto min-w-0 text-sm text-ink">{e.payer}<span className="ml-1.5 text-xs text-ink-muted">{e.account}</span></span>
          {e.relationship && <span className="chip text-ink-muted">{e.relationship}</span>}
          <span className="chip text-ink-muted">ending {e.last4}</span>
          <span className="chip text-warn">{e.kind_label}</span>
        </div>
      ))}
    </div>
  );
}

export function RemittanceRows({ items }: { items: any[] }) {
  return (
    <div className="divide-y divide-border-soft" data-testid="inward-remittances">
      {items.map((r) => (
        <div key={r.id} className="flex flex-wrap items-center gap-1.5 px-3 py-1.5" data-testid="inward-remittance" title={r.document}>
          <span className="mr-auto min-w-0 text-sm text-ink">{r.client}<span className="ml-1.5 text-xs text-ink-muted">{r.certificate}</span></span>
          <span className="chip text-ink-muted">{r.kind}</span>
          <span className="chip tabular-nums text-ink">{rupees(r.amount_inr)}</span>
          {r.foreign_amount != null && <span className="chip tabular-nums text-ink-muted">{r.foreign_currency} {r.foreign_amount.toLocaleString("en-IN")}</span>}
          <span className="chip tabular-nums text-ink-muted">{reviewDay(r.received_on)}</span>
          {r.remitter_match !== "full" && <span className="chip text-warn" title={r.remitter}>Sender {r.remitter_match === "initials" ? "by initials" : "not the investor"}</span>}
        </div>
      ))}
    </div>
  );
}

export function CoolingChip({ cooling }: { cooling: any }) {
  return <span className={`chip ${cooling.set_by_firm ? "text-ink-muted" : "text-warn"}`} data-testid="bank-cooling" title={cooling.label}>
    {cooling.set_by_firm ? `${cooling.days}-day cooling-off` : "No cooling-off set"}
  </span>;
}

/** A tool result for the conversation: the payer check, or India's money in. */
export function IndiaMoneyCard({ result }: { result: any }) {
  if (result.check) {
    const c = result.check;
    return (
      <div className="flex flex-wrap items-center gap-1.5 rounded-lg border border-border bg-surface px-3 py-2" data-testid="payer-check" title={c.said}>
        <span className="mr-auto text-sm text-ink">{c.payer}<span className="ml-1.5 text-xs text-ink-muted">{c.account}</span></span>
        {c.last4 && <span className="chip text-ink-muted">ending {c.last4}</span>}
        {c.amount != null && <span className="chip tabular-nums text-ink-muted">{rupees(c.amount)}</span>}
        <span className="chip text-ink-muted">{c.use === "payout" ? "Money out" : c.use === "mandate" ? "Debit mandate" : "Money in"}</span>
        <span className={`chip ${c.ok ? "text-ok" : "text-crit"}`} data-testid="payer-check-verdict">{c.label}</span>
      </div>
    );
  }
  const india = result.india ?? result;
  if (india.applies === false) {
    return <div className="rounded-lg border border-border bg-surface px-3 py-2"><span className="chip text-ink-muted">Not an Indian firm</span></div>;
  }
  const deposits: any[] = result.deposits ?? [];
  return (
    <div className="overflow-hidden rounded-lg border border-border bg-surface" data-testid="india-money-card">
      <div className="flex flex-wrap items-center gap-1.5 border-b border-border-soft px-3 py-2">
        <div className="mr-auto text-sm font-medium text-ink">Money in and banks</div>
        {india.queue.length > 0 && <span className="chip text-crit">{india.queue.length} mandate item{india.queue.length === 1 ? "" : "s"}</span>}
        <CoolingChip cooling={india.cooling} />
      </div>
      {india.queue.length > 0 && <MandateQueueRows queue={india.queue.slice(0, 6)} />}
      {deposits.length > 0 && <><Heading>Credits</Heading><CreditRows deposits={deposits.slice(0, 5)} /></>}
      {india.banks.length > 0 && <><Heading>Registered banks</Heading><BankRows banks={india.banks.slice(0, 8)} /></>}
      {india.exceptions.length > 0 && <><Heading>Payer exceptions</Heading><ExceptionRows items={india.exceptions} /></>}
      {india.remittances.length > 0 && <><Heading>Inward remittances</Heading><RemittanceRows items={india.remittances.slice(0, 5)} /></>}
    </div>
  );
}
