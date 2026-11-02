"use client";

import { Fragment, useState } from "react";
import { Card, Empty, ErrorState, SkeletonList } from "@/components/ui";
import { api } from "@/lib/api";
import { money } from "@/lib/format";
import { useApi } from "@/lib/hooks";
import { withRef } from "@/lib/refs";
import { reviewDay } from "@/app/workspace/components/artifacts/ReviewsCard";
import { PlanChips, pct, type EquityHousehold, type Exposure, type Grant } from "@/app/workspace/components/artifacts/EquityCompCard";
import { ViewLink, useViewNav } from "./ViewNav";
import { AllClients, ViewHeader } from "./ViewHeader";

interface PlanForm {
  exposure: Exposure;
  method: string;
  target: string;
  target_date: string;
  rationale: string;
  source_document: string;
}

const METHODS = ["staged_sale", "10b5_1", "exchange_fund", "collar", "gift", "hold"];

function Exposures({ household, canRecord, say, onSaved, asOf }: { household: EquityHousehold; canRecord: boolean; say: ((s: string) => void) | null; onSaved: () => void; asOf: string }) {
  const [form, setForm] = useState<PlanForm | null>(null);
  const [opened, setOpened] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [msg, setMsg] = useState<string | null>(null);
  const ready = !!form && Number(form.target) >= 0 && form.target.trim() !== "" && !!form.target_date && form.rationale.trim().length >= 5 && form.source_document.trim().length >= 5;

  async function send() {
    if (!form) return;
    const sentence = `Record a ${form.method.replace("_", " ")} plan for ${household.household}'s ${form.exposure.symbol}, down to ${form.target}% by ${form.target_date}: ${form.rationale.trim()}. Agreed in "${form.source_document.trim()}".`;
    if (say) {
      say(withRef(sentence, "household", household.household_id));
      setForm(null);
      return;
    }
    setBusy(true);
    setMsg(null);
    try {
      await api("/api/equity-comp/plans", { method: "POST", body: {
        household_id: household.household_id, symbol: form.exposure.symbol, method: form.method, target_weight_pct: Number(form.target),
        target_date: form.target_date, rationale: form.rationale, source_document: form.source_document,
      } });
      setForm(null);
      onSaved();
    } catch (e: any) {
      setMsg(e?.message ?? "That did not work.");
    } finally {
      setBusy(false);
    }
  }

  return (
    <>
      {msg && <div className="px-4 pb-2 font-mono text-xs text-ink-muted" role="status">{msg}</div>}
      {household.exposures.map((e) => {
        const open = form?.exposure.symbol === e.symbol ? form : null;
        const showing = opened === e.symbol;
        return (
          <Fragment key={e.symbol}>
            <tr>
              <td className="px-4 py-2"><span className="font-mono text-xs text-ink">{e.symbol}</span><span className="ml-2 text-xs text-ink-muted">{e.name}</span></td>
              <td className="px-4 py-2 text-right font-mono text-xs tabular-nums text-ink-muted">{money(e.held)}</td>
              <td className="px-4 py-2 text-right font-mono text-xs tabular-nums text-ink-muted">{e.granted ? money(e.granted) : "—"}</td>
              <td className="px-4 py-2 text-right font-mono text-xs tabular-nums text-ink">{pct(e.weight)}</td>
              <td className="px-4 py-2"><PlanChips exposure={e} /></td>
              <td className="px-4 py-2 text-right">
                <div className="flex justify-end gap-1.5">
                  {e.scenarios.length > 0 && <button className="btn-outline text-xs" data-testid="exposure-scenarios" aria-expanded={showing} onClick={() => setOpened(showing ? null : e.symbol)}>Paths</button>}
                  {canRecord && !open && (
                    <button className="btn-outline text-xs" data-testid="exposure-plan" onClick={() => setForm({ exposure: e, method: e.plan?.method ?? "staged_sale", target: e.plan ? String(e.plan.target_weight * 100) : "", target_date: e.plan?.target_date ?? "", rationale: "", source_document: "" })}>Plan</button>
                  )}
                </div>
              </td>
            </tr>
            {showing && (
              <tr>
                <td colSpan={6} className="bg-border-soft/30 px-4 py-3">
                  <table className="w-full text-xs" data-testid="exposure-paths">
                    <thead><tr className="text-left text-[10px] uppercase tracking-wide text-ink-muted"><th className="py-1 font-medium">Over</th><th className="py-1 text-right font-medium">Sold a year</th><th className="py-1 text-right font-medium">Units a year</th><th className="py-1 text-right font-medium">Gain a year</th></tr></thead>
                    <tbody className="divide-y divide-border-soft">
                      {e.scenarios.map((p) => (
                        <tr key={p.years}>
                          <td className="py-1.5 text-ink">{p.years} years</td>
                          <td className="py-1.5 text-right font-mono tabular-nums text-ink-muted">{money(p.sold_a_year)}</td>
                          <td className="py-1.5 text-right font-mono tabular-nums text-ink-muted">{p.units_a_year.toLocaleString()}</td>
                          <td className="py-1.5 text-right font-mono tabular-nums text-ink-muted">{p.gain_a_year != null ? money(p.gain_a_year) : "—"}</td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </td>
              </tr>
            )}
            {open && (
              <tr>
                <td colSpan={6} className="bg-border-soft/30 px-4 py-3">
                  <div className="flex flex-wrap items-center gap-2" data-testid="plan-form">
                    <select className="input text-xs" aria-label="Method" value={open.method} onChange={(x) => setForm({ ...open, method: x.target.value })}>
                      {METHODS.map((m) => <option key={m} value={m}>{m.replace("_", " ")}</option>)}
                    </select>
                    <input className="input w-24 text-right font-mono text-xs" inputMode="decimal" aria-label="Target percent" placeholder="Target %" value={open.target} onChange={(x) => setForm({ ...open, target: x.target.value })} />
                    <input type="date" className="input text-xs" aria-label="Target date" value={open.target_date} min={asOf} onChange={(x) => setForm({ ...open, target_date: x.target.value })} />
                    <input className="input min-w-44 flex-1 text-xs" aria-label="Rationale" placeholder="Why this plan" value={open.rationale} onChange={(x) => setForm({ ...open, rationale: x.target.value })} />
                    <input className="input min-w-44 flex-1 text-xs" aria-label="Source document" placeholder="Meeting note or letter" value={open.source_document} onChange={(x) => setForm({ ...open, source_document: x.target.value })} />
                    <button className="btn-primary text-xs" data-testid="plan-confirm" disabled={busy || !ready} onClick={send}>{say ? "Ask Astra" : "Confirm"}</button>
                    <button className="btn-outline text-xs" disabled={busy} onClick={() => setForm(null)}>Not now</button>
                  </div>
                </td>
              </tr>
            )}
          </Fragment>
        );
      })}
    </>
  );
}

function Grants({ grants }: { grants: Grant[] }) {
  return (
    <table className="w-full text-sm" data-testid="equity-grants">
      <thead>
        <tr className="text-left text-[10px] uppercase tracking-wide text-ink-muted">
          <th className="px-4 py-2 font-medium">Grant</th>
          <th className="px-4 py-2 text-right font-medium">Vested</th>
          <th className="px-4 py-2 text-right font-medium">Unvested</th>
          <th className="px-4 py-2 font-medium">Next vest</th>
        </tr>
      </thead>
      <tbody className="divide-y divide-border-soft">
        {grants.map((g) => (
          <tr key={g.id} title={g.source_document}>
            <td className="px-4 py-2 text-ink">
              {g.kind.toUpperCase()} {g.symbol ?? g.employer}
              <span className="ml-1 text-xs text-ink-muted">{g.person}</span>
              {g.price == null && <span className="chip ml-1.5 text-warn">no real price</span>}
              {g.expired && <span className="chip ml-1.5 text-crit">expired</span>}
            </td>
            <td className="px-4 py-2 text-right font-mono text-xs tabular-nums text-ink-muted">{g.vested.toLocaleString()}{g.value_vested != null && ` · ${money(g.value_vested)}`}</td>
            <td className="px-4 py-2 text-right font-mono text-xs tabular-nums text-ink-muted">{g.unvested.toLocaleString()}{g.value_unvested != null && ` · ${money(g.value_unvested)}`}</td>
            <td className="px-4 py-2 font-mono text-xs text-ink-muted">{g.next_vest_on ? reviewDay(g.next_vest_on) : "—"}</td>
          </tr>
        ))}
      </tbody>
    </table>
  );
}

/** Grants and concentrated positions, a household's when opened on one. A grant is recorded in the conversation,
 * because its vesting schedule is read from a statement; a plan can be from here. */
export function EquityCompView({ household = null }: { household?: string | null }) {
  const nav = useViewNav();
  const { data, loading, error, refetch } = useApi<any>(household ? `/api/equity-comp?household=${household}` : "/api/equity-comp", [household]);
  if (error) return <div className="view-container"><Card><ErrorState what="equity compensation" message={error} onRetry={refetch} /></Card></div>;
  // Moving between the firm and one client, the last response is still here for a render.
  if (loading || !data || (data.household_id ?? null) !== household) return <div className="view-container"><SkeletonList count={3} /></div>;
  const rows: EquityHousehold[] = household ? [data] : data.households;
  const s = data.summary;
  return (
    <div className="view-container">
      <ViewHeader title={household ? `Equity compensation · ${data.household}` : "Equity compensation"} actions={household ? <AllClients href="/studio/equity-comp" /> : undefined} />
      <div className="mb-4 grid grid-cols-2 gap-3 cq-md:grid-cols-4" data-testid="equity-summary">
        {(household ? [["Vested", money(data.value_vested), "text-ink"], ["Unvested", money(data.value_unvested), "text-ink-muted"], ["Vesting in 12 months", money(data.value_vesting_12m), "text-ink-muted"],
          ["Next vest", data.next_vest_on ? reviewDay(data.next_vest_on) : "—", "text-ink-muted"]]
          : [["With grants", s.with_grants, "text-ink"], ["Concentrated", s.concentrated, s.concentrated ? "text-warn" : "text-ink-muted"], ["No plan", s.no_plan, s.no_plan ? "text-crit" : "text-ink-muted"],
            ["Vesting in 12 months", money(s.value_vesting_12m), "text-ink-muted"]]).map(([label, v, tone]: any) => (
          <Card key={label}>
            <div className="tile-label">{label}</div>
            <div className={`font-mono text-lg tabular-nums ${tone}`}>{v}</div>
          </Card>
        ))}
      </div>
      <Card className="mb-4">
        <div className="flex flex-wrap gap-1.5">
          <span className="chip text-ink-muted">recorded from the plan statement</span>
          <span className="chip text-ink-muted">not in net worth</span>
          <span className="chip text-ink-muted">paths hold the price flat</span>
          <span className="chip text-ink-muted">tax, AMT and withholding not modelled</span>
        </div>
      </Card>
      {rows.length === 0 || rows.every((r) => !r.grants.length && !r.exposures.length) ? <Empty>None on file.</Empty> : (
        <div className="space-y-4" data-testid="equity-rows">
          {rows.map((r) => (
            <Card key={r.household_id} className="overflow-hidden p-0">
              {!household && (
                <div className="border-b border-border-soft px-4 py-2.5">
                  <ViewLink href={`/studio/equity-comp?household=${r.household_id}`} className="text-sm text-ink hover:underline">{r.household}</ViewLink>
                </div>
              )}
              {r.exposures.length > 0 && (
                <div className="overflow-x-auto">
                  <table className="w-full text-sm" data-testid="equity-exposures">
                    <thead>
                      <tr className="text-left text-[10px] uppercase tracking-wide text-ink-muted">
                        <th className="px-4 py-2 font-medium">Position</th>
                        <th className="px-4 py-2 text-right font-medium">Held</th>
                        <th className="px-4 py-2 text-right font-medium">Granted</th>
                        <th className="px-4 py-2 text-right font-medium">Of the book</th>
                        <th className="px-4 py-2 font-medium">Plan</th>
                        <th className="px-4 py-2 text-right font-medium">Act</th>
                      </tr>
                    </thead>
                    <tbody className="divide-y divide-border-soft">
                      <Exposures household={r} canRecord={data.can_record} say={nav.say} onSaved={refetch} asOf={data.as_of} />
                    </tbody>
                  </table>
                </div>
              )}
              {r.grants.length > 0 && <div className="overflow-x-auto border-t border-border-soft"><Grants grants={r.grants} /></div>}
            </Card>
          ))}
        </div>
      )}
    </div>
  );
}
