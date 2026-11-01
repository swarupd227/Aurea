"use client";

import { Fragment, useState } from "react";
import { Card, Empty, ErrorState, SkeletonList } from "@/components/ui";
import { api } from "@/lib/api";
import { money } from "@/lib/format";
import { useApi } from "@/lib/hooks";
import { withRef } from "@/lib/refs";
import { reviewDay } from "@/app/workspace/components/artifacts/ReviewsCard";
import {
  BasisChip, CashFlowChip, kindText, outsideBook, type BalanceItem, type BalanceSheet,
} from "@/app/workspace/components/artifacts/BalanceSheetCard";
import { ViewLink, useViewNav } from "./ViewNav";
import { ViewHeader } from "./ViewHeader";

interface Panel {
  b: BalanceSheet;
  kind: string;
  person_id: string;
  name: string;
  amount: string;
  as_of: string;
  source_document: string;
  annual_payment: string;
  interest_rate: string;
  ends_on: string;
  replaces: BalanceItem | null;
}

const number = (v: string) => (v.trim() === "" ? null : Number(v));

/** The whole record in words, so the conversation's Confirm card has every figure to name. */
function spoken(p: Panel): string {
  const whose = p.person_id ? ` for ${p.b.people.find((x) => x.person_id === p.person_id)?.name}` : ` for ${p.b.household}, jointly`;
  const sentence = [
    `${p.replaces ? `Replace ${p.replaces.name} with a new figure for this` : "Record this"} ${kindText(p.kind)}${whose}: ${p.name.trim()}, ${p.amount}, as at ${p.as_of}, from the document "${p.source_document.trim()}".`,
    p.annual_payment.trim() ? `Annual payment ${p.annual_payment}.` : "",
    p.interest_rate.trim() ? `Interest rate ${p.interest_rate}%.` : "",
    p.ends_on ? `Ends on ${p.ends_on}.` : "",
  ].filter(Boolean).join(" ");
  const withHousehold = withRef(sentence, "household", p.b.household_id);
  return p.person_id ? withRef(withHousehold, "person", p.person_id) : withHousehold;
}

/** What each household owns, owes and spends beyond the managed book. In the work pane, a change is
 * the conversation's tool behind its Confirm card; on a page the form acts directly. */
export function NetWorthView() {
  const nav = useViewNav();
  const { data, loading, error, refetch } = useApi<any>("/api/balance-sheet");
  const [msg, setMsg] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [panel, setPanel] = useState<Panel | null>(null);
  const [opened, setOpened] = useState<string | null>(null);
  const [ending, setEnding] = useState<{ id: string; reason: string } | null>(null);

  if (error) return <div className="view-container"><Card><ErrorState what="net worth" message={error} onRetry={refetch} /></Card></div>;
  if (loading || !data) return <div className="view-container"><SkeletonList count={4} /></div>;
  const s = data.summary;
  const rows: BalanceSheet[] = data.households;
  const kinds: Record<string, string[]> = data.kinds;
  const canRecord: boolean = data.can_record;
  const isDebt = !!panel && kinds.liability.includes(panel.kind);
  const ready = !!panel && panel.name.trim().length >= 2 && Number(panel.amount) > 0 && !!panel.as_of && panel.source_document.trim().length >= 3;

  const toRecord = (b: BalanceSheet, replaces: BalanceItem | null = null): Panel => ({
    b, replaces, kind: replaces?.kind ?? "mortgage", person_id: replaces?.person_id ?? "", name: replaces?.name ?? "",
    amount: "", as_of: data.as_of, source_document: "", annual_payment: replaces?.annual_payment?.toString() ?? "",
    interest_rate: replaces?.interest_rate != null ? String(Math.round(replaces.interest_rate * 1000000) / 10000) : "",
    ends_on: replaces?.ends_on ?? "",
  });

  async function send() {
    if (!panel) return;
    if (nav.say) {
      nav.say(spoken(panel));
      setPanel(null);
      return;
    }
    setBusy(true);
    setMsg(null);
    try {
      const r = await api<any>(`/api/balance-sheet/households/${panel.b.household_id}`, {
        method: "POST",
        body: {
          kind: panel.kind, name: panel.name, amount: Number(panel.amount), as_of: panel.as_of, source_document: panel.source_document,
          person_id: panel.person_id || null, annual_payment: isDebt ? number(panel.annual_payment) : null,
          interest_rate: isDebt && number(panel.interest_rate) != null ? Number(panel.interest_rate) / 100 : null,
          ends_on: isDebt ? panel.ends_on || null : null, replaces_id: panel.replaces?.id ?? null,
        },
      });
      setMsg(`${r.household}: net worth ${money(r.net_worth)}`);
      setPanel(null);
      await refetch();
    } catch (e: any) {
      setMsg(e?.message ?? "That did not work.");
    } finally {
      setBusy(false);
    }
  }

  async function end(i: BalanceItem) {
    if (!ending) return;
    if (nav.say) {
      nav.say(withRef(`End ${i.name} (${kindText(i.kind)}). Reason: ${ending.reason.trim()}`, "balance_item", i.id));
      setEnding(null);
      return;
    }
    setBusy(true);
    setMsg(null);
    try {
      const r = await api<any>(`/api/balance-sheet/items/${i.id}/end`, { method: "POST", body: { reason: ending.reason } });
      setMsg(`${r.household}: net worth ${money(r.net_worth)}`);
      setEnding(null);
      await refetch();
    } catch (e: any) {
      setMsg(e?.message ?? "That did not work.");
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="view-container">
      <ViewHeader title="Net worth" />
      {msg && <div className="mb-4 font-mono text-xs text-ink-muted" role="status" data-testid="balance-status">{msg}</div>}

      <div className="mb-4 grid grid-cols-2 gap-3 cq-md:grid-cols-4" data-testid="balance-summary">
        {[
          ["With debts on file", s.with_liabilities, "text-ok"],
          ["Assets only", s.assets_only, s.assets_only ? "text-warn" : "text-ink-muted"],
          ["Cash flow assessed", s.cash_flow_assessed, "text-ink-muted"],
          ["Not converted", s.not_converted, s.not_converted ? "text-warn" : "text-ink-muted"],
        ].map(([label, v, tone]: any) => (
          <Card key={label}>
            <div className="tile-label">{label}</div>
            <div className={`font-mono text-2xl tabular-nums ${tone}`}>{v}</div>
          </Card>
        ))}
      </div>

      <Card className="mb-4">
        <div className="flex flex-wrap gap-1.5" data-testid="balance-basis">
          <span className="chip text-ink-muted">recorded from a document</span>
          <span className="chip text-ink-muted">no debt on file does not mean no debt</span>
          <span className="chip text-ink-muted">other currencies are not converted</span>
        </div>
      </Card>

      {rows.length === 0 ? <Empty>No households.</Empty> : (
        <Card className="overflow-hidden p-0">
          <div className="overflow-x-auto">
            <table className="w-full text-sm" data-testid="balance-rows">
              <thead>
                <tr className="text-left text-[10px] uppercase tracking-wide text-ink-muted">
                  <th className="px-4 py-2 font-medium">Client</th>
                  <th className="px-4 py-2 text-right font-medium">Managed</th>
                  <th className="px-4 py-2 text-right font-medium">Outside the book</th>
                  <th className="px-4 py-2 text-right font-medium">Owed</th>
                  <th className="px-4 py-2 text-right font-medium">Net worth</th>
                  <th className="px-4 py-2 font-medium">Status</th>
                  <th className="px-4 py-2 text-right font-medium">Act</th>
                </tr>
              </thead>
              <tbody className="divide-y divide-border-soft">
                {rows.map((b) => {
                  const open = panel?.b.household_id === b.household_id ? panel : null;
                  const showing = opened === b.household_id;
                  return (
                    <Fragment key={b.household_id}>
                      <tr>
                        <td className="px-4 py-2">
                          <ViewLink href={`/studio/clients/${b.household_id}`} className="text-ink hover:underline" data-testid="balance-client">{b.household}</ViewLink>
                        </td>
                        <td className="px-4 py-2 text-right font-mono text-xs tabular-nums text-ink-muted">{money(b.assets.managed)}</td>
                        <td className="px-4 py-2 text-right font-mono text-xs tabular-nums text-ink-muted"
                          title={`held away ${money(b.assets.held_away)} · retirement ${money(b.assets.retirement_accounts)} · cash ${money(b.assets.cash)} · property ${money(b.assets.property)}`}>{money(outsideBook(b))}</td>
                        <td className="px-4 py-2 text-right font-mono text-xs tabular-nums text-ink-muted">{b.basis === "assets_only" ? "—" : money(b.liabilities.total)}</td>
                        <td className="px-4 py-2 text-right font-mono text-xs tabular-nums text-ink">{money(b.net_worth)}</td>
                        <td className="px-4 py-2">
                          <div className="flex flex-wrap gap-1.5">
                            <BasisChip basis={b.basis} />
                            <CashFlowChip flow={b.cash_flow} />
                            {b.not_converted.length > 0 && <span className="chip text-warn">{b.not_converted.length} not converted</span>}
                          </div>
                        </td>
                        <td className="px-4 py-2 text-right">
                          {!open && (
                            <div className="flex justify-end gap-1.5">
                              {b.items.length + b.not_converted.length > 0 && (
                                <button className="btn-outline text-xs" data-testid="balance-items" aria-expanded={showing}
                                  onClick={() => setOpened(showing ? null : b.household_id)}>Items</button>
                              )}
                              <button className="btn-outline text-xs" data-testid="balance-record" disabled={!canRecord || busy}
                                title={!canRecord ? "Only an adviser, paraplanner or operations records these" : undefined}
                                onClick={() => { setMsg(null); setPanel(toRecord(b)); }}>Record</button>
                            </div>
                          )}
                        </td>
                      </tr>
                      {showing && (
                        <tr>
                          <td colSpan={7} className="bg-border-soft/30 px-4 py-3">
                            <table className="w-full text-xs" data-testid="balance-item-rows">
                              <tbody className="divide-y divide-border-soft">
                                {[...b.items, ...b.not_converted].map((i) => (
                                  <tr key={i.id} title={i.source_document}>
                                    <td className="py-1.5 pr-3 text-ink">{i.name}<span className="ml-1 text-ink-muted">{i.person ?? "joint"}</span></td>
                                    <td className="py-1.5 pr-3 text-ink-muted">{kindText(i.kind)}</td>
                                    <td className="py-1.5 pr-3 text-right font-mono tabular-nums text-ink-muted">
                                      {money(i.amount, i.currency)}{i.side === "expense" ? "/yr" : ""}
                                      {i.annual_payment != null && <span className="ml-1">· {money(i.annual_payment, i.currency)}/yr</span>}
                                    </td>
                                    <td className="py-1.5 pr-3 font-mono text-ink-muted">{reviewDay(i.as_of)}</td>
                                    <td className="py-1.5 text-right">
                                      {canRecord && (ending?.id === i.id ? (
                                        <div className="flex flex-wrap items-center justify-end gap-1.5" data-testid="balance-end">
                                          <input className="input w-44 py-1 text-xs" aria-label="Reason" placeholder="Repaid, sold, closed…"
                                            value={ending.reason} onChange={(e) => setEnding({ ...ending, reason: e.target.value })} />
                                          <button className="btn-primary text-xs" disabled={busy || ending.reason.trim().length < 3} onClick={() => end(i)}>
                                            {nav.say ? "Ask Astra" : "Confirm"}
                                          </button>
                                          <button className="btn-outline text-xs" disabled={busy} onClick={() => setEnding(null)}>Not now</button>
                                        </div>
                                      ) : (
                                        <div className="flex justify-end gap-1.5">
                                          <button className="btn-outline text-xs" data-testid="balance-replace" onClick={() => { setMsg(null); setPanel(toRecord(b, i)); }}>New figure</button>
                                          <button className="btn-outline text-xs" data-testid="balance-end-open" onClick={() => setEnding({ id: i.id, reason: "" })}>End</button>
                                        </div>
                                      ))}
                                    </td>
                                  </tr>
                                ))}
                              </tbody>
                            </table>
                          </td>
                        </tr>
                      )}
                      {open && (
                        <tr>
                          <td colSpan={7} className="bg-border-soft/30 px-4 py-3">
                            <div className="flex flex-wrap items-center gap-2" data-testid="balance-confirm">
                              <select className="input text-xs" aria-label="Kind" value={open.kind} disabled={!!open.replaces}
                                onChange={(e) => setPanel({ ...open, kind: e.target.value })}>
                                {Object.entries(kinds).map(([side, list]) => (
                                  <optgroup key={side} label={side}>
                                    {list.map((k) => <option key={k} value={k}>{kindText(k)}</option>)}
                                  </optgroup>
                                ))}
                              </select>
                              <select className="input text-xs" aria-label="Whose" value={open.person_id} disabled={!!open.replaces}
                                onChange={(e) => setPanel({ ...open, person_id: e.target.value })}>
                                <option value="">Joint</option>
                                {b.people.map((p) => <option key={p.person_id} value={p.person_id}>{p.name}</option>)}
                              </select>
                              <input className="input min-w-40 flex-1 text-xs" aria-label="Name" placeholder="Institution or lender" value={open.name} disabled={!!open.replaces}
                                onChange={(e) => setPanel({ ...open, name: e.target.value })} />
                              <input className="input w-32 text-right font-mono text-xs" inputMode="decimal" aria-label="Amount" placeholder={kinds.expense.includes(open.kind) ? "A year" : "Amount"} value={open.amount}
                                onChange={(e) => setPanel({ ...open, amount: e.target.value })} />
                              <input type="date" className="input text-xs" aria-label="As at" value={open.as_of} max={data.as_of}
                                onChange={(e) => setPanel({ ...open, as_of: e.target.value })} />
                              <input className="input min-w-48 flex-1 text-xs" aria-label="Source document" placeholder="Source document" value={open.source_document}
                                onChange={(e) => setPanel({ ...open, source_document: e.target.value })} />
                              {isDebt && (
                                <>
                                  <input className="input w-36 text-right font-mono text-xs" inputMode="decimal" aria-label="Annual payment" placeholder="Paid a year" value={open.annual_payment}
                                    onChange={(e) => setPanel({ ...open, annual_payment: e.target.value })} />
                                  <input className="input w-24 text-right font-mono text-xs" inputMode="decimal" aria-label="Interest rate percent" placeholder="Rate %" value={open.interest_rate}
                                    onChange={(e) => setPanel({ ...open, interest_rate: e.target.value })} />
                                  <input type="date" className="input text-xs" aria-label="Ends on" value={open.ends_on} min={data.as_of}
                                    onChange={(e) => setPanel({ ...open, ends_on: e.target.value })} />
                                </>
                              )}
                              <button className="btn-primary text-xs" data-testid="balance-confirm-yes" disabled={busy || !ready} onClick={send}>{nav.say ? "Ask Astra" : "Confirm"}</button>
                              <button className="btn-outline text-xs" disabled={busy} onClick={() => setPanel(null)}>Not now</button>
                            </div>
                          </td>
                        </tr>
                      )}
                    </Fragment>
                  );
                })}
              </tbody>
            </table>
          </div>
        </Card>
      )}
    </div>
  );
}
