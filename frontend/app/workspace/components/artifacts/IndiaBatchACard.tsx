"use client";

import { money } from "@/lib/format";
import { CounselChip } from "./GovernanceCard";
import { MandateQueueRows } from "./IndiaMoneyCard";

// India batch A: overseas remittances, a SIP's debit mandate, the shelf's other classes and the pledge of units, and the tax habits.
// Figures and chips only: the agent's message carries the sentences, and every source and caveat is a chip's title.

const usd = (v: number) => `USD ${Math.round(v).toLocaleString("en-US")}`;
const MANDATE_TONE: Record<string, string> = { active: "text-ok", requested: "text-warn", failed: "text-crit", not_yet: "text-warn", expired: "text-crit", paused: "text-warn", rejected: "text-crit", cancelled: "text-ink-muted", none: "text-warn" };

/** Each person's year against the Scheme's limit: the dollars counted, the headroom, the rupees toward the tax threshold and the tax collected. */
export function LrsRows({ people, figures }: { people: any[]; figures?: any[] }) {
  return (
    <div className="divide-y divide-border-soft" data-testid="lrs-people">
      {people.map((p) => (
        <div key={p.person_id} className="flex flex-wrap items-center gap-1.5 px-3 py-1.5" data-testid="lrs-person" title={p.said}>
          <span className="mr-auto text-sm text-ink">{p.person}</span>
          {p.state === "assessed" ? (
            <>
              <span className="chip tabular-nums text-ink-muted" title={`Financial year ${p.year}`}>{usd(p.remitted_usd)} of {usd(p.limit_usd)}</span>
              <span className={`chip tabular-nums ${p.over_limit ? "text-crit" : "text-ok"}`} data-testid="lrs-headroom">{usd(p.headroom_usd)} left</span>
              <span className="chip tabular-nums text-ink-muted" title="Rupees on the advices, counted toward the tax-collected-at-source threshold">
                {p.inr_unknown ? `${money(p.remitted_inr, "INR")} (${p.inr_unknown} advice without rupees)` : money(p.remitted_inr, "INR")}
              </span>
              <span className="chip tabular-nums text-ink-muted" title="As the advices show it">Tax collected {money(p.tcs_collected, "INR")}</span>
            </>
          ) : (
            <span className="chip text-warn" data-testid="lrs-not-assessed">Not assessed</span>
          )}
        </div>
      ))}
      {(figures ?? []).map((f) => (
        <div key={f.key} className="flex flex-wrap items-center gap-1.5 px-3 py-1">
          <span className="mr-auto min-w-0 flex-1 text-xs text-ink-soft" title={f.source}>{f.label}</span>
          <CounselChip counsel={f.counsel} />
        </div>
      ))}
    </div>
  );
}

/** What would let a SIP die silently, every mandate with its state today, then each active SIP against the one behind it. */
export function MandateRows({ data }: { data: any }) {
  return (
    <div className="divide-y divide-border-soft" data-testid="sip-mandates">
      {(data.queue ?? []).length > 0 && <div className="border-b border-border-soft"><MandateQueueRows queue={data.queue} /></div>}
      {(data.mandates ?? []).map((m: any) => (
        <div key={m.id} className="flex flex-wrap items-center gap-1.5 px-3 py-1.5" data-testid="sip-mandate" title={m.document}>
          <span className="mr-auto text-sm text-ink">{m.reference}</span>
          <span className="chip text-ink-muted">{m.type_label}</span>
          <span className="chip tabular-nums text-ink-muted" title="The most one debit may be">{money(m.per_debit_limit, "INR")}</span>
          {m.last4 && <span className="chip text-ink-muted" title={m.bank}>ending {m.last4}</span>}
          {m.expires_on && <span className="chip tabular-nums text-ink-muted">to {m.expires_on}</span>}
          {m.first_debit && <span className={`chip ${m.first_debit.outcome === "confirmed" ? "text-ok" : "text-crit"}`} title={m.first_debit.reason ?? m.first_debit.on} data-testid="sip-first-debit">First debit {m.first_debit.outcome}</span>}
          {m.over_upi_line && <span className="chip text-warn" title="Above the line for mutual-fund debits; counsel has not read it">Over the UPI line</span>}
          <span className={`chip ${MANDATE_TONE[m.state]}`} data-testid="sip-mandate-state">{m.label}</span>
        </div>
      ))}
      {(data.sips ?? []).map((s: any) => (
        <div key={s.sip_id} className="flex flex-wrap items-center gap-1.5 px-3 py-1.5" data-testid="sip-cover" title={s.warning ?? undefined}>
          <span className="mr-auto text-sm text-ink-soft">{s.fund} · {s.account}</span>
          <span className="chip tabular-nums text-ink-muted">{money(s.amount, "INR")} a month</span>
          <span className={`chip ${MANDATE_TONE[s.mandate_state]}`} data-testid="sip-cover-state">{s.mandate_label}</span>
        </div>
      ))}
    </div>
  );
}

/** Each recorded holding with the rule of its class beside it. */
export function SavingsRows({ data }: { data: any }) {
  const rules: Record<string, any> = Object.fromEntries((data.rules ?? data.all_rules ?? []).map((r: any) => [r.kind, r]));
  return (
    <div className="divide-y divide-border-soft" data-testid="india-savings">
      {(data.holdings ?? []).map((h: any) => (
        <div key={h.id} className="flex flex-wrap items-center gap-1.5 px-3 py-1.5" data-testid="india-saving" title={h.document}>
          <span className="mr-auto text-sm text-ink">{h.label}{h.owner ? ` · ${h.owner}` : ""}</span>
          <span className="chip text-ink-muted">{h.institution}</span>
          <span className="chip tabular-nums text-ink-muted" title={`As at ${h.value_on}`}>{money(h.value, h.currency)}</span>
          {h.rate != null ? <span className="chip tabular-nums text-ink-muted" title="The statement's rate">{h.rate}%</span>
            : h.administered && <span className="chip text-warn" title={h.rate_note}>Rate not on file</span>}
          {h.locked_in != null && <span className={`chip ${h.locked_in ? "text-warn" : "text-ok"}`} title={h.locked_in_to ? `To ${h.locked_in_to}` : undefined}>{h.locked_in ? "Locked in" : "Past lock-in"}</span>}
          {h.locked_in === null && h.lock_in_note && <span className="chip text-warn" title={h.lock_in_note}>Lock-in not assessed</span>}
          {h.exit_split && <span className="chip tabular-nums text-ink-muted" title={h.exit_split.basis}>60% {money(h.exit_split.lump_sum, h.currency)} · 40% annuity</span>}
          {h.fmc && <span className={`chip tabular-nums ${h.fmc.over_cap ? "text-crit" : "text-ink-muted"}`} title="Fund management charge against the cap">FMC {h.fmc.stated}% / cap {h.fmc.cap}%</span>}
          {h.redeems_exempt && <span className="chip text-ok" title="A gain on redemption at maturity is exempt for an individual">Exempt at maturity</span>}
          {h.new_issues && <span className="chip text-warn" title={h.new_issues}>New issues discontinued</span>}
        </div>
      ))}
      {Object.values(rules).slice(0, (data.holdings ?? []).length ? 12 : 12).map((r: any) => (
        <div key={r.kind} className="flex flex-wrap items-center gap-1.5 px-3 py-1" data-testid="india-saving-rule">
          <span className="mr-auto min-w-0 flex-1 text-xs text-ink-soft" title={(r.rules ?? []).map((x: any) => x.source).join(" | ")}>{r.label}: {(r.rules ?? [])[0]?.label ?? r.tax}</span>
          {(r.rules ?? []).slice(0, 1).map((x: any) => <CounselChip key={x.key} counsel={x.counsel} />)}
        </div>
      ))}
    </div>
  );
}

/** Units a lender holds a lien on: pledged or released, with what is free to sell. */
export function PledgeRows({ pledges }: { pledges: any[] }) {
  return (
    <div className="divide-y divide-border-soft" data-testid="pledges">
      {pledges.map((p) => (
        <div key={p.id} className="flex flex-wrap items-center gap-1.5 px-3 py-1.5" data-testid="pledge" title={p.document}>
          <span className="mr-auto text-sm text-ink">{p.symbol} · {p.account}</span>
          <span className="chip text-ink-muted">{p.lender}</span>
          <span className="chip tabular-nums text-ink-muted" title="Units pledged, and still under lien">{p.units.toLocaleString("en-IN")} / {p.under_lien.toLocaleString("en-IN")}</span>
          <span className="chip tabular-nums text-ink-muted" title="Units the account may still sell">{p.sellable.toLocaleString("en-IN")} free</span>
          <span className={`chip ${p.state === "pledged" ? "text-warn" : "text-ok"}`} data-testid="pledge-state">{p.label}</span>
        </div>
      ))}
    </div>
  );
}

/** Gain harvesting, dividends and gifts for a household. */
export function TaxHabitRows({ data }: { data: any }) {
  return (
    <div className="divide-y divide-border-soft" data-testid="tax-habits">
      {(data.gain_harvest ?? []).map((g: any) => (
        <div key={g.person_id} className="flex flex-wrap items-center gap-1.5 px-3 py-1.5" data-testid="gain-harvest" title={g.basis ?? g.said}>
          <span className="mr-auto text-sm text-ink">{g.person} · gain harvesting{g.household ? <span className="ml-1.5 text-xs text-ink-muted">{g.household}</span> : null}</span>
          {g.state === "assessed" ? (
            <>
              <span className="chip tabular-nums text-ink-muted" title={`The exemption for ${g.year}`}>{money(g.used, "INR")} of {money(g.exemption, "INR")} used</span>
              <span className="chip tabular-nums text-ok">{money(g.left, "INR")} left</span>
              {g.total_step_up > 0 && <span className="chip tabular-nums text-ink-muted" title={g.candidates.map((c: any) => `${c.symbol} ${c.quantity}`).join(", ")}>Cost steps up {money(g.total_step_up, "INR")}</span>}
            </>
          ) : <span className="chip text-warn">Not assessed</span>}
        </div>
      ))}
      {(data.dividends ?? []).map((d: any) => (
        <div key={d.person_id} className="flex flex-wrap items-center gap-1.5 px-3 py-1.5" data-testid="dividends" title={d.said}>
          <span className="mr-auto text-sm text-ink">{d.person} · dividends{d.household ? <span className="ml-1.5 text-xs text-ink-muted">{d.household}</span> : null}</span>
          {d.state === "assessed" ? (
            <>
              <span className="chip tabular-nums text-ink-muted">{money(d.total, "INR")} from {d.payers.length}</span>
              <span className="chip tabular-nums text-ink-muted" title="Deducted by a payer that paid above the threshold">TDS {money(d.tds, "INR")}</span>
              {d.tax != null ? <span className="chip tabular-nums text-ink-muted" title="At the recorded slab and cess">Tax {money(d.tax, "INR")}</span> : <span className="chip text-warn" title={d.needs}>Tax not assessed</span>}
            </>
          ) : <span className="chip text-ink-muted">{d.state === "none_on_file" ? "None on file" : "Not assessed"}</span>}
        </div>
      ))}
      {(data.gifts ?? []).map((g: any) => (
        <div key={g.id} className="flex flex-wrap items-center gap-1.5 px-3 py-1.5" data-testid="tax-gift" title={g.document}>
          <span className="mr-auto text-sm text-ink">{g.giver} to {g.receiver}{g.household ? <span className="ml-1.5 text-xs text-ink-muted">{g.household}</span> : null}</span>
          <span className="chip text-ink-muted" title="As the document states it">{g.relationship_label}</span>
          <span className="chip tabular-nums text-ink-muted">{money(g.value, "INR")}</span>
          <span className={`chip ${g.receiver_tax.state === "taxable" ? "text-warn" : "text-ok"}`} title={g.receiver_tax.said}>{g.receiver_tax.state === "exempt" ? "Exempt" : g.receiver_tax.state === "taxable" ? "Income to receiver" : "Under threshold"}</span>
          {g.clubbing && (
            <span className={`chip tabular-nums ${g.clubbing.state === "added_back" ? "text-warn" : "text-ink-muted"}`} title={g.clubbing.said} data-testid="gift-clubbing">
              {g.clubbing.state === "added_back" ? `Added back ${money(g.clubbing.added_to_giver, "INR")}` : "Clubbing not assessed"}
            </span>
          )}
        </div>
      ))}
    </div>
  );
}

/** A tool result for the conversation: whichever of the five it is. */
export function IndiaBatchACard({ result }: { result: any }) {
  if (result.applies === false) {
    return <div className="rounded-lg border border-border bg-surface px-3 py-2"><span className="chip text-ink-muted">Not an Indian firm</span></div>;
  }
  const kind = Array.isArray(result.mandates) ? "mandates" : Array.isArray(result.holdings) ? "savings" : Array.isArray(result.pledges) ? "pledges"
    : Array.isArray(result.gain_harvest) ? "habits" : "lrs";
  const title = { mandates: "SIP mandates", savings: "Savings, pension and other classes", pledges: "Pledged units", habits: "Tax habits", lrs: "Overseas remittances" }[kind];
  return (
    <div className="overflow-hidden rounded-lg border border-border bg-surface" data-testid="india-batch-a-card">
      <div className="flex flex-wrap items-center justify-between gap-2 border-b border-border-soft px-3 py-2">
        <div className="text-sm font-medium text-ink">{title}</div>
        {kind === "mandates" && result.without_mandate > 0 && <span className="chip text-warn">{result.without_mandate} without a mandate</span>}
      </div>
      {kind === "lrs" && <LrsRows people={result.people ?? []} figures={result.figures} />}
      {kind === "mandates" && <MandateRows data={result} />}
      {kind === "savings" && <SavingsRows data={result} />}
      {kind === "pledges" && <PledgeRows pledges={result.pledges} />}
      {kind === "habits" && <TaxHabitRows data={result} />}
    </div>
  );
}
