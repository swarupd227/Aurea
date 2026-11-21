"use client";

import { useState } from "react";
import { Card, ErrorState, SkeletonList } from "@/components/ui";
import { api } from "@/lib/api";
import { useApi } from "@/lib/hooks";
import { withRef } from "@/lib/refs";
import { HeldAwayChips, HeldAwayRows, type HeldAwayLine } from "@/app/workspace/components/artifacts/HeldAwayCard";
import { useViewNav } from "./ViewNav";

interface Form {
  institution: string;
  instrument_name: string;
  symbol: string;
  quantity: string;
  value: string;
  value_on: string;
  source_document: string;
  owner: string;
}

const blank = (asOf: string): Form => ({ institution: "", instrument_name: "", symbol: "", quantity: "", value: "", value_on: asOf, source_document: "", owner: "" });

/** One household's holdings held elsewhere, on its net worth page (B-198): each line at what its statement states, its
 * year on real prices beside its reference and the shelf, and any overlap with the managed book. In the work pane a
 * change is the conversation's tool behind its Confirm card; on a page the form acts directly. */
export function HeldAwaySection({ householdId, household, canRecord, asOf }: { householdId: string; household: string; canRecord: boolean; asOf: string }) {
  const nav = useViewNav();
  const { data, loading, error, refetch } = useApi<any>(`/api/held-away/households/${householdId}`, [householdId]);
  const [form, setForm] = useState<Form | null>(null);
  const [ending, setEnding] = useState<{ id: string; reason: string } | null>(null);
  const [busy, setBusy] = useState(false);
  const [msg, setMsg] = useState<string | null>(null);

  if (error) return <Card className="mb-4"><ErrorState what="holdings held elsewhere" message={error} onRetry={refetch} /></Card>;
  if (loading || !data) return <SkeletonList count={2} />;
  const lines: HeldAwayLine[] = data.holdings;
  const people: { id: string; name: string; entity?: boolean }[] = data.people;
  const ready = !!form && form.institution.trim().length >= 2 && form.instrument_name.trim().length >= 2 && Number(form.value) >= 0
    && form.value.trim() !== "" && !!form.value_on && form.source_document.trim().length >= 3;

  async function record() {
    if (!form) return;
    const owner = people.find((p) => p.id === form.owner);
    if (nav.say) {
      const sentence = `Record a holding held elsewhere for ${household}${owner ? `, ${owner.name}'s` : ", jointly"}: ${form.instrument_name.trim()}`
        + `${form.symbol.trim() ? ` (${form.symbol.trim().toUpperCase()})` : ""} at ${form.institution.trim()}`
        + `${form.quantity.trim() ? `, ${form.quantity} units` : ""}, valued ${form.value} on ${form.value_on}, from "${form.source_document.trim()}".`;
      const withHousehold = withRef(sentence, "household", householdId);
      nav.say(owner ? withRef(withHousehold, owner.entity ? "entity" : "person", owner.id) : withHousehold);
      setForm(null);
      return;
    }
    setBusy(true);
    setMsg(null);
    try {
      const r = await api<any>(`/api/held-away/households/${householdId}`, {
        method: "POST",
        body: {
          institution: form.institution, instrument_name: form.instrument_name, symbol: form.symbol || null,
          quantity: form.quantity.trim() ? Number(form.quantity) : null, value: Number(form.value), value_on: form.value_on,
          source_document: form.source_document, person_id: owner && !owner.entity ? owner.id : null, entity_id: owner?.entity ? owner.id : null,
        },
      });
      setMsg(`Recorded ${r.recorded}`);
      setForm(null);
      await refetch();
    } catch (e: any) {
      setMsg(e?.message ?? "That did not work.");
    } finally {
      setBusy(false);
    }
  }

  async function end(l: HeldAwayLine) {
    if (!ending) return;
    if (nav.say) {
      nav.say(`End the holding ${l.symbol || l.name} at ${l.institution} for ${household} (holding ${l.id}). Reason: ${ending.reason.trim()}`);
      setEnding(null);
      return;
    }
    setBusy(true);
    setMsg(null);
    try {
      const r = await api<any>(`/api/held-away/holdings/${l.id}/end`, { method: "POST", body: { reason: ending.reason } });
      setMsg(`${r.ended_holding} has ended`);
      setEnding(null);
      await refetch();
    } catch (e: any) {
      setMsg(e?.message ?? "That did not work.");
    } finally {
      setBusy(false);
    }
  }

  return (
    <Card className="mb-4 overflow-hidden p-0" data-testid="held-away-section">
      <div className="flex flex-wrap items-center justify-between gap-2 border-b border-border-soft px-4 py-2">
        <div className="tile-label">Held away</div>
        <div className="flex flex-wrap items-center gap-1.5">
          <HeldAwayChips summary={data.summary} currency={data.currency} />
          <span className="chip text-ink-muted" title={data.rule.text}>{data.rule.period_days} days · {Math.round(data.rule.gap * 100)} pts</span>
          <button className="btn-outline text-xs" data-testid="held-away-record" disabled={!canRecord || busy}
            title={!canRecord ? "Only an adviser, paraplanner or operations records these" : undefined}
            onClick={() => { setMsg(null); setForm(blank(asOf)); }}>Record</button>
        </div>
      </div>
      {msg && <div className="px-4 py-2 font-mono text-xs text-ink-muted" role="status" data-testid="held-away-status">{msg}</div>}
      {form && (
        <div className="flex flex-wrap items-center gap-2 border-b border-border-soft bg-border-soft px-4 py-3" data-testid="held-away-confirm">
          <select className="input text-xs" aria-label="Whose" value={form.owner} onChange={(e) => setForm({ ...form, owner: e.target.value })}>
            <option value="">Joint</option>
            {people.map((p) => <option key={p.id} value={p.id}>{p.name}</option>)}
          </select>
          <input className="input min-w-36 flex-1 text-xs" aria-label="Institution" placeholder="Institution" value={form.institution}
            onChange={(e) => setForm({ ...form, institution: e.target.value })} />
          <input className="input min-w-40 flex-1 text-xs" aria-label="Holding" placeholder="Holding, as the statement names it" value={form.instrument_name}
            onChange={(e) => setForm({ ...form, instrument_name: e.target.value })} />
          <input className="input w-24 font-mono text-xs" aria-label="Symbol" placeholder="Symbol" value={form.symbol}
            onChange={(e) => setForm({ ...form, symbol: e.target.value })} />
          <input className="input w-24 text-right font-mono text-xs" inputMode="decimal" aria-label="Quantity" placeholder="Units" value={form.quantity}
            onChange={(e) => setForm({ ...form, quantity: e.target.value })} />
          <input className="input w-32 text-right font-mono text-xs" inputMode="decimal" aria-label="Value" placeholder="Value stated" value={form.value}
            onChange={(e) => setForm({ ...form, value: e.target.value })} />
          <input type="date" className="input text-xs" aria-label="Statement date" value={form.value_on} max={asOf}
            onChange={(e) => setForm({ ...form, value_on: e.target.value })} />
          <input className="input min-w-48 flex-1 text-xs" aria-label="Statement" placeholder="Statement - institution and date" value={form.source_document}
            onChange={(e) => setForm({ ...form, source_document: e.target.value })} />
          <button className="btn-primary text-xs" data-testid="held-away-confirm-yes" disabled={busy || !ready} onClick={record}>{nav.say ? "Ask Astra" : "Confirm"}</button>
          <button className="btn-outline text-xs" disabled={busy} onClick={() => setForm(null)}>Not now</button>
        </div>
      )}
      {lines.length === 0 ? (
        <div className="px-4 py-3"><span className="chip text-ink-muted">none listed · not on file does not mean none held</span></div>
      ) : (
        <div className="overflow-x-auto">
          <HeldAwayRows rows={lines} act={canRecord ? (l) => (ending?.id === l.id ? (
            <div className="flex flex-wrap items-center justify-end gap-1.5" data-testid="held-away-end">
              <input className="input w-36 py-1 text-xs" aria-label="Reason" placeholder="Sold, transferred in…"
                value={ending.reason} onChange={(e) => setEnding({ ...ending, reason: e.target.value })} />
              <button className="btn-primary text-xs" disabled={busy || ending.reason.trim().length < 3} onClick={() => end(l)}>{nav.say ? "Ask Astra" : "Confirm"}</button>
              <button className="btn-outline text-xs" disabled={busy} onClick={() => setEnding(null)}>Not now</button>
            </div>
          ) : (
            <button className="btn-outline text-xs" data-testid="held-away-end-open" onClick={() => setEnding({ id: l.id, reason: "" })}>End</button>
          )) : undefined} />
        </div>
      )}
    </Card>
  );
}
