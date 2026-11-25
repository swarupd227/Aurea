import { money } from "@/lib/format";

const SHOWN = 6;
const TAX_TONE: Record<string, string> = {
  resident_ordinary: "text-ink", resident_rnor: "text-ink", resident: "text-warn", non_resident: "text-ink", not_assessed: "text-warn",
};
const FLAG_TONE: Record<string, string> = { ok: "text-ok", warn: "text-warn", info: "text-ink-muted" };
const CHECK_TONE: Record<string, string> = { pass: "text-ok", fail: "text-crit", not_assessed: "text-ink-muted" };
const CHECK_TEXT: Record<string, string> = { pass: "pass", fail: "held", not_assessed: "not assessed" };
const MINOR_TONE: Record<string, string> = {
  guardian_on_file: "text-ok", no_guardian: "text-crit", attained_majority: "text-crit", converted: "text-ok", adult: "text-ink-muted", not_assessed: "text-ink-muted",
};
const MINOR_TEXT: Record<string, string> = {
  guardian_on_file: "Guardian on file", no_guardian: "No guardian on file", attained_majority: "Now an adult: convert", converted: "Converted", adult: "Adult", not_assessed: "Not assessed",
};

const pct = (v: number | null | undefined) => (v == null ? "-" : `${(v * 100).toFixed(2)}%`);

/** One person's residence: the tax test, FEMA status, the accounts held and the FATCA and CRS flags. */
function PersonRow({ p }: { p: any }) {
  if (!p.recorded) {
    return (
      <div className="flex flex-wrap items-center gap-1.5 px-3 py-1.5" data-testid="residency-person">
        <span className="text-sm text-ink mr-auto">{p.name}</span>
        <span className="chip text-warn" title={p.message}>Not assessed</span>
      </div>
    );
  }
  const tax = p.tax;
  const accounts: any[] = (p.fema?.accounts ?? []).filter((a: any) => a.recorded);
  return (
    <div className="space-y-1.5 px-3 py-2" data-testid="residency-person">
      <div className="flex flex-wrap items-center gap-1.5">
        <span className="text-sm text-ink mr-auto">{p.name}</span>
        <span className={`chip ${TAX_TONE[tax.status]}`} title={tax.steps.join(". ")} data-testid="residency-tax">{tax.label}</span>
        <span className="chip text-ink-muted tabular-nums">{tax.tax_year}</span>
        {p.fema_label && <span className="chip text-ink-muted" title="FEMA status: a separate question from tax residence">{p.fema_label}</span>}
        {p.fema?.country && <span className="chip text-ink-muted">{p.fema.country}</span>}
        {p.differs && <span className="chip text-warn" title="FEMA status and tax residence are two tests">Tests differ</span>}
      </div>
      <div className="flex flex-wrap gap-1.5">
        {tax.needs.map((n: string) => <span key={n} className="chip text-warn">Needs: {n}</span>)}
        {accounts.map((a) => <span key={a.kind} className="chip text-ink-muted" title={`${a.repatriable}. ${a.holds}`}>{a.kind}</span>)}
        {p.fema?.pis?.recorded && <span className="chip text-ink-muted" title={p.fema.pis.reference}>PIS letter</span>}
        {p.fatca_crs?.on_file && <span className="chip text-ok" title={`Self-certified ${p.fatca_crs.self_certified_on}`}>FATCA and CRS on file</span>}
        {(p.fatca_crs?.flags ?? []).map((f: any) => <span key={f.key} className={`chip ${FLAG_TONE[f.tone]}`}>{f.text}</span>)}
        {(p.fatca_crs?.indicia ?? []).map((i: any) => <span key={i.key} className="chip text-warn">{i.label}</span>)}
        {p.treatment && <span className="chip text-ink-muted" title={p.treatment.not_modelled.join(". ")}>
          Tax at source {pct(p.treatment.tds[0].rate)} / {pct(p.treatment.tds[1].rate)}</span>}
      </div>
    </div>
  );
}

/** The households with a residence on file, person by person. */
export function ResidencyPeople({ households }: { households: any[] }) {
  return (
    <div className="divide-y divide-border-soft" data-testid="residency-list">
      {households.slice(0, SHOWN).map((h) => (
        <div key={h.household_id ?? h.household}>
          {h.household && <div className="px-3 pt-2 text-xs uppercase tracking-wide text-ink-muted">{h.household}</div>}
          <div className="divide-y divide-border-soft">{(h.people ?? []).map((p: any) => <PersonRow key={p.person_id} p={p} />)}</div>
        </div>
      ))}
      {households.length > SHOWN && <div className="px-3 py-2"><span className="chip text-ink-muted">+{households.length - SHOWN} more</span></div>}
    </div>
  );
}

/** Each minor and who runs the account. */
export function GuardianChips({ guardians }: { guardians: any }) {
  const minors: any[] = guardians?.minors ?? [];
  if (!guardians?.applies || minors.length === 0) return null;
  return (
    <div className="divide-y divide-border-soft" data-testid="guardian-list">
      {minors.map((m) => (
        <div key={m.person_id} className="flex flex-wrap items-center gap-1.5 px-3 py-1.5" title={m.citation}>
          <span className="text-sm text-ink mr-auto">{m.name}</span>
          {m.age != null && <span className="chip text-ink-muted tabular-nums">Age {m.age}</span>}
          {m.guardian && <span className="chip text-ink-muted">{m.guardian.name}, {m.guardian.relationship_label.toLowerCase()}</span>}
          <span className={`chip ${MINOR_TONE[m.state]}`}>{MINOR_TEXT[m.state]}</span>
          {m.majority_on && <span className="chip text-ink-muted tabular-nums">Adult {m.majority_on}</span>}
          {m.converted_on && <span className="chip text-ink-muted tabular-nums">Converted {m.converted_on}</span>}
        </div>
      ))}
    </div>
  );
}

/** A client's residence as a person recorded it, and each minor's guardian. */
export function ResidencyCard({ result }: { result: any }) {
  if (result.applies === false) {
    return <div className="rounded-lg border border-border bg-surface px-3 py-2" data-testid="residency-card"><span className="chip text-ink-muted">Not an Indian firm</span></div>;
  }
  const households: any[] = result.households ?? (result.people ? [{ household_id: result.household_id, household: result.household, people: result.people }] : []);
  const c = result.counts;
  return (
    <div className="overflow-hidden rounded-lg border border-border bg-surface" data-testid="residency-card">
      <div className="flex flex-wrap items-center justify-between gap-2 border-b border-border-soft px-3 py-2">
        <div className="text-sm font-medium text-ink">Residence</div>
        {c && (
          <div className="flex flex-wrap gap-1.5">
            <span className="chip text-ink-muted">{c.non_resident} non-resident</span>
            <span className={`chip ${c.not_on_file ? "text-warn" : "text-ok"}`}>{c.not_on_file} not assessed</span>
          </div>
        )}
      </div>
      {households.length === 0 ? <div className="px-3 py-2"><span className="chip text-ink-muted">Nothing on file</span></div> : <ResidencyPeople households={households} />}
      <GuardianChips guardians={result.guardians} />
    </div>
  );
}

/** A PMS client's checks, each against its figure: held, passing or not assessed. */
export function PmsClients({ clients }: { clients: any[] }) {
  return (
    <div className="divide-y divide-border-soft" data-testid="pms-list">
      {clients.slice(0, SHOWN).map((c) => (
        <div key={c.mandate_id} className="space-y-1.5 px-3 py-2" data-testid="pms-client">
          <div className="flex flex-wrap items-center gap-1.5">
            <span className="text-sm text-ink mr-auto">{c.client ?? c.mandate}</span>
            {c.agreement && <span className="chip text-ink-muted">{c.agreement.kind_label}</span>}
            {c.agreement?.strategy && <span className="chip text-ink-muted">{c.agreement.strategy}</span>}
            {c.value > 0 && <span className="chip text-ink-muted tabular-nums">{money(c.value, "INR")}</span>}
            <span className={`chip ${c.held.length ? "text-crit" : c.agreement ? "text-ok" : "text-warn"}`}>
              {c.held.length ? "Approval held" : c.agreement ? "Agreement on file" : "No agreement recorded"}
            </span>
          </div>
          <div className="flex flex-wrap gap-1.5">
            {c.checks.map((k: any) => (
              <span key={k.key} className={`chip ${CHECK_TONE[k.state]}`} title={`${k.detail} · ${k.source}`} data-testid={`pms-check-${k.key}`}>
                {k.label} · {CHECK_TEXT[k.state]}
              </span>
            ))}
            {c.agreement?.fee_label && <span className="chip text-ink-muted">{c.agreement.fee_label}</span>}
            {c.agreement?.fixed_rate != null && <span className="chip text-ink-muted tabular-nums">Fixed {pct(c.agreement.fixed_rate)}</span>}
            {c.agreement?.performance_rate != null && (
              <span className="chip text-ink-muted tabular-nums">
                {pct(c.agreement.performance_rate)} over {pct(c.agreement.hurdle_rate)}{c.agreement.high_water_mark ? ", own high-water mark" : ""}
              </span>
            )}
          </div>
        </div>
      ))}
      {clients.length > SHOWN && <div className="px-3 py-2"><span className="chip text-ink-muted">+{clients.length - SHOWN} more</span></div>}
    </div>
  );
}

/** The Portfolio Manager's clients against their agreements and the Master Circular. */
export function PmsCard({ result }: { result: any }) {
  if (result.applies === false) {
    return <div className="rounded-lg border border-border bg-surface px-3 py-2" data-testid="pms-card"><span className="chip text-ink-muted">No Portfolio Manager licence</span></div>;
  }
  return (
    <div className="overflow-hidden rounded-lg border border-border bg-surface" data-testid="pms-card">
      <div className="flex flex-wrap items-center justify-between gap-2 border-b border-border-soft px-3 py-2">
        <div className="text-sm font-medium text-ink">Portfolio Manager clients</div>
        <div className="flex flex-wrap gap-1.5">
          <span className={`chip ${result.held ? "text-crit" : "text-ok"}`}>{result.held} held</span>
          <span className={`chip ${result.without_agreement ? "text-warn" : "text-ink-muted"}`}>{result.without_agreement} not assessed</span>
        </div>
      </div>
      <PmsClients clients={result.clients} />
    </div>
  );
}

/** The last quarter for one PMS client: figures and their basis. A draft for the manager, never for the client. */
export function PmsReportCard({ result }: { result: any }) {
  const tile = (label: string, value: string) => (
    <div key={label}><div className="tile-label">{label}</div><div className="text-sm tabular-nums text-ink">{value}</div></div>
  );
  return (
    <div className="overflow-hidden rounded-lg border border-border bg-surface" data-testid="pms-report-card">
      <div className="flex flex-wrap items-center justify-between gap-2 border-b border-border-soft px-3 py-2">
        <div className="text-sm font-medium text-ink">{result.client}, {result.quarter}</div>
        <span className="chip text-warn" title={result.not_publishable_because}>Not for the client</span>
      </div>
      <div className="grid grid-cols-2 gap-3 px-3 py-2 cq-md:grid-cols-4">
        {tile("Opening", result.opening_value == null ? "-" : money(result.opening_value, "INR"))}
        {tile("Closing", result.closing_value == null ? "-" : money(result.closing_value, "INR"))}
        {tile("Return", result.return == null ? "Not assessed" : pct(result.return))}
        {tile("Fees billed", money(result.fees_billed, "INR"))}
      </div>
      <div className="flex flex-wrap gap-1.5 border-t border-border-soft px-3 py-2">
        <span className="chip text-ink-muted">{result.basis}</span>
        {result.benchmarks.map((b: any) => <span key={b.name} className="chip text-ink-muted" title={b.note}>{b.name}: not on file</span>)}
      </div>
    </div>
  );
}
