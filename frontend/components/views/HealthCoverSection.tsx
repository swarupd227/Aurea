"use client";

import { useState } from "react";
import { Card, Empty, ErrorState, SkeletonList } from "@/components/ui";
import { api } from "@/lib/api";
import { useApi } from "@/lib/hooks";
import { withRef } from "@/lib/refs";
import { money } from "@/app/workspace/components/artifacts/ProtectionCard";
import { FigureChip, MemberTable, PolicyLayout, RenewalChip, type HealthHousehold, type PolicyValue } from "@/app/workspace/components/artifacts/HealthCoverCard";
import { useViewNav } from "./ViewNav";

type Open = "policy" | "values" | "figure" | null;
type Fields = Record<string, string>;

const KINDS: [string, string][] = [["family_floater", "Family floater"], ["individual", "Individual"], ["super_top_up", "Super top-up"], ["employer_group", "Employer group"]];
const POLICY_FIELDS: [string, string][] = [
  ["provider", "Insurer"], ["sum_insured", "Sum insured"], ["deductible", "Deductible (super top-up)"],
  ["members", "Insured (Name: relationship; …)"], ["room_rent_cap", "Room-rent cap, as stated"], ["co_payment_percent", "Co-payment %"],
  ["co_payment_terms", "Co-payment terms"], ["sub_limits", "Sub-limits (what: limit; …)"], ["waiting_periods", "Waiting periods (what: 36 months; …)"],
  ["covered_since", "Covered since (YYYY-MM-DD)"], ["renewal_on", "Renewal date (YYYY-MM-DD)"], ["annual_premium", "Premium a year"],
  ["source_document", "Source document"],
];
const VALUE_FIELDS: [string, string][] = [
  ["statement_date", "Statement date (YYYY-MM-DD)"], ["premiums_paid", "Premiums paid"], ["surrender_value", "Surrender value"],
  ["paid_up_value", "Paid-up value"], ["fund_value", "Fund value (unit-linked)"], ["annual_premium", "Premium a year"],
  ["premium_years_left", "Premium years left"], ["maturity_on", "Maturity date (YYYY-MM-DD)"], ["maturity_value", "Maturity benefit"],
  ["commenced_on", "Started on (YYYY-MM-DD)"], ["charges_stated", "Charges, as stated"], ["source_document", "Source document"],
];
const NUMBERS = ["sum_insured", "deductible", "co_payment_percent", "annual_premium", "premiums_paid", "surrender_value", "paid_up_value",
  "fund_value", "premium_years_left", "maturity_value", "sum_assured", "family_cover", "parent_cover"];

const filled = (f: Fields) => Object.fromEntries(Object.entries(f).filter(([, v]) => v.trim() !== "").map(([k, v]) => [k, v.trim()]));
const body = (f: Fields) => Object.fromEntries(Object.entries(filled(f)).map(([k, v]) => [k, NUMBERS.includes(k) ? Number(v) : v]));
const spelled = (f: Fields, labels: [string, string][]) => {
  const given = filled(f);
  return labels.filter(([k]) => given[k]).map(([k, label]) => `${label.replace(/ \(.*\)$/, "").toLowerCase()}: ${given[k]}`).join("; ");
};

/** B-397: health cover and the surrender, paid-up and continue layout, on the Protection page. Shown for an Indian firm.
 * In the work pane a change is the conversation's tool behind its Confirm card; on a page the forms act directly. */
export function HealthCoverSection({ household = null, protectionHomes = [] }: { household?: string | null; protectionHomes?: any[] }) {
  const nav = useViewNav();
  const { data, loading, error, refetch } = useApi<any>("/api/health-cover");
  const [open, setOpen] = useState<Open>(null);
  const [form, setForm] = useState<Fields>({});
  const [confirming, setConfirming] = useState(false);
  const [busy, setBusy] = useState(false);
  const [msg, setMsg] = useState<string | null>(null);
  const [ending, setEnding] = useState<{ id: string; reason: string } | null>(null);

  if (error) return <Card className="mb-4"><ErrorState what="health cover" message={error} onRetry={refetch} /></Card>;
  if (loading || !data) return <div className="mb-4"><SkeletonList count={2} /></div>;
  if (!data.health.applies && !data.values.applies) return null;

  const homes: HealthHousehold[] = household ? data.health.households.filter((h: HealthHousehold) => h.household_id === household) : data.health.households;
  const one = household ? homes[0] ?? null : null;
  const values: PolicyValue[] = (data.values.policies as PolicyValue[]).filter((p) => !household || p.household_id === household);
  const members = homes.flatMap((h) => h.members.map((m) => ({ household: one ? undefined : h.household, m })));
  const policies = homes.flatMap((h) => h.policies.map((p) => ({ ...p, household: h.household, household_id: h.household_id })));
  const cover = household ? protectionHomes.filter((h) => h.household_id === household) : protectionHomes;
  const people: any[] = cover.flatMap((h) => h.people.map((p: any) => ({ ...p, household: h.household })));
  const lifePolicies: any[] = cover.flatMap((h) => h.policies.filter((p: any) => p.kind === "life" && !p.rider_of && !p.group_cover && p.status === "in_force"));

  const set = (k: string, v: string) => { setForm({ ...form, [k]: v }); setConfirming(false); };
  const toggle = (what: Open) => {
    setOpen(open === what ? null : what);
    setConfirming(false);
    setForm(what === "policy" ? { kind: "family_floater", household_id: household ?? "" } : what === "values" ? { product: "endowment" } : {});
  };
  const close = () => { setOpen(null); setConfirming(false); setForm({}); };

  const chosen = homes.find((h) => h.household_id === form.household_id) ?? data.health.households.find((h: HealthHousehold) => h.household_id === form.household_id);
  const ready = open === "policy"
    ? !!(form.household_id && form.provider?.trim() && form.sum_insured && form.members?.trim() && (form.source_document ?? "").trim().length >= 5)
    : open === "values"
      ? !!((form.policy_id || (form.person_id && form.provider?.trim() && form.sum_assured)) && form.statement_date && form.premiums_paid && (form.source_document ?? "").trim().length >= 5)
      : !!(form.family_cover && (form.source_document ?? "").trim().length >= 5);

  async function run(path: string, payload: any, done: string) {
    setBusy(true);
    setMsg(null);
    try {
      await api<any>(path, { method: "POST", body: payload });
      setMsg(done);
      close();
      setEnding(null);
      await refetch();
    } catch (e: any) {
      setMsg(e?.message ?? "That did not work.");
      setConfirming(false);
    } finally {
      setBusy(false);
    }
  }

  function ask() {
    if (!nav.say) return;
    if (open === "policy") {
      // The schedule it renews travels as a second marker, so its id is exact and never shown.
      const old = chosen?.policies.find((p: any) => p.id === form.replaces_id);
      const sentence = withRef(`Record a ${form.kind.replace(/_/g, " ")} health policy for ${chosen?.household}${old ? `, replacing the schedule with ${old.provider}` : ""}. ${spelled(form, POLICY_FIELDS)}.`, "household", form.household_id);
      nav.say(old ? withRef(sentence, "policy", old.id) : sentence);
    } else if (open === "values") {
      const of = lifePolicies.find((p) => p.id === form.policy_id);
      const subject = of ? `the ${of.provider} life policy of ${of.person}`
        : `a new life policy of ${people.find((p) => p.person_id === form.person_id)?.name} with ${form.provider}, sum assured ${form.sum_assured}`;
      const basis = form.maturity_basis ? `; maturity benefit is ${form.maturity_basis}` : "";
      nav.say(withRef(`Record ${form.product.replace(/_/g, " ")} values from the insurer's statement for ${subject}. ${spelled(form, VALUE_FIELDS)}${basis}.`,
        of ? "policy" : "person", of ? form.policy_id : form.person_id));
    } else {
      nav.say(`Set the firm's health cover figure: ${form.family_cover} for each person in a family${form.parent_cover ? `, ${form.parent_cover} for a parent` : ""}, from "${form.source_document.trim()}".`);
    }
    close();
  }

  function save() {
    if (open === "policy") return run("/api/health-cover/policies", body(form), "Health policy recorded.");
    if (open === "values") return run("/api/health-cover/statements", body(form), "Statement recorded.");
    return run("/api/health-cover/figure", body(form), "Health cover figure set.");
  }

  const input = (k: string, label: string) => (
    <label key={k} className="block"><div className="tile-label">{label}</div>
      <input className="input w-full" aria-label={label} value={form[k] ?? ""} onChange={(e) => set(k, e.target.value)} />
    </label>
  );
  const pick = (k: string, label: string, options: [string, string][], blank?: string) => (
    <label className="block"><div className="tile-label">{label}</div>
      <select className="input w-full" aria-label={label} value={form[k] ?? ""} onChange={(e) => set(k, e.target.value)}>
        {blank !== undefined && <option value="">{blank}</option>}
        {options.map(([v, text]) => <option key={v} value={v}>{text}</option>)}
      </select>
    </label>
  );

  return (
    <section data-testid="health-cover-section">
      <div className="mb-2 flex flex-wrap items-center justify-between gap-3">
        <div className="flex flex-wrap items-center gap-1.5">
          <h2 className="text-sm font-medium text-ink">Health cover</h2>
          <FigureChip figure={data.health.figure} />
          <span className="chip text-ink-muted">employer cover shown apart</span>
          <span className="chip text-ink-muted">none on file is not no cover</span>
        </div>
        <div className="flex flex-wrap gap-2">
          <button className="btn-outline text-xs" data-testid="health-record-policy" disabled={!data.can_record}
            title={!data.can_record ? "Only advisers, paraplanners or operations may record a policy" : undefined} onClick={() => toggle("policy")}>Record health policy</button>
          <button className="btn-outline text-xs" data-testid="health-record-values" disabled={!data.can_record}
            title={!data.can_record ? "Only advisers, paraplanners or operations may record a statement" : undefined} onClick={() => toggle("values")}>Record policy values</button>
          {data.can_set_figure && <button className="btn-outline text-xs" data-testid="health-set-figure" onClick={() => toggle("figure")}>Set cover figure</button>}
        </div>
      </div>
      {msg && <div className="mb-2 font-mono text-xs text-ink-muted" role="status" data-testid="health-status">{msg}</div>}

      {open && (
        <Card className="mb-3">
          <div className="grid gap-3 cq-md:grid-cols-3" data-testid="health-form">
            {open === "policy" && (
              <>
                {pick("household_id", "Household", data.health.households.map((h: HealthHousehold) => [h.household_id, h.household]), "Choose a household")}
                {pick("kind", "Kind", KINDS)}
                {chosen && pick("replaces_id", "Renews or replaces", chosen.policies.filter((p: any) => p.status !== "ended").map((p: any) => [p.id, `${p.provider} · ${p.kind_label}`]), "Nothing: a new policy")}
                {POLICY_FIELDS.map(([k, label]) => input(k, label))}
              </>
            )}
            {open === "values" && (
              <>
                {pick("product", "Product", [["endowment", "Endowment"], ["unit_linked", "Unit-linked"], ["money_back", "Money-back"], ["whole_life", "Whole life"]])}
                {pick("policy_id", "Life policy on file", lifePolicies.map((p) => [p.id, `${p.person} · ${p.provider}`]), "Not on file: record it")}
                {!form.policy_id && (
                  <>
                    {pick("person_id", "Insured", people.map((p) => [p.person_id, `${p.household} · ${p.name}`]), "Choose a person")}
                    {input("provider", "Insurer")}
                    {input("sum_assured", "Sum assured")}
                  </>
                )}
                {VALUE_FIELDS.map(([k, label]) => input(k, label))}
                {pick("maturity_basis", "Maturity benefit is", [["guaranteed", "Guaranteed"], ["illustrated", "Illustrated"]], "Not stated")}
              </>
            )}
            {open === "figure" && (
              <>
                {input("family_cover", "Cover for each person in a family")}
                {input("parent_cover", "Cover for a parent (optional)")}
                {input("source_document", "Source of the figure")}
              </>
            )}
            <div className="flex flex-wrap items-center gap-2 cq-md:col-span-3">
              {!confirming ? (
                <>
                  <button className="btn-primary text-xs" disabled={!ready} onClick={() => (nav.say ? ask() : setConfirming(true))}>{nav.say ? "Ask Astra" : "Record"}</button>
                  <button className="btn-outline text-xs" onClick={close}>Cancel</button>
                </>
              ) : (
                <div className="flex items-center gap-2" data-testid="health-confirm">
                  <span className="chip text-ink-muted">{open === "figure" ? "changes every household's figure" : "recorded from the document"}</span>
                  <button className="btn-primary text-xs" disabled={busy} onClick={save}>{busy ? "Recording…" : "Confirm"}</button>
                  <button className="btn-outline text-xs" disabled={busy} onClick={() => setConfirming(false)}>Not now</button>
                </div>
              )}
            </div>
          </div>
        </Card>
      )}

      {members.length === 0 ? <Empty>No one on file.</Empty> : (
        <Card className="mb-3 overflow-hidden p-0"><MemberTable rows={members} showLimits={!!one} /></Card>
      )}

      {policies.length > 0 && (
        <Card className="mb-3 overflow-hidden p-0">
          <div className="overflow-x-auto">
            <table className="w-full text-sm" data-testid="health-policies">
              <thead>
                <tr className="text-left text-[10px] uppercase tracking-wide text-ink-muted">
                  <th className="px-4 py-2 font-medium">Policy</th>
                  <th className="px-4 py-2 text-right font-medium">Sum insured</th>
                  <th className="px-4 py-2 font-medium">Terms as stated</th>
                  <th className="px-4 py-2" />
                </tr>
              </thead>
              <tbody className="divide-y divide-border-soft">
                {policies.map((p) => (
                  <tr key={p.id} className="align-top" title={p.source_document}>
                    <td className="px-4 py-2">
                      <div className="text-ink">{p.provider} · {p.kind_label}</div>
                      <div className="text-xs text-ink-muted">{[one ? null : p.household, p.members.map((m) => m.name).join(", ")].filter(Boolean).join(" · ")}</div>
                    </td>
                    <td className="px-4 py-2 text-right font-mono tabular-nums text-ink-muted">{money(p.sum_insured)}{p.members.length > 1 ? " shared" : ""}</td>
                    <td className="px-4 py-2">
                      <div className="flex flex-wrap gap-1">
                        <RenewalChip p={p} />
                        {p.not_counted && p.status !== "ended" && <span className="chip text-warn" title={p.not_counted}>not counted</span>}
                        {p.status !== "ended" && p.limits.map((l) => <span key={l} className="chip text-ink-muted">{l}</span>)}
                      </div>
                    </td>
                    <td className="px-4 py-2 text-right">
                      {data.can_record && p.status !== "ended" && (ending?.id === p.id ? (
                        <div className="flex flex-wrap items-center justify-end gap-2" data-testid="health-end">
                          <input className="input w-48 py-1 text-xs" aria-label="Reason" placeholder="Lapsed, ported, job ended…" value={ending.reason}
                            onChange={(e) => setEnding({ id: p.id, reason: e.target.value })} />
                          <button className="btn-primary text-xs" disabled={busy || ending.reason.trim().length < 3}
                            onClick={() => {
                              if (nav.say) {
                                nav.say(withRef(`End the ${p.kind_label.toLowerCase()} health policy with ${p.provider}. Reason: ${ending.reason.trim()}`, "policy", p.id));
                                setEnding(null);
                                return;
                              }
                              run(`/api/health-cover/policies/${p.id}/end`, { reason: ending.reason }, "Health policy ended.");
                            }}>{nav.say ? "Ask Astra" : "Confirm"}</button>
                          <button className="btn-outline text-xs" disabled={busy} onClick={() => setEnding(null)}>Not now</button>
                        </div>
                      ) : (
                        <button className="btn-outline text-xs" data-testid="health-end-open" onClick={() => setEnding({ id: p.id, reason: "" })}>End</button>
                      ))}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </Card>
      )}

      <h2 className="mb-2 text-sm font-medium text-ink">Surrender · paid-up · continue</h2>
      {values.length === 0 ? <Empty>No insurer statement on file.</Empty> : (
        <Card className="mb-4 overflow-hidden p-0" data-testid="policy-values">
          <div className="divide-y divide-border-soft">{values.map((p) => <PolicyLayout key={p.policy_id} p={p} household={!one} />)}</div>
          <div className="flex flex-wrap gap-1.5 border-t border-border-soft px-3 py-2">
            <span className="chip text-ink-muted">figures only · no recommendation</span>
            <span className="chip text-ink-muted">not modelled: {data.values.not_modelled.join(" · ").replace(/_/g, " ")}</span>
          </div>
        </Card>
      )}
    </section>
  );
}
