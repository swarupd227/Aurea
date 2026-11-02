"use client";

import { useState } from "react";
import { Check, X } from "lucide-react";
import { Card, Empty, Spinner } from "@/components/ui";
import { CHANNEL_TEXT, ConsentChips, ConsentPanel } from "@/components/ConsentPanel";
import { getUser } from "@/lib/api";
import { useApi } from "@/lib/hooks";
import { money, titleCase } from "@/lib/format";
import { withRef } from "@/lib/refs";
import { LicenceChips } from "@/app/workspace/components/artifacts/LicenceCard";
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

/** What each licence lets an agent do, client by client, and the approved proposals waiting for a
 * client's consent. In the work pane a client's answer goes through the conversation's
 * record_client_consent tool; on a page ConsentPanel records it directly. */
export function LicencesView() {
  const nav = useViewNav();
  const { data, loading, refetch } = useApi<any>("/api/licences");
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
