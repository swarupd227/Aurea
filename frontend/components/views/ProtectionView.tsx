"use client";

import { useState } from "react";
import { Card, Empty, ErrorState, SkeletonList } from "@/components/ui";
import { api } from "@/lib/api";
import { useApi } from "@/lib/hooks";
import { withRef } from "@/lib/refs";
import { KIND_TEXT, Scenario, money } from "@/app/workspace/components/artifacts/ProtectionCard";
import { ViewLink, useViewNav } from "./ViewNav";
import { AllClients, ViewHeader } from "./ViewHeader";

const NEW_POLICY = { person_id: "", kind: "life", provider: "", amount: "", benefit_years: "", expires_on: "", source_document: "" };
const NEW_INCOME = { person_id: "", annual_income: "", source_document: "" };

/** What happens to a household if an earner dies or cannot work, or someone needs care: every client,
 * or one when opened on it. In the work pane, a change is the conversation's tool behind its Confirm
 * card; on a page the forms act directly. */
export function ProtectionView({ household = null }: { household?: string | null }) {
  const nav = useViewNav();
  const { data, loading, error, refetch } = useApi<any>("/api/protection");
  const [msg, setMsg] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [open, setOpen] = useState<"policy" | "income" | null>(null);
  const [confirming, setConfirming] = useState(false);
  const [policy, setPolicy] = useState(NEW_POLICY);
  const [income, setIncome] = useState(NEW_INCOME);
  const [ending, setEnding] = useState<{ id: string; reason: string } | null>(null);

  if (error) return <div className="view-container"><Card><ErrorState what="protection" message={error} onRetry={refetch} /></Card></div>;
  if (loading || !data) return <div className="view-container"><SkeletonList count={4} /></div>;
  const s = data.summary;
  const canRecord: boolean = data.can_record;
  const homes: any[] = household ? data.households.filter((h: any) => h.household_id === household) : data.households;
  const one = household ? homes[0] ?? null : null;
  const people = homes.flatMap((h: any) => h.people.map((p: any) => ({ ...p, household: h.household, household_id: h.household_id })));
  const policies = homes.flatMap((h: any) => h.policies.map((p: any) => ({ ...p, household: h.household })));
  const isCare = policy.kind === "long_term_care";
  const isYearly = isCare || policy.kind === "income_protection";
  const care = data.care_basis;

  function close() {
    setOpen(null);
    setConfirming(false);
  }

  async function run(path: string, body: any, done: (r: any) => string) {
    setBusy(true);
    setMsg(null);
    try {
      const r = await api<any>(path, { method: "POST", body });
      setMsg(done(r));
      close();
      setEnding(null);
      await refetch();
      return true;
    } catch (e: any) {
      setMsg(e?.message ?? "That did not work.");
      setConfirming(false);
      return false;
    } finally {
      setBusy(false);
    }
  }

  const nameOf = (id: string) => people.find((p: any) => p.person_id === id)?.name ?? "this person";

  function askPolicy() {
    const figure = `${isYearly ? "annual benefit" : "sum insured"} ${policy.amount}`;
    nav.say?.(withRef(
      `Record a ${policy.kind} policy for ${nameOf(policy.person_id)} with ${policy.provider.trim()}: ${figure}`
      + `${isCare && policy.benefit_years ? `, paid for ${policy.benefit_years} years` : ""}`
      + `${policy.expires_on ? `, ending on ${policy.expires_on}` : ""}, from the document "${policy.source_document.trim()}".`,
      "person", policy.person_id));
    setPolicy(NEW_POLICY);
    close();
  }

  function askIncome() {
    nav.say?.(withRef(
      `Record an annual income of ${income.annual_income} for ${nameOf(income.person_id)}, from the document "${income.source_document.trim()}".`,
      "person", income.person_id));
    setIncome(NEW_INCOME);
    close();
  }

  async function savePolicy() {
    const amount = policy.amount === "" ? null : Number(policy.amount);
    const ok = await run("/api/protection/policies", {
      person_id: policy.person_id, kind: policy.kind, provider: policy.provider, source_document: policy.source_document,
      sum_insured: isYearly ? null : amount, annual_benefit: isYearly ? amount : null,
      benefit_years: isCare && policy.benefit_years ? Number(policy.benefit_years) : null,
      expires_on: policy.expires_on || null,
    }, (r) => `Recorded ${KIND_TEXT[r.kind] ?? r.kind} cover with ${r.provider} for ${r.person}.`);
    if (ok) setPolicy(NEW_POLICY);
  }

  async function saveIncome() {
    const ok = await run("/api/protection/income", {
      person_id: income.person_id, annual_income: Number(income.annual_income), source_document: income.source_document,
    }, (r) => `Recorded an income of ${money(r.annual_income)} for ${r.person}.`);
    if (ok) setIncome(NEW_INCOME);
  }

  const policyReady = policy.person_id && policy.provider.trim() && policy.amount && policy.source_document.trim().length >= 3;
  const incomeReady = income.person_id && income.annual_income !== "" && income.source_document.trim().length >= 3;
  const personPicker = (value: string, onChange: (v: string) => void) => (
    <select className="input w-full" value={value} aria-label="Person" onChange={(e) => onChange(e.target.value)}>
      <option value="">Choose a person</option>
      {people.map((p: any) => <option key={p.person_id} value={p.person_id}>{p.household} · {p.name}</option>)}
    </select>
  );

  return (
    <div className="view-container">
      <ViewHeader title={one ? `Protection · ${one.household}` : "Protection"} actions={one ? <AllClients href="/studio/protection" /> : undefined} />
      {msg && <div className="mb-4 font-mono text-xs text-ink-muted" role="status" data-testid="protection-status">{msg}</div>}

      {!household && (
        <div className="mb-4 grid grid-cols-2 gap-3 cq-md:grid-cols-4" data-testid="protection-summary">
          {[
            ["With a gap", s.with_gap, s.with_gap ? "text-crit" : "text-ink-muted"],
            ["Clear", s.clear, "text-ok"],
            ["Not assessed", s.not_assessed, s.not_assessed ? "text-warn" : "text-ink-muted"],
            ["No one to assess", s.no_people, "text-ink-muted"],
          ].map(([label, v, tone]: any) => (
            <Card key={label}>
              <div className="tile-label">{label}</div>
              <div className={`font-mono text-2xl tabular-nums ${tone}`}>{v}</div>
            </Card>
          ))}
        </div>
      )}

      <Card className="mb-4">
        <div className="flex flex-wrap items-center justify-between gap-3">
          <div className="flex flex-wrap gap-1.5" data-testid="protection-basis">
            <span className="chip text-ink-muted">{Math.round(data.assumptions.death_replacement * 100)}% household income</span>
            <span className="chip text-ink-muted">{Math.round(data.assumptions.disability_replacement * 100)}% own income</span>
            <span className="chip text-ink-muted">to retirement goal, else {data.assumptions.retirement_age}</span>
            <span className="chip text-ink-muted">{(data.assumptions.discount_rate * 100).toFixed(0)}% discount</span>
            <span className="chip text-ink-muted">debts on file add to the death need</span>
            <span className="chip text-ink-muted">home and business not counted</span>
            {care
              ? <span className="chip text-ink-muted" title={`${care.source} · set by ${care.set_by}`} data-testid="protection-care-basis">care {money(care.annual_cost)} a year · {care.years} years · from age {data.assumptions.care_from_age}</span>
              : <span className="chip text-warn" data-testid="protection-care-basis">cost of care not set</span>}
            <span className="chip text-ink-muted">not modelled: {data.not_modelled.join(" · ").replace(/_/g, " ")}</span>
          </div>
          <div className="flex gap-2">
            <button className="btn-outline text-xs" data-testid="protection-record-policy" disabled={!canRecord}
              title={!canRecord ? "Only advisers, paraplanners or operations may record cover" : undefined}
              onClick={() => { setOpen(open === "policy" ? null : "policy"); setConfirming(false); }}>Record a policy</button>
            <button className="btn-outline text-xs" data-testid="protection-record-income" disabled={!canRecord}
              title={!canRecord ? "Only advisers, paraplanners or operations may record income" : undefined}
              onClick={() => { setOpen(open === "income" ? null : "income"); setConfirming(false); }}>Record income</button>
          </div>
        </div>

        {open === "policy" && (
          <div className="mt-4 grid gap-3 cq-md:grid-cols-3" data-testid="protection-policy-form">
            <label className="block"><div className="tile-label">Insured</div>{personPicker(policy.person_id, (v) => setPolicy({ ...policy, person_id: v }))}</label>
            <label className="block">
              <div className="tile-label">Cover</div>
              <select className="input w-full" value={policy.kind} aria-label="Cover" onChange={(e) => setPolicy({ ...policy, kind: e.target.value })}>
                <option value="life">Life</option>
                <option value="tpd">Total and permanent disablement</option>
                <option value="income_protection">Income protection</option>
                <option value="long_term_care">Long-term care</option>
              </select>
            </label>
            <label className="block">
              <div className="tile-label">{isYearly ? "Annual benefit" : "Sum insured"}</div>
              <input className="input w-full font-mono" inputMode="decimal" value={policy.amount} aria-label="Amount"
                onChange={(e) => setPolicy({ ...policy, amount: e.target.value })} />
            </label>
            {isCare && (
              <label className="block">
                <div className="tile-label">Years paid (blank if as long as care lasts)</div>
                <input className="input w-full font-mono" inputMode="numeric" value={policy.benefit_years} aria-label="Years paid" data-testid="protection-benefit-years"
                  onChange={(e) => setPolicy({ ...policy, benefit_years: e.target.value })} />
              </label>
            )}
            <label className="block">
              <div className="tile-label">Provider</div>
              <input className="input w-full" value={policy.provider} aria-label="Provider" onChange={(e) => setPolicy({ ...policy, provider: e.target.value })} />
            </label>
            <label className="block">
              <div className="tile-label">Ends on (if it has a term)</div>
              <input type="date" className="input w-full" value={policy.expires_on} aria-label="Ends on" onChange={(e) => setPolicy({ ...policy, expires_on: e.target.value })} />
            </label>
            <label className="block">
              <div className="tile-label">Source document</div>
              <input className="input w-full" value={policy.source_document} aria-label="Source document" placeholder="Policy schedule, 1 Mar 2026, provider"
                onChange={(e) => setPolicy({ ...policy, source_document: e.target.value })} />
            </label>
            <div className="flex flex-wrap items-center gap-2 cq-md:col-span-3">
              {!confirming ? (
                <>
                  <button className="btn-primary text-xs" disabled={!policyReady} onClick={() => (nav.say ? askPolicy() : setConfirming(true))}>{nav.say ? "Ask Astra" : "Record"}</button>
                  <button className="btn-outline text-xs" onClick={close}>Cancel</button>
                </>
              ) : (
                <div className="flex items-center gap-2" data-testid="protection-confirm">
                  <span className="chip text-ink-muted">counts as cover · changes the gap</span>
                  <button className="btn-primary text-xs" disabled={busy} onClick={savePolicy}>{busy ? "Recording…" : "Confirm"}</button>
                  <button className="btn-outline text-xs" disabled={busy} onClick={() => setConfirming(false)}>Not now</button>
                </div>
              )}
            </div>
          </div>
        )}

        {open === "income" && (
          <div className="mt-4 grid gap-3 cq-md:grid-cols-3" data-testid="protection-income-form">
            <label className="block"><div className="tile-label">Person</div>{personPicker(income.person_id, (v) => setIncome({ ...income, person_id: v }))}</label>
            <label className="block">
              <div className="tile-label">Annual income</div>
              <input className="input w-full font-mono" inputMode="decimal" value={income.annual_income} aria-label="Annual income"
                onChange={(e) => setIncome({ ...income, annual_income: e.target.value })} />
            </label>
            <label className="block">
              <div className="tile-label">Source document</div>
              <input className="input w-full" value={income.source_document} aria-label="Source document" placeholder="Payslip, Oct 2026"
                onChange={(e) => setIncome({ ...income, source_document: e.target.value })} />
            </label>
            <div className="flex flex-wrap items-center gap-2 cq-md:col-span-3">
              {!confirming ? (
                <>
                  <button className="btn-primary text-xs" disabled={!incomeReady} onClick={() => (nav.say ? askIncome() : setConfirming(true))}>{nav.say ? "Ask Astra" : "Record"}</button>
                  <button className="btn-outline text-xs" onClick={close}>Cancel</button>
                </>
              ) : (
                <div className="flex items-center gap-2" data-testid="protection-confirm">
                  <span className="chip text-ink-muted">changes the gap and the tax planning</span>
                  <button className="btn-primary text-xs" disabled={busy} onClick={saveIncome}>{busy ? "Recording…" : "Confirm"}</button>
                  <button className="btn-outline text-xs" disabled={busy} onClick={() => setConfirming(false)}>Not now</button>
                </div>
              )}
            </div>
          </div>
        )}
      </Card>

      <h2 className="mb-2 text-sm font-medium text-ink">People</h2>
      {people.length === 0 ? <Empty>No one to assess.</Empty> : (
        <Card className="mb-4 overflow-hidden p-0">
          <div className="overflow-x-auto">
            <table className="w-full text-sm" data-testid="protection-people">
              <thead>
                <tr className="text-left text-[10px] uppercase tracking-wide text-ink-muted">
                  <th className="px-4 py-2 font-medium">Person</th>
                  <th className="px-4 py-2 text-right font-medium">Age · to retirement</th>
                  <th className="px-4 py-2 text-right font-medium">Income</th>
                  <th className="px-4 py-2 text-right font-medium">If they die · {data.currency}</th>
                  <th className="px-4 py-2 text-right font-medium">If they cannot work</th>
                  <th className="px-4 py-2 text-right font-medium">If they need care</th>
                </tr>
              </thead>
              <tbody className="divide-y divide-border-soft">
                {people.map((p: any) => (
                  <tr key={p.person_id}>
                    <td className="px-4 py-2">
                      <div className="text-ink">{p.name}</div>
                      {p.household_id
                        ? <ViewLink href={`/studio/clients/${p.household_id}`} className="text-xs text-ink-muted hover:underline" data-testid="protection-client">{p.household}</ViewLink>
                        : <div className="text-xs text-ink-muted">{p.household}</div>}
                    </td>
                    <td className="px-4 py-2 text-right font-mono tabular-nums text-ink-muted" title={p.retirement_basis === "assumed" ? "Retirement age assumed" : "From the retirement goal"}>{p.age ?? "—"} · {p.years_to_retirement != null ? `${p.years_to_retirement}y` : "—"}</td>
                    <td className="px-4 py-2 text-right font-mono tabular-nums text-ink-muted">
                      {money(p.income)}
                      {p.other_income_missing && <span className="chip ml-1 text-warn" title="Another adult's income is not on file, so the need is overstated">partner unknown</span>}
                    </td>
                    <td className="px-4 py-2 text-right" title={p.death.need != null ? `need ${money(p.death.need)}${p.death.debts ? ` including debts ${money(p.death.debts)}` : ""} · cover ${money(p.death.cover)} · assets ${money(p.death.assets)}` : undefined}><Scenario s={p.death} /></td>
                    <td className="px-4 py-2 text-right" title={p.disability.need != null ? `need ${money(p.disability.need)} · cover ${money(p.disability.cover)} · assets ${money(p.disability.assets)}` : undefined}><Scenario s={p.disability} /></td>
                    <td className="px-4 py-2 text-right" title={p.care.need != null ? `need ${money(p.care.need)} · cover ${money(p.care.cover)} · assets ${money(p.care.assets)}` : undefined}><Scenario s={p.care} /></td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </Card>
      )}

      <h2 className="mb-2 text-sm font-medium text-ink">Policies on file</h2>
      {policies.length === 0 ? <Empty>No policies on file.</Empty> : (
        <Card className="overflow-hidden p-0">
          <div className="overflow-x-auto">
            <table className="w-full text-sm" data-testid="protection-policies">
              <thead>
                <tr className="text-left text-[10px] uppercase tracking-wide text-ink-muted">
                  <th className="px-4 py-2 font-medium">Insured</th>
                  <th className="px-4 py-2 font-medium">Cover</th>
                  <th className="px-4 py-2 text-right font-medium">Amount</th>
                  <th className="px-4 py-2 font-medium">Status</th>
                  <th className="px-4 py-2" />
                </tr>
              </thead>
              <tbody className="divide-y divide-border-soft">
                {policies.map((p: any) => (
                  <tr key={p.id} title={p.source_document}>
                    <td className="px-4 py-2">
                      <div className="text-ink">{p.person}</div>
                      <div className="text-xs text-ink-muted">{p.household}</div>
                    </td>
                    <td className="px-4 py-2 text-ink-muted">{KIND_TEXT[p.kind] ?? p.kind} · {p.provider}</td>
                    <td className="px-4 py-2 text-right font-mono tabular-nums text-ink-muted">{p.currency} {money(p.sum_insured ?? p.annual_benefit)}{p.annual_benefit != null ? " /yr" : ""}{p.benefit_years != null ? ` · ${p.benefit_years}y` : ""}</td>
                    <td className="px-4 py-2"><span className={`chip ${p.counted ? "text-ok" : "text-ink-muted"}`}>{p.status.replace("_", " ")}</span>{p.expires_on && <span className="chip ml-1 text-ink-muted">to {p.expires_on}</span>}</td>
                    <td className="px-4 py-2 text-right">
                      {canRecord && p.status !== "ended" && (ending?.id === p.id ? (
                        <div className="flex flex-wrap items-center justify-end gap-2" data-testid="protection-end">
                          <input className="input w-48 py-1 text-xs" aria-label="Reason" placeholder="Lapsed, cancelled, claimed…"
                            value={ending.reason} onChange={(e) => setEnding({ ...ending, reason: e.target.value })} />
                          <button className="btn-primary text-xs" disabled={busy || ending.reason.trim().length < 3}
                            onClick={() => {
                              if (nav.say) {
                                nav.say(withRef(`End the ${KIND_TEXT[p.kind] ?? p.kind} policy with ${p.provider} for ${p.person}. Reason: ${ending.reason.trim()}`, "policy", p.id));
                                setEnding(null);
                                return;
                              }
                              run(`/api/protection/policies/${p.id}/end`, { reason: ending.reason }, (r) => `Ended the ${KIND_TEXT[r.kind] ?? r.kind} policy with ${r.provider}.`);
                            }}>{nav.say ? "Ask Astra" : "Confirm"}</button>
                          <button className="btn-outline text-xs" disabled={busy} onClick={() => setEnding(null)}>Not now</button>
                        </div>
                      ) : (
                        <button className="btn-outline text-xs" data-testid="protection-end-open" onClick={() => setEnding({ id: p.id, reason: "" })}>End</button>
                      ))}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </Card>
      )}
    </div>
  );
}
