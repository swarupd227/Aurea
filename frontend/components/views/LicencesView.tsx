"use client";

import { useState } from "react";
import { Check, X } from "lucide-react";
import { Card, Empty, Spinner } from "@/components/ui";
import { CHANNEL_TEXT, ConsentChips, ConsentPanel } from "@/components/ConsentPanel";
import { api, getUser } from "@/lib/api";
import { useApi } from "@/lib/hooks";
import { money, titleCase } from "@/lib/format";
import { withRef } from "@/lib/refs";
import { LicenceChips } from "@/app/workspace/components/artifacts/LicenceCard";
import { FeeCapChips, PreAdviceChecks } from "@/app/workspace/components/artifacts/SebiCard";
import { GuardianChips, PmsClients, ResidencyPeople } from "@/app/workspace/components/artifacts/IndiaCard";
import { AggregatorConsents, StatementChips } from "@/app/workspace/components/artifacts/AggregatorCard";
import { AdvertisingChecker, AnnexureTable, ComplaintRows } from "@/app/workspace/components/artifacts/ComplaintsCard";
import { KycCounts, KycParties } from "@/app/workspace/components/artifacts/IndiaKycCard";
import { EstateHousehold, FamilyChips } from "@/app/workspace/components/artifacts/FamilyEstateCard";
import { ReminderRows } from "@/app/workspace/components/artifacts/PolishCard";
import { CounselRows, LicenceMapTable, PolicyForm, PolicyRows } from "@/app/workspace/components/artifacts/GovernanceCard";
import { IndiaDateItems } from "@/app/workspace/components/artifacts/RegulatoryCountdownCard";
import { AccreditedRows, FeeTermRows, RateCardChips, RateCardRows, TicketGateFleet } from "@/app/workspace/components/artifacts/IndiaGatesCard";
import { ChannelChips, ChannelRows, StackChips, StackRows, TemplateRows } from "@/app/workspace/components/artifacts/IndiaChannelsCard";
import { useViewNav } from "./ViewNav";
import { ViewHeader } from "./ViewHeader";

const CHANNELS = ["email", "recorded_call", "signed_document"] as const;
const RECORDERS = ["adviser", "paraplanner"];

/** In the work pane, a client's answer is recorded by the conversation's record_client_consent tool,
 * behind its Confirm card: the same inputs as ConsentPanel, handed over as a sentence. */
function ConsentAsk({ consent, demonstration, say }: { consent: any; demonstration?: boolean; say: (text: string) => void }) {
  const [channel, setChannel] = useState<string>("email");
  const [evidence, setEvidence] = useState("");
  const user = getUser();
  const canRecord = RECORDERS.includes(user?.role);
  const simulate = demonstration ?? !!user?.firm?.demonstration;
  const orders: any[] = consent.orders || [];
  const what = `${consent.title ? `"${consent.title}"` : "the proposal"}${consent.subject ? ` for ${consent.subject}` : ""}`;

  function send(given: boolean, simulated = false) {
    const sentence = simulated
      ? `Record a simulated consent from the client to ${what}.`
      : `Record that the client ${given ? "consented to" : "declined"} ${what}, by ${CHANNEL_TEXT[channel].toLowerCase()}; the answer is on file at ${evidence.trim()}.`;
    say(withRef(sentence, "recommendation", consent.recommendation_id));
    setEvidence("");
  }

  return (
    <div className="border-t border-border p-3 space-y-2" data-testid="rec-consent">
      <div className="flex flex-wrap items-center gap-1.5">
        <span className="font-mono text-[11px] uppercase tracking-wider text-ink-muted mr-auto">Waiting for the client</span>
        <span className="chip text-warn">No orders placed</span>
        {orders.slice(0, 6).map((o, i) => (
          <span key={i} className="chip text-ink-muted" title={o.est_value != null ? money(o.est_value) : undefined}>
            {o.side} {o.quantity} {o.symbol}
          </span>
        ))}
        {orders.length > 6 && <span className="chip text-ink-muted">+{orders.length - 6} more</span>}
      </div>
      {canRecord && (
        <div className="flex flex-wrap items-end gap-2">
          <div>
            <label className="label text-[11px]">How</label>
            <select className="input text-sm" value={channel} aria-label="How the client answered"
              data-testid="consent-channel" onChange={(e) => setChannel(e.target.value)}>
              {CHANNELS.map((k) => <option key={k} value={k}>{CHANNEL_TEXT[k]}</option>)}
            </select>
          </div>
          <div className="flex-1 min-w-[12rem]">
            <label className="label text-[11px]">Where it is on file</label>
            <input className="input text-sm" value={evidence} aria-label="Where the client's answer is on file"
              data-testid="consent-evidence" onChange={(e) => setEvidence(e.target.value)} />
          </div>
          <button className="btn-outline" data-testid="consent-decline" disabled={!evidence.trim()}
            onClick={() => send(false)}><X size={15} /> Declined</button>
          <button className="btn-primary" data-testid="consent-given" disabled={!evidence.trim()}
            onClick={() => send(true)}><Check size={15} /> Consented</button>
          {simulate && (
            <button className="btn-outline" data-testid="consent-simulate" onClick={() => send(true, true)}>Simulate client</button>
          )}
        </div>
      )}
    </div>
  );
}

/** Compliance marks a Budget figure as awaiting the Budget: in the work pane through the conversation's record_figure_watch tool behind its
 * Confirm card, on a page directly. */
function WatchForm({ figures, onDone }: { figures: any[]; onDone: () => void }) {
  const nav = useViewNav();
  const budget = figures.filter((f) => f.kind === "budget" && f.counsel?.state !== "awaiting_budget");
  const [key, setKey] = useState("");
  const [reason, setReason] = useState("");
  const [error, setError] = useState<string | null>(null);
  if (getUser()?.role !== "compliance" || budget.length === 0) return null;
  async function mark() {
    setError(null);
    if (nav.say) {
      nav.say(`Mark ${key} as awaiting the Budget: ${reason.trim()}.`);
      setReason("");
      return;
    }
    try {
      await api("/api/licences/rate-card/watch", { body: { figure_key: key, reason: reason.trim() } });
      setReason("");
      onDone();
    } catch (e: any) {
      setError(e?.message ?? "Not recorded");
    }
  }
  return (
    <div className="flex flex-wrap items-end gap-2 px-3 py-2" data-testid="watch-form">
      <select className="input flex-1 min-w-[12rem] text-sm" value={key} aria-label="The figure" data-testid="watch-figure" onChange={(e) => setKey(e.target.value)}>
        <option value="">A Budget figure</option>
        {budget.map((f) => <option key={f.key} value={f.key}>{f.label}</option>)}
      </select>
      <input className="input flex-1 min-w-[12rem] text-sm" value={reason} aria-label="Which Budget it waits on" placeholder="Which Budget it waits on" data-testid="watch-reason" onChange={(e) => setReason(e.target.value)} />
      <button className="btn-outline" data-testid="watch-mark" disabled={!key || reason.trim().length < 5} onClick={mark}>Awaiting the Budget</button>
      {error && <span className="chip text-crit">{error}</span>}
    </div>
  );
}

const CAL_TONE: Record<string, string> = { overdue: "text-crit", due_soon: "text-warn", upcoming: "text-ink-muted", filed: "text-ok" };
const CAL_TEXT: Record<string, string> = { overdue: "Overdue", due_soon: "Due soon", upcoming: "Upcoming", filed: "Filed" };

/** One owed period. Compliance records it filed: in the work pane through the conversation's
 * record_compliance_filing tool behind its Confirm card, on a page directly. */
function FilingRow({ item, onDone }: { item: any; onDone: () => void }) {
  const nav = useViewNav();
  const [reference, setReference] = useState("");
  const [error, setError] = useState<string | null>(null);
  const canRecord = getUser()?.role === "compliance" && item.state !== "filed";

  async function record() {
    setError(null);
    if (nav.say) {
      nav.say(`Record that the ${item.label.toLowerCase()} for ${item.period} was filed today, reference ${reference.trim()}.`);
      setReference("");
      return;
    }
    try {
      await api("/api/licences/filings", { body: { obligation: item.obligation, period: item.period, reference: reference.trim() } });
      setReference("");
      onDone();
    } catch (e: any) {
      setError(e?.message ?? "Not recorded");
    }
  }

  return (
    <div className="space-y-1.5 py-2" title={`${item.citation} · due day: ${item.due_basis}`}>
      <div className="flex flex-wrap items-center gap-1.5">
        <span className="text-sm text-ink mr-auto">{item.label}</span>
        <span className="chip text-ink-muted">{item.period}</span>
        <span className="chip text-ink-muted tabular-nums">{item.due_on}</span>
        <span className={`chip ${CAL_TONE[item.state]}`} data-testid="calendar-state">{CAL_TEXT[item.state]}</span>
        {item.reference && <span className="chip text-ink-muted">{item.reference}</span>}
        {item.register && (
          <span className="chip text-ink-muted tabular-nums" title="Worked from the complaints register" data-testid="calendar-register">
            {item.register.received} received · {item.register.resolved} resolved · {item.register.pending_at_end} pending
          </span>
        )}
      </div>
      {canRecord && (
        <div className="flex flex-wrap items-end gap-2">
          <input className="input flex-1 min-w-[12rem] text-sm" value={reference} aria-label="Acknowledgement or reference"
            placeholder="Acknowledgement or reference" data-testid="filing-reference" onChange={(e) => setReference(e.target.value)} />
          <button className="btn-outline" data-testid="filing-record" disabled={reference.trim().length < 4} onClick={record}>
            <Check size={15} /> Filed
          </button>
          {error && <span className="chip text-crit">{error}</span>}
        </div>
      )}
    </div>
  );
}

function CalendarSection({ calendar, onDone }: { calendar: any; onDone: () => void }) {
  const c = calendar.counts;
  return (
    <>
      <Card className="mb-4">
        <div className="mb-2 flex flex-wrap items-center gap-1.5">
          <span className="tile-label mr-auto">Compliance calendar</span>
          {c.overdue > 0 && <span className="chip text-crit">{c.overdue} overdue</span>}
          {c.due_soon > 0 && <span className="chip text-warn">{c.due_soon} due soon</span>}
          <span className="chip text-ok">{c.filed} filed</span>
          {calendar.governance?.counts?.overdue > 0 && <span className="chip text-crit" title="Policy reviews overdue, from the governance register">{calendar.governance.counts.overdue} policy review overdue</span>}
          {calendar.governance?.counts?.due_soon > 0 && <span className="chip text-warn" title="Policy reviews due soon, from the governance register">{calendar.governance.counts.due_soon} policy review due soon</span>}
          <span className="chip text-ink-muted">since {calendar.since}</span>
        </div>
        <div className="divide-y divide-border-soft" data-testid="licence-calendar">
          {calendar.items.map((i: any) => <FilingRow key={`${i.obligation}-${i.period}`} item={i} onDone={onDone} />)}
        </div>
      </Card>
      <Card className="mb-4">
        <div className="tile-label mb-2">Registered details</div>
        <div className="divide-y divide-border-soft" data-testid="licence-officers">
          {calendar.officers.map((o: any) => (
            <div key={o.key} className="flex flex-wrap items-center gap-1.5 py-1.5">
              <span className="text-sm text-ink-soft mr-auto">{o.label}</span>
              {o.value ? <span className="chip text-ink">{o.value}</span> : <span className="chip text-warn">Not recorded</span>}
            </div>
          ))}
        </div>
      </Card>
    </>
  );
}

/** What each licence lets an agent do, client by client, and the approved proposals waiting for a
 * client's consent. In the work pane a client's answer goes through the conversation's
 * record_client_consent tool; on a page ConsentPanel records it directly. */
export function LicencesView() {
  const nav = useViewNav();
  const { data, loading, refetch } = useApi<any>("/api/licences");
  // The complaints register is read by compliance, a branch leader and operations; an adviser may record one and reads none.
  const canReadComplaints = ["compliance", "branch_leader", "operations"].includes(getUser()?.role);
  const complaints = useApi<any>(canReadComplaints ? "/api/licences/complaints" : null);
  // India's dates for the firm sit in the same countdown the client pages read.
  const dates = useApi<any>("/api/licences/dates");
  const canRecordPolicy = getUser()?.role === "compliance";
  if (loading || !data) return <div className="view-container"><Spinner /></div>;
  if (!data.applies) {
    return (
      <div className="view-container">
        <ViewHeader title="Licences" />
        <Empty>No licences on file</Empty>
      </div>
    );
  }
  const waiting: any[] = data.awaiting_consent;
  const mandates: any[] = data.mandates;
  const consenting = mandates.filter((m) => m.per_trade_consent).length;

  return (
    <div className="view-container">
      <ViewHeader
        title="Licences"
        meta={data.demonstration ? <span className="chip text-warn">demonstration firm</span> : undefined}
      />

      <div className="mb-4 grid grid-cols-2 gap-3 cq-md:grid-cols-4" data-testid="licence-summary">
        {[
          ["Licences held", data.licences.length, "text-ink"],
          ["Agent ceiling", (data.ceiling ?? "").replace("tier_", "Tier "), "text-ink"],
          ["Client consents", `${consenting} of ${mandates.length}`, "text-ink"],
          ["Waiting on clients", waiting.length, waiting.length ? "text-warn" : "text-ink-muted"],
        ].map(([label, v, tone]: any) => (
          <Card key={label}>
            <div className="tile-label">{label}</div>
            <div className={`mt-1 text-2xl font-semibold tabular-nums ${tone}`}>{v}</div>
          </Card>
        ))}
      </div>

      <Card className="mb-4">
        <div className="tile-label mb-2">Held by this firm</div>
        <div className="divide-y divide-border-soft" data-testid="licence-matrix">
          {data.licences.map((l: any) => (
            <div key={l.key} className="flex flex-wrap items-center gap-1.5 py-2">
              <span className="text-sm font-medium text-ink mr-auto">{l.name}</span>
              <LicenceChips l={l} />
              <span className="chip text-ink-muted">{l.max_tier.replace("tier_", "Tier ")} at most</span>
              <span className="chip text-ink-muted">{l.citation}</span>
            </div>
          ))}
        </div>
      </Card>

      {(data.pre_advice ?? []).length > 0 && (
        <Card className="mb-4">
          <div className="tile-label mb-2">Before advice</div>
          <div className="divide-y divide-border-soft" data-testid="licence-pre-advice">
            {data.pre_advice.map((g: any) => (
              <div key={g.household_id} className="space-y-1.5 py-2">
                <div className="flex flex-wrap items-center gap-1.5">
                  <span className="text-sm text-ink mr-auto">{g.household}</span>
                  <span className={`chip ${g.met ? "text-ok" : "text-warn"}`}>{g.met ? "May advise" : "No advice yet"}</span>
                </div>
                <PreAdviceChecks gate={g} />
              </div>
            ))}
          </div>
        </Card>
      )}

      {(data.fee_caps ?? []).length > 0 && (
        <Card className="mb-4">
          <div className="tile-label mb-2">Fees against SEBI's cap, per family</div>
          <div className="divide-y divide-border-soft" data-testid="licence-fee-caps">
            {data.fee_caps.map((c: any) => (
              <div key={c.household_id} className="flex flex-wrap items-center gap-1.5 py-2">
                <span className="text-sm text-ink mr-auto">{c.household}</span>
                <FeeCapChips cap={c} />
              </div>
            ))}
          </div>
        </Card>
      )}

      {(data.residency?.households ?? []).length > 0 && (
        <Card className="mb-4">
          <div className="mb-2 flex flex-wrap items-center gap-1.5">
            <span className="tile-label mr-auto">Residence, as recorded</span>
            <span className="chip text-ink-muted">{data.residency.counts.non_resident} non-resident</span>
            <span className={`chip ${data.residency.counts.not_on_file ? "text-warn" : "text-ok"}`}>{data.residency.counts.not_on_file} not assessed</span>
          </div>
          <div className="-mx-3" data-testid="licence-residency"><ResidencyPeople households={data.residency.households} /></div>
        </Card>
      )}

      {(data.guardians?.minors ?? []).length > 0 && (
        <Card className="mb-4">
          <div className="tile-label mb-2">Minors and their guardians</div>
          <div className="-mx-3" data-testid="licence-guardians"><GuardianChips guardians={data.guardians} /></div>
        </Card>
      )}

      {data.kyc?.applies && (data.kyc.parties ?? []).length > 0 && (
        <Card className="mb-4">
          <div className="mb-2 flex flex-wrap items-center gap-1.5">
            <span className="tile-label mr-auto">KYC, as recorded</span>
            <KycCounts counts={data.kyc.counts} />
            <span className="chip text-ink-muted" title={data.kyc.not_connected}>Lookups not connected</span>
          </div>
          <div className="-mx-3" data-testid="licence-kyc"><KycParties parties={data.kyc.parties} /></div>
        </Card>
      )}

      {data.pms?.applies && (data.pms.clients ?? []).length > 0 && (
        <Card className="mb-4">
          <div className="mb-2 flex flex-wrap items-center gap-1.5">
            <span className="tile-label mr-auto">Portfolio Manager clients</span>
            <span className={`chip ${data.pms.held ? "text-crit" : "text-ok"}`}>{data.pms.held} held</span>
            <span className={`chip ${data.pms.without_agreement ? "text-warn" : "text-ink-muted"}`}>{data.pms.without_agreement} not assessed</span>
          </div>
          <div className="-mx-3" data-testid="licence-pms"><PmsClients clients={data.pms.clients} /></div>
        </Card>
      )}

      {data.aggregator?.applies && ((data.aggregator.consents ?? []).length > 0 || (data.aggregator.statements ?? []).length > 0) && (
        <Card className="mb-4">
          <div className="mb-2 flex flex-wrap items-center gap-1.5">
            <span className="tile-label mr-auto">Account Aggregator and statements</span>
            <span className="chip text-ok">{data.aggregator.counts.active} active</span>
            {data.aggregator.counts.expired > 0 && <span className="chip text-warn">{data.aggregator.counts.expired} lapsed</span>}
            {data.aggregator.counts.revoked > 0 && <span className="chip text-crit">{data.aggregator.counts.revoked} withdrawn</span>}
          </div>
          <div className="-mx-3" data-testid="licence-aggregator">
            <AggregatorConsents consents={data.aggregator.consents} />
            {(data.aggregator.statements ?? []).length > 0 && <StatementChips statements={data.aggregator.statements} />}
          </div>
        </Card>
      )}

      {complaints.data?.applies && (complaints.data.complaints ?? []).length > 0 && (
        <Card className="mb-4">
          <div className="mb-2 flex flex-wrap items-center gap-1.5">
            <span className="tile-label mr-auto">Complaints register</span>
            {complaints.data.counts.overdue > 0 && <span className="chip text-crit">{complaints.data.counts.overdue} overdue</span>}
            {complaints.data.counts.at_risk > 0 && <span className="chip text-warn">{complaints.data.counts.at_risk} at risk</span>}
            <span className="chip text-ink-muted">{complaints.data.counts.open} open</span>
            <span className="chip text-ok">{complaints.data.counts.resolved} resolved</span>
          </div>
          <div className="-mx-3" data-testid="licence-complaints">
            <ComplaintRows complaints={complaints.data.complaints} />
            {complaints.data.annexure_c && <div className="border-t border-border-soft"><AnnexureTable table={complaints.data.annexure_c} /></div>}
          </div>
        </Card>
      )}

      {data.applies && (
        <Card className="mb-4">
          <div className="tile-label mb-2">Check client wording</div>
          <AdvertisingChecker />
        </Card>
      )}

      {data.family?.applies && ((data.family.families ?? []).length > 0 || (data.estate?.households ?? []).length > 0) && (
        <Card className="mb-4">
          <div className="mb-2 flex flex-wrap items-center gap-1.5">
            <span className="tile-label mr-auto">Family of client and estate</span>
            <span className="chip text-ok">{data.family.recorded} recorded</span>
            <span className="chip text-ink-muted" title="Households with no family recorded: the household stands in">{data.family.household_standing_in} stand in</span>
            {data.estate?.issues > 0 && <span className="chip text-warn">{data.estate.issues} to look at</span>}
          </div>
          <div className="-mx-3" data-testid="licence-family">
            <FamilyChips families={data.family.families} relations={data.family.relations} />
            {(data.estate?.households ?? []).map((h: any) => <div key={h.household_id} className="border-t border-border-soft"><EstateHousehold view={h} currency="INR" /></div>)}
          </div>
        </Card>
      )}

      {data.reminders?.applies && (data.reminders.reminders ?? []).length > 0 && (
        <Card className="mb-4">
          <div className="mb-2 flex flex-wrap items-center gap-1.5">
            <span className="tile-label mr-auto">Dates that need a task</span>
            {data.reminders.to_raise > 0 && <span className="chip text-warn">{data.reminders.to_raise} without a task</span>}
            <span className="chip text-ok">{data.reminders.tasks_exist} raised</span>
          </div>
          <div className="-mx-3" data-testid="licence-reminders"><ReminderRows reminders={data.reminders.reminders} /></div>
        </Card>
      )}

      {data.governance && (
        <Card className="mb-4">
          <div className="mb-2 flex flex-wrap items-center gap-1.5">
            <span className="tile-label mr-auto">Licence map and governance register</span>
            {data.governance.policies.counts?.overdue > 0 && <span className="chip text-crit">{data.governance.policies.counts.overdue} overdue</span>}
            {data.governance.policies.counts?.due_soon > 0 && <span className="chip text-warn">{data.governance.policies.counts.due_soon} due soon</span>}
            {data.governance.policies.counts?.not_recorded > 0 && <span className="chip text-ink-muted">{data.governance.policies.counts.not_recorded} not recorded</span>}
          </div>
          <div className="-mx-3">
            <LicenceMapTable map={data.governance.map} />
            <div className="border-t border-border-soft"><PolicyRows policies={data.governance.policies.policies} /></div>
            {canRecordPolicy && <div className="border-t border-border-soft"><PolicyForm onDone={() => { refetch(); nav.changed(); }} /></div>}
          </div>
        </Card>
      )}

      {data.counsel?.applies && (
        <Card className="mb-4">
          <div className="mb-2 flex flex-wrap items-center gap-1.5">
            <span className="tile-label mr-auto">Counsel's review of each law figure</span>
            <span className="chip text-ok">{data.counsel.counts.confirmed} confirmed</span>
            {data.counsel.counts.not_confirmed + data.counsel.counts.corrected > 0 && <span className="chip text-warn">{data.counsel.counts.not_confirmed + data.counsel.counts.corrected} not confirmed or corrected</span>}
            <span className="chip text-ink-muted">{data.counsel.counts.not_reviewed} of {data.counsel.total} not reviewed</span>
          </div>
          <div className="-mx-3"><CounselRows figures={data.counsel.figures} limit={8} /></div>
        </Card>
      )}

      {data.ticket_gates?.applies && (data.ticket_gates.households ?? []).length > 0 && (
        <Card className="mb-4">
          <div className="mb-2 flex flex-wrap items-center gap-1.5">
            <span className="tile-label mr-auto">Product categories open to each household</span>
            <span className="chip text-ink-muted" title="SIF, PMS and AIF minimums, each a law figure counsel has not yet read by default">minimums by ticket size</span>
          </div>
          <div className="-mx-3"><TicketGateFleet fleet={data.ticket_gates} /></div>
        </Card>
      )}

      {data.accredited?.applies && (data.accredited.people ?? []).length > 0 && (
        <Card className="mb-4">
          <div className="mb-2 flex flex-wrap items-center gap-1.5">
            <span className="tile-label mr-auto">Accredited investors</span>
            {(data.accredited.counts?.accredited ?? 0) > 0 && <span className="chip text-ok">{data.accredited.counts.accredited} accredited</span>}
            {(data.accredited.counts?.lapsed ?? 0) > 0 && <span className="chip text-warn">{data.accredited.counts.lapsed} lapsed</span>}
          </div>
          <div className="-mx-3"><AccreditedRows people={data.accredited.people} criteria={data.accredited.criteria} /></div>
        </Card>
      )}

      {data.fee_terms?.applies && ((data.fee_terms.households ?? []).length > 0 || data.fee_terms.deposit?.recorded) && (
        <Card className="mb-4">
          <div className="mb-2 flex flex-wrap items-center gap-1.5">
            <span className="tile-label mr-auto">Adviser fee terms</span>
          </div>
          <div className="-mx-3"><FeeTermRows data={data.fee_terms} /></div>
        </Card>
      )}

      {data.channels?.applies && (data.channels.households ?? []).length > 0 && (
        <Card className="mb-4">
          <div className="mb-2 flex flex-wrap items-center gap-1.5">
            <span className="tile-label mr-auto">Languages and WhatsApp</span>
            <ChannelChips data={data.channels} />
          </div>
          <div className="-mx-3">
            <ChannelRows data={data.channels} />
            {(data.channels.templates ?? []).length > 0 && <div className="border-t border-border-soft"><TemplateRows templates={data.channels.templates} /></div>}
          </div>
        </Card>
      )}

      {data.india_stack?.applies && (
        <Card className="mb-4">
          <div className="mb-2 flex flex-wrap items-center gap-1.5">
            <span className="tile-label mr-auto">DigiLocker and eSign</span>
            <StackChips data={data.india_stack} />
          </div>
          <div className="-mx-3"><StackRows items={data.india_stack.items ?? []} /></div>
        </Card>
      )}

      {data.rate_card?.applies && (
        <Card className="mb-4">
          <div className="mb-2 flex flex-wrap items-center gap-1.5">
            <span className="tile-label mr-auto">February rate card</span>
            <RateCardChips card={data.rate_card} />
          </div>
          <div className="-mx-3">
            <RateCardRows card={data.rate_card} limit={8} />
            <div className="border-t border-border-soft"><WatchForm figures={data.rate_card.figures} onDone={() => { refetch(); nav.changed(); }} /></div>
          </div>
        </Card>
      )}

      {dates.data?.analysis && (
        <Card className="mb-4">
          <div className="mb-2 flex flex-wrap items-center gap-1.5">
            <span className="tile-label mr-auto">The firm's India dates</span>
            {dates.data.analysis.figures.counts?.overdue > 0 && <span className="chip text-crit">{dates.data.analysis.figures.counts.overdue} overdue</span>}
            {dates.data.analysis.figures.counts?.due_soon > 0 && <span className="chip text-warn">{dates.data.analysis.figures.counts.due_soon} due soon</span>}
          </div>
          <div data-testid="licence-india-dates"><IndiaDateItems items={dates.data.analysis.figures.items} limit={10} /></div>
        </Card>
      )}

      {data.calendar?.applies && <CalendarSection calendar={data.calendar} onDone={() => { refetch(); nav.changed(); }} />}

      <Card className="mb-4">
        <div className="tile-label mb-2">Waiting on clients</div>
        {waiting.length === 0
          ? <div className="font-mono text-xs text-ink-muted">nothing waiting</div>
          : (
            <div className="-mx-5 divide-y divide-border" data-testid="licence-waiting">
              {waiting.map((c) => (
                <div key={c.id}>
                  <div className="flex flex-wrap items-center gap-1.5 px-5 py-2">
                    <span className="text-sm text-ink mr-auto">{c.subject}</span>
                    <span className="chip text-ink-muted">Approved by {c.requested_by}</span>
                  </div>
                  {nav.say
                    ? <ConsentAsk consent={c} demonstration={data.demonstration} say={nav.say} />
                    : <ConsentPanel recId={c.recommendation_id} consent={c} demonstration={data.demonstration} onDone={() => { refetch(); nav.changed(); }} />}
                </div>
              ))}
            </div>
          )}
      </Card>

      {data.recent_consents.length > 0 && (
        <Card className="mb-4">
          <div className="tile-label mb-2">Answers on record</div>
          <div className="divide-y divide-border-soft" data-testid="licence-answers">
            {data.recent_consents.map((c: any) => (
              <div key={c.id} className="flex flex-wrap items-center gap-1.5 py-2">
                <span className="text-sm text-ink mr-auto">{c.subject}</span>
                <ConsentChips consent={c} />
                <span className="chip text-ink-muted">{c.recorded_by}</span>
              </div>
            ))}
          </div>
        </Card>
      )}

      <Card>
        <div className="tile-label mb-2">Mandates by track</div>
        <div className="overflow-x-auto">
          <table className="w-full text-sm" data-testid="licence-mandates">
            <thead>
              <tr className="text-left text-[10px] uppercase tracking-wide text-ink-muted">
                <th className="py-1.5 pr-3 font-medium">Mandate</th>
                <th className="py-1.5 pr-3 font-medium">Household</th>
                <th className="py-1.5 pr-3 font-medium">Type</th>
                <th className="py-1.5 pr-3 font-medium">Track</th>
              </tr>
            </thead>
            <tbody className="divide-y divide-border-soft">
              {mandates.map((m) => (
                <tr key={m.mandate_id}>
                  <td className="py-1.5 pr-3 text-ink">{m.mandate}</td>
                  <td className="py-1.5 pr-3 text-ink-muted">{m.household ?? "—"}</td>
                  <td className="py-1.5 pr-3 text-ink-muted">{titleCase(m.mandate_type)}</td>
                  <td className="py-1.5 pr-3 text-ink-soft">{m.licence_name ?? "—"}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </Card>
    </div>
  );
}
