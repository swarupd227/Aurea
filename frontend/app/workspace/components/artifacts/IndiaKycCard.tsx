import { money } from "@/lib/format";

const SHOWN = 8;
const STATE_TONE: Record<string, string> = {
  verified: "text-ok", due: "text-warn", overdue: "text-crit", blocked: "text-crit", not_assessed: "text-warn", not_on_file: "text-ink-muted",
};
const STATE_TEXT: Record<string, string> = {
  verified: "Verified", due: "Due", overdue: "Overdue", blocked: "Blocked at the KRA", not_assessed: "Not assessed", not_on_file: "Not on file",
};
const LOSS_TONE: Record<string, string> = { carried: "text-ok", expired: "text-ink-muted", late_return: "text-warn", no_return: "text-warn", current_year: "text-ink-muted" };
const LOSS_TEXT: Record<string, string> = {
  carried: "carried", expired: "expired", late_return: "late return", no_return: "no return", current_year: "this year",
};
const FLAG_TONE: Record<string, string> = { warn: "text-warn", info: "text-ink-muted" };
const FLAG_TEXT: Record<string, string> = {
  exemption_burn: "Uses the exemption", rebuy_review: "Rebuy: review", gain_harvest_review: "Gain harvest: review", carry_forward_left: "Loss left over",
};
const ADVANCE_TONE: Record<string, string> = { passed: "text-ink-muted", due_soon: "text-warn", upcoming: "text-ink-muted" };

const pct = (v: number | null | undefined) => (v == null ? "-" : `${(v * 100).toFixed(v * 100 % 1 ? 1 : 0)}%`);
const rupees = (v: number | null | undefined) => (v == null ? "-" : money(v, "INR"));

/** One party's KYC: the state worked out from what was recorded, with the clock, the KRA and what is still needed. */
function PartyRow({ p }: { p: any }) {
  return (
    <div className="space-y-1.5 px-3 py-2" data-testid="kyc-party">
      <div className="flex flex-wrap items-center gap-1.5">
        <span className="text-sm text-ink mr-auto">{p.name}</span>
        {p.structure_label && <span className="chip text-ink-muted">{p.structure_label}</span>}
        <span className={`chip ${STATE_TONE[p.state]}`} title={p.message} data-testid="kyc-state">{STATE_TEXT[p.state]}</span>
        {p.due_on && <span className="chip text-ink-muted tabular-nums" title={p.band_from ? `Band from ${p.band_from}` : undefined}>Re-KYC {p.due_on}</span>}
        {p.band && <span className="chip text-ink-muted">{p.band} risk, {Math.round(p.review_months / 12)}y</span>}
      </div>
      {p.recorded && (
        <div className="flex flex-wrap gap-1.5">
          {p.kra_label && <span className={`chip ${p.kra_status === "validated" ? "text-ok" : "text-warn"}`} title={p.kra_name ?? undefined}>KRA {p.kra_label}</span>}
          {p.ckyc_number && <span className="chip text-ink-muted tabular-nums">CKYC {p.ckyc_number}</span>}
          {p.mode_label && <span className="chip text-ink-muted">{p.mode_label}</span>}
          {p.aadhaar && <span className="chip text-ink-muted tabular-nums">Aadhaar {p.aadhaar}</span>}
          {p.pan_aadhaar_linked != null && <span className={`chip ${p.pan_aadhaar_linked ? "text-ok" : "text-crit"}`}>PAN {p.pan_aadhaar_linked ? "linked" : "not linked"}</span>}
          {p.pep && <span className="chip text-warn">Politically exposed</span>}
          {p.id_proof?.valid_to && <span className="chip text-ink-muted tabular-nums">ID to {p.id_proof.valid_to}</span>}
          {(p.owners?.beneficial ?? []).map((n: string) => <span key={n} className="chip text-ink-muted" title={p.owners.rule}>Beneficial owner {n}</span>)}
          {(p.owners?.controlling ?? []).map((n: string) => <span key={`c-${n}`} className="chip text-ink-muted" title="CRS controlling person: a different test">Controlling {n}</span>)}
          {(p.triggers ?? []).map((t: any, i: number) => <span key={i} className="chip text-warn tabular-nums">{String(t.kind).replace(/_/g, " ")} {t.on}</span>)}
        </div>
      )}
      {(p.needs ?? []).length > 0 && (
        <div className="flex flex-wrap gap-1.5">{p.needs.map((n: string) => <span key={n} className="chip text-warn">Needs: {n}</span>)}</div>
      )}
    </div>
  );
}

/** Each client and entity's KYC, worst first. */
export function KycParties({ parties }: { parties: any[] }) {
  return (
    <div className="divide-y divide-border-soft" data-testid="kyc-list">
      {parties.slice(0, SHOWN).map((p) => <PartyRow key={p.person_id ?? p.entity_id ?? p.case_id ?? p.name} p={p} />)}
      {parties.length > SHOWN && <div className="px-3 py-2"><span className="chip text-ink-muted">+{parties.length - SHOWN} more</span></div>}
    </div>
  );
}

export function KycCounts({ counts }: { counts: Record<string, number> }) {
  return (
    <div className="flex flex-wrap gap-1.5">
      {["overdue", "blocked", "due", "not_assessed", "not_on_file", "verified"].filter((k) => counts[k]).map((k) => (
        <span key={k} className={`chip ${STATE_TONE[k]}`}>{counts[k]} {STATE_TEXT[k].toLowerCase()}</span>
      ))}
    </div>
  );
}

/** KYC as a person recorded it. Real KRA and CKYC lookups are not connected. */
export function KycCard({ result }: { result: any }) {
  if (result.applies === false) {
    return <div className="rounded-lg border border-border bg-surface px-3 py-2" data-testid="kyc-card"><span className="chip text-ink-muted">Not an Indian firm</span></div>;
  }
  const policy = result.policy?.review_months ?? {};
  return (
    <div className="overflow-hidden rounded-lg border border-border bg-surface" data-testid="kyc-card">
      <div className="flex flex-wrap items-center justify-between gap-2 border-b border-border-soft px-3 py-2">
        <div className="text-sm font-medium text-ink">KYC</div>
        <KycCounts counts={result.counts ?? {}} />
      </div>
      {(result.parties ?? []).length === 0 ? <div className="px-3 py-2"><span className="chip text-ink-muted">Nothing on file</span></div> : <KycParties parties={result.parties} />}
      <div className="flex flex-wrap gap-1.5 border-t border-border-soft px-3 py-2">
        {Object.entries(policy).map(([band, v]: any) => (
          <span key={band} className="chip text-ink-muted" title={`${result.policy.source} (${v.source})`}>{band} {Math.round(v.months / 12)}y</span>
        ))}
        <span className="chip text-ink-muted" title={result.not_connected}>Lookups not connected</span>
      </div>
    </div>
  );
}

/** What the tax engine reads beside the gains for one taxpayer: losses, grandfathering, regime, advance tax and flags. */
export function TaxEngine({ engine }: { engine: any }) {
  if (!engine) return null;
  const losses: any[] = engine.loss_ledger ?? [];
  const gf: any[] = engine.grandfathering ?? [];
  const regime = engine.regime;
  const advance = engine.advance_tax;
  return (
    <div className="space-y-2 border-t border-border-soft px-3 py-2" data-testid="tax-engine">
      {losses.length > 0 && (
        <div className="flex flex-wrap gap-1.5" data-testid="tax-ledger">
          {losses.map((l) => (
            <span key={`${l.tax_year}-${l.kind}`} className={`chip tabular-nums ${LOSS_TONE[l.state]}`} title={l.why}>
              {l.tax_year} {l.kind === "long_term" ? "long" : "short"} {rupees(l.amount)}{l.used ? `, ${rupees(l.used)} used` : ""} · {LOSS_TEXT[l.state]}
            </span>
          ))}
        </div>
      )}
      {gf.length > 0 && (
        <div className="flex flex-wrap gap-1.5">
          {gf.map((g) => (
            <span key={`${g.symbol}-${g.account}`} className={`chip ${g.applied ? "text-ok" : "text-warn"}`} title={g.needs ?? g.account}>
              {g.symbol} {g.applied ? `2018 value ${rupees(g.fmv_per_unit)}` : "needs its 2018 value"}
            </span>
          ))}
        </div>
      )}
      {regime?.assessed ? (
        <div className="grid grid-cols-2 gap-x-4 gap-y-1 sm:grid-cols-4" data-testid="tax-regime">
          <Figure label="New regime" value={rupees(regime.new.tax)} />
          <Figure label="Old regime" value={rupees(regime.old.tax)} />
          <Figure label={regime.conclusive ? "Cheaper" : "Cheaper without deductions"} value={regime.cheaper === "old" ? "Old regime" : "New regime"} />
          <Figure label={regime.conclusive ? "Saves" : "Old wins above"} value={regime.conclusive ? rupees(regime.saving) : (regime.break_even_deductions != null ? rupees(regime.break_even_deductions) : "-")} />
        </div>
      ) : (
        <div className="flex flex-wrap gap-1.5">{(regime?.needs ?? []).map((n: string) => <span key={n} className="chip text-warn">Regime comparison needs {n}</span>)}</div>
      )}
      {regime?.assessed && (regime.needs ?? []).length > 0 && (
        <div className="flex flex-wrap gap-1.5">{regime.needs.map((n: string) => <span key={n} className="chip text-warn">Needs: {n}</span>)}</div>
      )}
      {advance?.assessed ? (
        <div className="overflow-x-auto" data-testid="tax-advance">
          <table className="w-full text-sm">
            <thead><tr className="text-left text-[10px] uppercase tracking-wide text-ink-muted">
              <th className="px-1 py-1 font-medium">Advance tax</th><th className="px-1 py-1 text-right font-medium">Instalment</th>
              <th className="px-1 py-1 text-right font-medium">By then</th><th className="px-1 py-1 font-medium">State</th>
            </tr></thead>
            <tbody className="divide-y divide-border-soft">
              {advance.instalments.map((i: any) => (
                <tr key={i.key}>
                  <td className="px-1 py-1 tabular-nums text-ink-soft" title={i.citation}>{i.due_on}</td>
                  <td className="px-1 py-1 text-right tabular-nums text-ink">{advance.asked ? rupees(i.amount) : "-"}</td>
                  <td className="px-1 py-1 text-right tabular-nums text-ink-muted">{advance.asked ? rupees(i.cumulative) : "-"}</td>
                  <td className="px-1 py-1"><span className={`chip ${ADVANCE_TONE[i.state]}`}>{i.state === "passed" ? "passed, payment not recorded" : i.state.replace("_", " ")}</span></td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      ) : (
        <div className="flex flex-wrap gap-1.5">{(advance?.needs ?? []).map((n: string) => <span key={n} className="chip text-warn">Advance tax needs {n}</span>)}</div>
      )}
      {(engine.flags ?? []).length > 0 && (
        <div className="flex flex-wrap gap-1.5">
          {engine.flags.map((f: any, i: number) => <span key={i} className={`chip ${FLAG_TONE[f.tone]}`} title={f.text}>{f.symbol ? `${f.symbol}: ` : ""}{FLAG_TEXT[f.kind] ?? f.kind}</span>)}
        </div>
      )}
    </div>
  );
}

function Figure({ label, value }: { label: string; value: string }) {
  return (
    <div>
      <div className="text-[10px] uppercase tracking-wide text-ink-muted">{label}</div>
      <div className="font-mono text-sm tabular-nums text-ink">{value}</div>
    </div>
  );
}

/** What a person recorded for the tax engine, and the table of asset classes. */
export function TaxRecordsCard({ result }: { result: any }) {
  if (result.applies === false) {
    return <div className="rounded-lg border border-border bg-surface px-3 py-2" data-testid="tax-records-card"><span className="chip text-ink-muted">Not an Indian firm</span></div>;
  }
  const taxpayers: any[] = result.taxpayers ?? [];
  const values: any[] = result.grandfather_values ?? [];
  return (
    <div className="overflow-hidden rounded-lg border border-border bg-surface" data-testid="tax-records-card">
      <div className="flex flex-wrap items-center justify-between gap-2 border-b border-border-soft px-3 py-2">
        <div className="text-sm font-medium text-ink">Tax records, {result.tax_year}</div>
        <span className="chip text-ink-muted" title={result.estimate}>Estimate</span>
      </div>
      {taxpayers.length === 0 && values.length === 0 && <div className="px-3 py-2"><span className="chip text-ink-muted">Nothing recorded</span></div>}
      <div className="divide-y divide-border-soft">
        {taxpayers.map((t) => (
          <div key={t.id} className="space-y-1.5 px-3 py-2">
            <div className="flex flex-wrap items-center gap-1.5">
              <span className="text-sm text-ink mr-auto">{t.name}</span>
              <span className="chip text-ink-muted tabular-nums">Short {rupees(t.available.short_term)}</span>
              <span className="chip text-ink-muted tabular-nums">Long {rupees(t.available.long_term)}</span>
            </div>
            <div className="flex flex-wrap gap-1.5">
              {t.losses.map((l: any) => (
                <span key={`${l.tax_year}-${l.kind}`} className={`chip tabular-nums ${LOSS_TONE[l.state]}`} title={l.why}>
                  {l.tax_year} {l.kind === "long_term" ? "long" : "short"} {rupees(l.amount)} · {LOSS_TEXT[l.state]}
                </span>
              ))}
            </div>
          </div>
        ))}
      </div>
      {values.length > 0 && (
        <div className="flex flex-wrap gap-1.5 border-t border-border-soft px-3 py-2">
          {values.map((v) => <span key={v.symbol} className="chip text-ink-muted tabular-nums" title={v.name}>{v.symbol} 31 Jan 2018 {rupees(v.fmv_per_unit)}</span>)}
        </div>
      )}
      <div className="overflow-x-auto border-t border-border-soft">
        <table className="w-full text-sm">
          <thead><tr className="text-left text-[10px] uppercase tracking-wide text-ink-muted">
            <th className="px-3 py-1 font-medium">Asset</th><th className="px-3 py-1 text-right font-medium">Long after</th>
            <th className="px-3 py-1 text-right font-medium">Short</th><th className="px-3 py-1 text-right font-medium">Long</th>
          </tr></thead>
          <tbody className="divide-y divide-border-soft">
            {(result.asset_classes ?? []).map((c: any) => (
              <tr key={c.key} title={c.note || undefined}>
                <td className="px-3 py-1 text-ink-soft">{c.label}</td>
                <td className="px-3 py-1 text-right tabular-nums text-ink-muted">{c.long_after_months ? `${c.long_after_months} months` : "never"}</td>
                <td className="px-3 py-1 text-right tabular-nums text-ink">{c.short_text}</td>
                <td className="px-3 py-1 text-right tabular-nums text-ink">{c.long_text}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
      <div className="flex flex-wrap gap-1.5 border-t border-border-soft px-3 py-2">
        {(result.dates ?? []).map((d: any) => (
          <span key={d.key} className={`chip tabular-nums ${ADVANCE_TONE[d.state]}`} title={d.citation}>{d.key === "return" ? "Return" : `Advance ${pct(d.share)}`} {d.due_on}</span>
        ))}
      </div>
    </div>
  );
}
