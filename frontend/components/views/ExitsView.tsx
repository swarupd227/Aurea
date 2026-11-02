"use client";

import { useState } from "react";
import { Card, Empty, ErrorState, SkeletonList } from "@/components/ui";
import { api } from "@/lib/api";
import { money } from "@/lib/format";
import { useApi } from "@/lib/hooks";
import { withRef } from "@/lib/refs";
import { reviewDay } from "@/app/workspace/components/artifacts/ReviewsCard";
import { ExitChips, whoFor, type ClientExit, type ExitAccount } from "@/app/workspace/components/artifacts/ExitsCard";
import { ViewLink, useViewNav } from "./ViewNav";
import { AllClients, ViewHeader } from "./ViewHeader";

type Form =
  | { kind: "departure"; reason: string; source_document: string; receiving_institution: string }
  | { kind: "death"; person_id: string; date_of_death: string; source_document: string; executor: string }
  | { kind: "executor"; exit: ClientExit; executor: string; source_document: string }
  | { kind: "transfer"; exit: ClientExit; account: ExitAccount; to_party: string; transfer_date: string; source_document: string; share_pct: string }
  | { kind: "withdraw"; exit: ClientExit; reason: string };

/** Clients leaving and estates being settled. Opened on one client, a departure or a death can be recorded.
 * In the work pane each act is the conversation's tool behind its Confirm card; on a page it acts directly. */
export function ExitsView({ household = null }: { household?: string | null }) {
  const nav = useViewNav();
  const { data, loading, error, refetch } = useApi<any>(household ? `/api/exits?household=${household}` : "/api/exits", [household]);
  const [form, setForm] = useState<Form | null>(null);
  const [busy, setBusy] = useState(false);
  const [msg, setMsg] = useState<string | null>(null);

  if (error) return <div className="view-container"><Card><ErrorState what="departures and estates" message={error} onRetry={refetch} /></Card></div>;
  // Moving between the firm and one client, the last response is still here for a render.
  if (loading || !data || (data.household_id ?? null) !== household) return <div className="view-container"><SkeletonList count={3} /></div>;
  const s = data.summary;
  const rows: ClientExit[] = data.exits;
  const name: string | null = data.household ?? null;

  /** The sentence for Astra, and the call a page makes itself. */
  function plan(f: Form): { say: string; path: string; body?: any } | null {
    if (f.kind === "departure") {
      const to = f.receiving_institution.trim();
      return {
        say: withRef(`${name} is leaving: ${f.reason.trim()}.${to ? ` Going to ${to}.` : ""} From the document "${f.source_document.trim()}".`, "household", household!),
        path: "/api/exits/departure",
        body: { household_id: household, reason: f.reason, source_document: f.source_document, receiving_institution: to || null },
      };
    }
    if (f.kind === "death") {
      const who = data.people.find((p: any) => p.person_id === f.person_id)?.name;
      const executor = f.executor.trim();
      return {
        say: withRef(`Record the death of ${who} on ${f.date_of_death}, from the document "${f.source_document.trim()}".${executor ? ` Executor: ${executor}.` : ""}`, "person", f.person_id),
        path: "/api/exits/death",
        body: { person_id: f.person_id, date_of_death: f.date_of_death, source_document: f.source_document, executor: executor || null },
      };
    }
    if (f.kind === "executor") {
      return {
        say: withRef(`Record ${f.executor.trim()} as executor of the estate of ${f.exit.person}, from the document "${f.source_document.trim()}".`, "exit", f.exit.id),
        path: `/api/exits/${f.exit.id}/executor`, body: { executor: f.executor, source_document: f.source_document },
      };
    }
    if (f.kind === "transfer") {
      const to = f.to_party.trim();
      const share = f.share_pct.trim();
      return {
        say: withRef(withRef(`Record what the custodian moved out of ${f.account.name}${to ? ` to ${to}` : ""} on ${f.transfer_date}${share ? `, ${share}% of the account` : ""}, from the document "${f.source_document.trim()}".`, "exit", f.exit.id), "account", f.account.account_id),
        path: `/api/exits/${f.exit.id}/transfer`,
        body: { account_id: f.account.account_id, transfer_date: f.transfer_date, source_document: f.source_document, to_party: to || null, share_pct: share ? Number(share) : null },
      };
    }
    return {
      say: withRef(`Withdraw this ${f.exit.kind === "death" ? "estate" : "departure"} for ${whoFor(f.exit)}. Reason: ${f.reason.trim()}`, "exit", f.exit.id),
      path: `/api/exits/${f.exit.id}/withdraw`, body: { reason: f.reason },
    };
  }

  async function act(p: { say: string; path: string; body?: any } | null) {
    if (!p) return;
    if (nav.say) {
      nav.say(p.say);
      setForm(null);
      return;
    }
    setBusy(true);
    setMsg(null);
    try {
      await api<any>(p.path, { method: "POST", body: p.body });
      setForm(null);
      await refetch();
    } catch (e: any) {
      setMsg(e?.message ?? "That did not work.");
    } finally {
      setBusy(false);
    }
  }

  const close = (e: ClientExit) => act({
    say: withRef(`Close this ${e.kind === "death" ? "estate" : "departure"} for ${whoFor(e)}.`, "exit", e.id), path: `/api/exits/${e.id}/close`,
  });
  const ready = !!form && (
    form.kind === "departure" ? form.reason.trim().length >= 3 && form.source_document.trim().length >= 5
      : form.kind === "death" ? !!form.person_id && !!form.date_of_death && form.source_document.trim().length >= 5
        : form.kind === "executor" ? form.executor.trim().length >= 2 && form.source_document.trim().length >= 5
          : form.kind === "transfer" ? !!form.transfer_date && form.source_document.trim().length >= 5
            : form.reason.trim().length >= 3);
  const set = (patch: Partial<Form>) => setForm({ ...(form as Form), ...patch } as Form);
  const buttons = (
    <>
      <button className="btn-primary text-xs" data-testid="exit-confirm" disabled={busy || !ready} onClick={() => act(form && plan(form))}>{nav.say ? "Ask Astra" : "Confirm"}</button>
      <button className="btn-outline text-xs" disabled={busy} onClick={() => setForm(null)}>Not now</button>
    </>
  );

  return (
    <div className="view-container">
      <ViewHeader title={name ? `Departures and estates · ${name}` : "Departures and estates"} actions={household ? <AllClients href="/studio/exits" /> : undefined} />
      {msg && <div className="mb-4 font-mono text-xs text-ink-muted" role="status" data-testid="exit-status">{msg}</div>}

      <div className="mb-4 grid grid-cols-2 gap-3 cq-md:grid-cols-4" data-testid="exits-summary">
        {[["Leaving", s.departures, "text-ink"], ["Estates", s.estates, "text-ink"], ["Stayed at the firm", money(s.stayed_at_firm), "text-ok"], ["Left the firm", money(s.left_firm), "text-ink-muted"]].map(([label, v, tone]: any) => (
          <Card key={label}>
            <div className="tile-label">{label}</div>
            <div className={`font-mono text-lg tabular-nums ${tone}`}>{v}</div>
          </Card>
        ))}
      </div>

      <Card className="mb-4">
        <div className="flex flex-wrap gap-1.5">
          <span className="chip text-ink-muted">recorded from a document</span>
          <span className="chip text-ink-muted">nothing moves until the custodian moves it</span>
          <span className="chip text-ink-muted">estate tax not modelled</span>
        </div>
      </Card>

      {household && (data.can_record ? (
        <Card className="mb-4">
          {form?.kind === "departure" ? (
            <div className="flex flex-wrap items-center gap-2" data-testid="exit-departure-form">
              <input className="input min-w-48 flex-1 text-xs" aria-label="Reason" placeholder="Why they are leaving" value={form.reason} onChange={(e) => set({ reason: e.target.value })} />
              <input className="input min-w-40 flex-1 text-xs" aria-label="Going to" placeholder="Going to" value={form.receiving_institution} onChange={(e) => set({ receiving_institution: e.target.value })} />
              <input className="input min-w-48 flex-1 text-xs" aria-label="Source document" placeholder="Source document" value={form.source_document} onChange={(e) => set({ source_document: e.target.value })} />
              {buttons}
            </div>
          ) : form?.kind === "death" ? (
            <div className="flex flex-wrap items-center gap-2" data-testid="exit-death-form">
              <select className="input text-xs" aria-label="Person" value={form.person_id} onChange={(e) => set({ person_id: e.target.value })}>
                <option value="">Person</option>
                {data.people.map((p: any) => <option key={p.person_id} value={p.person_id}>{p.name}</option>)}
              </select>
              <input type="date" className="input text-xs" aria-label="Date of death" value={form.date_of_death} max={data.as_of} onChange={(e) => set({ date_of_death: e.target.value })} />
              <input className="input min-w-48 flex-1 text-xs" aria-label="Source document" placeholder="Source document" value={form.source_document} onChange={(e) => set({ source_document: e.target.value })} />
              <input className="input min-w-40 flex-1 text-xs" aria-label="Executor" placeholder="Executor, if named" value={form.executor} onChange={(e) => set({ executor: e.target.value })} />
              {buttons}
            </div>
          ) : (
            <div className="flex flex-wrap gap-1.5">
              <button className="btn-outline text-xs" data-testid="exit-record-departure" onClick={() => setForm({ kind: "departure", reason: "", source_document: "", receiving_institution: "" })}>Record a departure</button>
              <button className="btn-outline text-xs" data-testid="exit-record-death" onClick={() => setForm({ kind: "death", person_id: "", date_of_death: "", source_document: "", executor: "" })}>Record a death</button>
            </div>
          )}
        </Card>
      ) : null)}

      {rows.length === 0 ? <Empty>None on file.</Empty> : (
        <div className="space-y-4" data-testid="exit-rows">
          {rows.map((e) => {
            const moved = e.accounts.some((a) => a.transfers.length > 0);
            return (
              <Card key={e.id} className="overflow-hidden p-0" data-testid="exit-row">
                <div className="flex flex-wrap items-center justify-between gap-2 border-b border-border-soft px-4 py-2.5">
                  <div>
                    <ViewLink href={`/studio/clients/${e.household_id}`} className="text-sm text-ink hover:underline">{whoFor(e)}</ViewLink>
                    <span className="ml-2 font-mono text-xs text-ink-muted" title={e.document}>{reviewDay(e.event_date)}</span>
                    {e.value_then != null && <span className="ml-2 font-mono text-xs tabular-nums text-ink-muted">{money(e.value_then)}</span>}
                    {e.executor && <span className="ml-2 text-xs text-ink-muted">{e.executor}</span>}
                  </div>
                  {e.status === "open" && (
                    <div className="flex flex-wrap gap-1.5">
                      {e.kind === "death" && !e.executor && data.can_record && (
                        <button className="btn-outline text-xs" data-testid="exit-executor" onClick={() => setForm({ kind: "executor", exit: e, executor: "", source_document: "" })}>Executor</button>
                      )}
                      {data.can_close && e.checks.accounts_open === 0 && (
                        <button className="btn-outline text-xs" data-testid="exit-close" disabled={busy} onClick={() => close(e)}>Close</button>
                      )}
                      {data.can_withdraw && !moved && (
                        <button className="btn-outline text-xs" data-testid="exit-withdraw" onClick={() => setForm({ kind: "withdraw", exit: e, reason: "" })}>Withdraw</button>
                      )}
                    </div>
                  )}
                </div>
                <div className="px-4 py-2"><ExitChips exit={e} /></div>
                {form && "exit" in form && form.exit.id === e.id && form.kind !== "transfer" && (
                  <div className="flex flex-wrap items-center gap-2 border-t border-border-soft bg-border-soft/30 px-4 py-2.5" data-testid="exit-form">
                    {form.kind === "executor" ? (
                      <>
                        <input className="input min-w-40 flex-1 text-xs" aria-label="Executor" placeholder="Executor" value={form.executor} onChange={(ev) => set({ executor: ev.target.value })} />
                        <input className="input min-w-48 flex-1 text-xs" aria-label="Source document" placeholder="Grant or letter" value={form.source_document} onChange={(ev) => set({ source_document: ev.target.value })} />
                      </>
                    ) : (
                      <input className="input min-w-48 flex-1 text-xs" aria-label="Reason" placeholder="Why it is being withdrawn" value={form.reason} onChange={(ev) => set({ reason: ev.target.value })} />
                    )}
                    {buttons}
                  </div>
                )}
                <div className="overflow-x-auto">
                  <table className="w-full text-sm" data-testid="exit-accounts">
                    <thead>
                      <tr className="text-left text-[10px] uppercase tracking-wide text-ink-muted">
                        <th className="px-4 py-2 font-medium">Account</th>
                        <th className="px-4 py-2 text-right font-medium">Value then</th>
                        <th className="px-4 py-2 text-right font-medium">Left</th>
                        <th className="px-4 py-2 font-medium">Paid or moved</th>
                        <th className="px-4 py-2 text-right font-medium">Act</th>
                      </tr>
                    </thead>
                    <tbody className="divide-y divide-border-soft">
                      {e.accounts.map((a) => {
                        const open = form?.kind === "transfer" && form.account.account_id === a.account_id;
                        return (
                          <tr key={a.account_id}>
                            <td className="px-4 py-2 text-ink">
                              {a.name}
                              {a.status === "closed" && <span className="chip ml-1.5 text-ink-muted">closed</span>}
                              {a.unpriced.length > 0 && <span className="chip ml-1.5 text-warn" title={a.unpriced.join(", ")}>no real price {a.unpriced.length}</span>}
                            </td>
                            <td className="px-4 py-2 text-right font-mono text-xs tabular-nums text-ink-muted">{a.value_then != null ? money(a.value_then) : "—"}</td>
                            <td className="px-4 py-2 text-right font-mono text-xs tabular-nums text-ink">{money(a.value_left)}</td>
                            <td className="px-4 py-2">
                              <div className="flex flex-wrap gap-1.5">
                                {a.beneficiaries.map((b) => (
                                  <span key={b.name} className={`chip ${b.paid ? "text-ok" : "text-ink-muted"}`} data-testid="exit-beneficiary">{b.name} {b.percentage}%</span>
                                ))}
                                {a.transfers.filter((t) => !a.beneficiaries.some((b) => b.name === t.to)).map((t) => (
                                  <span key={t.id} className="chip text-ok" title={t.document}>{t.to} {money(t.value)}</span>
                                ))}
                              </div>
                              {open && (
                                <div className="mt-2 flex flex-wrap items-center gap-2" data-testid="exit-transfer-form">
                                  <input className="input min-w-36 flex-1 text-xs" aria-label="To" placeholder={e.receiving_institution ?? "To"} value={form.to_party} onChange={(ev) => set({ to_party: ev.target.value })} />
                                  <input className="input w-20 text-right font-mono text-xs" inputMode="decimal" aria-label="Share percent" placeholder="Share %" value={form.share_pct} onChange={(ev) => set({ share_pct: ev.target.value })} />
                                  <input type="date" className="input text-xs" aria-label="Transfer date" value={form.transfer_date} min={e.event_date} max={data.as_of} onChange={(ev) => set({ transfer_date: ev.target.value })} />
                                  <input className="input min-w-44 flex-1 text-xs" aria-label="Source document" placeholder="Custodian's confirmation" value={form.source_document} onChange={(ev) => set({ source_document: ev.target.value })} />
                                  {buttons}
                                </div>
                              )}
                            </td>
                            <td className="px-4 py-2 text-right">
                              {e.status === "open" && a.status !== "closed" && data.can_move && !open && (
                                <button className="btn-outline text-xs" data-testid="exit-transfer" onClick={() => setForm({ kind: "transfer", exit: e, account: a, to_party: "", transfer_date: data.as_of, source_document: "", share_pct: "" })}>Record</button>
                              )}
                            </td>
                          </tr>
                        );
                      })}
                    </tbody>
                  </table>
                </div>
              </Card>
            );
          })}
        </div>
      )}
    </div>
  );
}
