"use client";
import { useState } from "react";
import { Check, X } from "lucide-react";
import { api, getUser } from "@/lib/api";
import { money } from "@/lib/format";

const CHANNELS = [
  ["email", "Email"],
  ["recorded_call", "Recorded call"],
  ["signed_document", "Signed document"],
] as const;
const RECORDERS = ["adviser", "paraplanner"];
export const CHANNEL_TEXT: Record<string, string> = {
  email: "Email", recorded_call: "Recorded call", signed_document: "Signed document", simulated: "Simulated",
};

/** How a client's answer stands, once there is one. */
export function ConsentChips({ consent }: { consent?: any }) {
  if (!consent || consent.status === "awaiting") return null;
  if (consent.status === "not_permitted") return <span className="chip text-warn">This licence may not execute</span>;
  return (
    <>
      <span className={`chip ${consent.status === "given" ? "text-ok" : "text-warn"}`} data-testid="rec-consent-state"
        title={consent.evidence_ref || undefined}>
        Client {consent.status === "given" ? "consented" : "declined"}{consent.simulated ? "" : ` · ${CHANNEL_TEXT[consent.channel] ?? consent.channel}`}
      </span>
      {consent.simulated && <span className="chip text-warn" data-testid="rec-consent-simulated">Simulated</span>}
    </>
  );
}

/** Record a client's answer. A person does this from the evidence: a yes places the orders. */
export function ConsentPanel({ recId, consent, onDone, demonstration }: {
  recId: string;
  consent: any;
  onDone: (result: any) => void;
  demonstration?: boolean;
}) {
  const [channel, setChannel] = useState("email");
  const [evidence, setEvidence] = useState("");
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState<string | null>(null);
  const [confirm, setConfirm] = useState<null | { given: boolean; simulated?: boolean }>(null);
  const user = getUser();
  const canRecord = RECORDERS.includes(user?.role);
  const simulate = demonstration ?? !!user?.firm?.demonstration;
  const orders: any[] = consent.orders || [];

  async function send(given: boolean, simulated: boolean) {
    setBusy(true);
    setErr(null);
    try {
      const result = await api(`/api/licences/consents/${recId}`, {
        body: { given, channel: simulated ? "simulated" : channel, evidence_ref: simulated ? null : evidence },
      });
      onDone(result);
    } catch (e: any) {
      setErr(e.message);
      setConfirm(null);
    } finally {
      setBusy(false);
    }
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
      {canRecord && !confirm && (
        <div className="flex flex-wrap items-end gap-2">
          <div>
            <label className="label text-[11px]">How</label>
            <select className="input text-sm" value={channel} aria-label="How the client answered"
              data-testid="consent-channel" onChange={(e) => setChannel(e.target.value)}>
              {CHANNELS.map(([k, v]) => <option key={k} value={k}>{v}</option>)}
            </select>
          </div>
          <div className="flex-1 min-w-[12rem]">
            <label className="label text-[11px]">Where it is on file</label>
            <input className="input text-sm" value={evidence} aria-label="Where the client's answer is on file"
              data-testid="consent-evidence" onChange={(e) => setEvidence(e.target.value)} />
          </div>
          <button className="btn-outline" data-testid="consent-decline" disabled={busy || !evidence.trim()}
            onClick={() => setConfirm({ given: false })}><X size={15} /> Declined</button>
          <button className="btn-primary" data-testid="consent-given" disabled={busy || !evidence.trim()}
            onClick={() => setConfirm({ given: true })}><Check size={15} /> Consented</button>
          {simulate && (
            <button className="btn-outline" data-testid="consent-simulate" disabled={busy}
              onClick={() => setConfirm({ given: true, simulated: true })}>Simulate client</button>
          )}
        </div>
      )}
      {canRecord && confirm && (
        <div className="flex flex-wrap items-center justify-end gap-2">
          <span className="chip text-warn mr-auto">
            {confirm.simulated ? "Simulated consent · orders are placed" : confirm.given ? "Orders are placed" : "Proposal closes"}
          </span>
          <button className="btn-ghost" disabled={busy} onClick={() => setConfirm(null)}>Not now</button>
          <button className="btn-primary" data-testid="consent-confirm" disabled={busy}
            onClick={() => send(confirm.given, !!confirm.simulated)}>{busy ? "Recording…" : "Confirm"}</button>
        </div>
      )}
      {err && <div className="text-xs text-crit">{err}</div>}
    </div>
  );
}
