"use client";

import { useState } from "react";
import { money } from "@/lib/format";
import { CounselChip } from "./GovernanceCard";

// The L100 India items: which product categories open to a household, accredited investors, an adviser's fee terms and the February
// rate card. Figures and chips only: the agent's message carries the sentences, and every source is a chip's title.

const STATE_TONE: Record<string, string> = { open: "text-ok", below: "text-crit", needs_fact: "text-warn" };
const STATE_TEXT: Record<string, string> = { open: "Opens", below: "Below the minimum", needs_fact: "Needs a fact first" };
const ACC_TONE: Record<string, string> = { accredited: "text-ok", lapsed: "text-warn", not_yet: "text-warn", withdrawn: "text-warn", none: "text-ink-muted" };

/** Each category with its minimum and whether the household's recorded assets reach it. */
export function TicketGateRows({ gates }: { gates: any }) {
  return (
    <div className="space-y-1" data-testid="ticket-gates">
      <div className="flex flex-wrap items-center gap-1.5 px-3 py-1.5">
        <span className="chip tabular-nums text-ink-muted" title={gates.assets?.basis}>
          {gates.assets?.amount != null ? `Assets ${money(gates.assets.amount, "INR")}` : "No asset figure on file"}
        </span>
        {(gates.accredited ?? []).length > 0 && <span className="chip text-ok" title={gates.accredited.join(", ")}>Accredited</span>}
      </div>
      <div className="divide-y divide-border-soft">
        {(gates.categories ?? []).map((c: any) => (
          <div key={c.category} className="flex flex-wrap items-center gap-1.5 px-3 py-1.5" data-testid="ticket-gate" title={c.about}>
            <span className="mr-auto text-sm text-ink">{c.label}</span>
            <span className="chip tabular-nums text-ink-muted" title={c.figure}>{money(c.minimum, "INR")}</span>
            {c.waived && <span className="chip text-ok">Waived</span>}
            <span className={`chip ${STATE_TONE[c.state]}`} title={c.said} data-testid="ticket-gate-state">{STATE_TEXT[c.state]}</span>
          </div>
        ))}
      </div>
    </div>
  );
}

/** Every household and the categories that open to it, for the Licences page. */
export function TicketGateFleet({ fleet }: { fleet: any }) {
  return (
    <div className="divide-y divide-border-soft" data-testid="ticket-gate-fleet">
      {(fleet.households ?? []).map((h: any) => (
        <div key={h.household_id} className="flex flex-wrap items-center gap-1.5 px-3 py-1.5" data-testid="ticket-gate-household">
          <span className="mr-auto text-sm text-ink">{h.household}</span>
          <span className="chip tabular-nums text-ink-muted">{h.assets != null ? money(h.assets, "INR") : "No figure"}</span>
          {h.categories.map((c: any) => (
            <span key={c.category} className={`chip ${STATE_TONE[c.state]}`} title={`${c.label}: ${STATE_TEXT[c.state]}, minimum ${money(c.minimum, "INR")}`}>
              {c.category.toUpperCase().replace("_", " ")}
            </span>
          ))}
        </div>
      ))}
    </div>
  );
}

/** Each person with a recorded certificate, and where it stands. */
export function AccreditedRows({ people, criteria }: { people: any[]; criteria?: any }) {
  return (
    <div className="divide-y divide-border-soft" data-testid="accredited-people">
      {people.map((p) => (
        <div key={p.person_id} className="flex flex-wrap items-center gap-1.5 px-3 py-1.5" data-testid="accredited-person" title={(p.criteria ?? []).join("; ")}>
          <span className="mr-auto text-sm text-ink">{p.person}</span>
          <span className={`chip ${ACC_TONE[p.state]}`} data-testid="accredited-state">{p.label}</span>
          {p.certificate_ref && <span className="chip text-ink-muted">{p.certificate_ref}</span>}
          {p.valid_to && <span className="chip tabular-nums text-ink-muted" title="The certificate runs out">to {p.valid_to}</span>}
        </div>
      ))}
      {criteria && (
        <div className="flex flex-wrap items-center gap-1.5 px-3 py-1.5">
          <span className="mr-auto min-w-0 flex-1 text-xs text-ink-soft" title={criteria.source}>{criteria.label}</span>
          <CounselChip counsel={criteria.counsel} />
        </div>
      )}
    </div>
  );
}

function termChips(h: any) {
  return (
    <>
      {h.advance && <span className="chip text-ink-muted" title={h.advance.document}>{h.advance.advance_months} months ahead</span>}
      {h.mode && <span className="chip text-ink-muted" title={h.mode.document}>{h.mode.mode_label} mode since {h.mode.effective_on}</span>}
      {(h.switches ?? []).length > 0 && <span className="chip text-ink-muted" title={(h.switches ?? []).map((s: any) => `${s.effective_on}: ${s.reason}`).join("; ")}>{h.switches.length} switch{h.switches.length === 1 ? "" : "es"}</span>}
      {h.switched_on && <span className="chip text-warn" title="The year's cap is the higher of the two modes">Switched {h.switched_on}</span>}
      {(h.second_opinion ?? []).map((s: any, i: number) => (
        <span key={i} className="chip tabular-nums text-ink-muted" title={`${money(s.assets, "INR")} at ${(s.rate * 100).toFixed(2)}%, ${s.effective_on}`}>
          Second opinion {money(s.fee, "INR")}
        </span>
      ))}
    </>
  );
}

/** Each household's recorded fee terms, and the firm's deposit with IAASB. */
export function FeeTermRows({ data }: { data: any }) {
  const households: any[] = data.households ?? (data.household_id ? [data] : []);
  const dep = data.deposit;
  return (
    <div className="divide-y divide-border-soft" data-testid="fee-terms">
      {households.map((h) => (
        <div key={h.household_id} className="flex flex-wrap items-center gap-1.5 px-3 py-1.5" data-testid="fee-term-household">
          <span className="mr-auto text-sm text-ink" title={h.stands_in}>{h.household}</span>
          {termChips(h)}
        </div>
      ))}
      {dep && (
        <div className="flex flex-wrap items-center gap-1.5 px-3 py-1.5" data-testid="iaasb-deposit">
          <span className="mr-auto text-sm text-ink-soft" title={dep.figure?.source}>Deposit with IAASB</span>
          {dep.recorded ? (
            <>
              <span className="chip text-ink-muted" title={dep.document}>{dep.amount}</span>
              <span className={`chip ${dep.in_liquid_or_overnight ? "text-ok" : "text-ink-muted"}`}>{dep.held_in}</span>
            </>
          ) : <span className="chip text-ink-muted" title={dep.said}>Not recorded</span>}
        </div>
      )}
    </div>
  );
}

/** The figures the Budget or a SEBI circular sets, with counsel's review and any wait for the Budget. */
export function RateCardRows({ card, limit = 12 }: { card: any; limit?: number }) {
  const [all, setAll] = useState(false);
  const figures: any[] = card.figures ?? [];
  const shown = all ? figures : figures.slice(0, limit);
  return (
    <div className="divide-y divide-border-soft" data-testid="rate-card">
      {shown.map((f) => (
        <div key={f.key} className="flex flex-wrap items-center gap-1.5 px-3 py-1.5" data-testid="rate-card-figure">
          <span className="mr-auto min-w-0 flex-1 text-xs text-ink-soft" title={f.source}>{f.label}</span>
          <span className="chip text-ink-muted">{f.kind_label}</span>
          <span className="chip tabular-nums text-ink-muted" title="The figure's own as-of day">as of {f.as_of}</span>
          <CounselChip counsel={f.counsel} />
        </div>
      ))}
      {!all && figures.length > limit && (
        <button type="button" className="chip m-2 text-ink-muted" onClick={() => setAll(true)}>+{figures.length - limit} more</button>
      )}
    </div>
  );
}

export function RateCardChips({ card }: { card: any }) {
  return (
    <>
      <span className="chip text-ink-muted">{card.counts.budget} Budget</span>
      <span className="chip text-ink-muted">{card.counts.sebi_circular} SEBI circular</span>
      {card.counts.awaiting_budget > 0 && <span className="chip text-warn">{card.counts.awaiting_budget} awaiting the Budget</span>}
      <span className={`chip ${card.task?.raised ? "text-ok" : "text-ink-muted"}`} title={`The review task is raised each February; next ${card.next_task_on}`}>
        {card.task?.raised ? `${card.year} task raised` : `Task ${card.next_task_on}`}
      </span>
    </>
  );
}

/** A tool result for the conversation: whichever of the four it is. */
export function IndiaGatesCard({ result }: { result: any }) {
  if (result.applies === false) {
    return <div className="rounded-lg border border-border bg-surface px-3 py-2"><span className="chip text-ink-muted">Not an Indian firm</span></div>;
  }
  const kind = Array.isArray(result.categories) ? "gates" : result.fleet ? "fleet" : Array.isArray(result.people) ? "accredited" : result.counts?.budget != null ? "rate" : "fees";
  const title = { gates: "Product categories open", fleet: "Product categories open, by household", accredited: "Accredited investors", rate: "Rate card", fees: "Adviser fee terms" }[kind];
  return (
    <div className="overflow-hidden rounded-lg border border-border bg-surface" data-testid="india-gates-card">
      <div className="flex flex-wrap items-center justify-between gap-2 border-b border-border-soft px-3 py-2">
        <div className="text-sm font-medium text-ink">{title}</div>
        {kind === "rate" && <div className="flex flex-wrap gap-1.5"><RateCardChips card={result} /></div>}
      </div>
      {kind === "gates" && <TicketGateRows gates={result} />}
      {kind === "fleet" && <TicketGateFleet fleet={result} />}
      {kind === "accredited" && <AccreditedRows people={result.people} criteria={result.criteria} />}
      {kind === "rate" && <RateCardRows card={result} />}
      {kind === "fees" && <FeeTermRows data={result} />}
    </div>
  );
}
