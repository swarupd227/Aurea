const SHOWN = 8;
export const REGISTRY_TONE: Record<string, string> = {
  validated: "text-ok", registered: "text-warn", on_hold: "text-crit", rejected: "text-crit", not_found: "text-warn", not_looked_up: "text-warn",
};
const STEP_TONE: Record<string, string> = { open: "text-warn", asked: "text-ink-muted", received: "text-ink-muted", done: "text-ok", not_needed: "text-ink-muted" };

/** The registry's status, apart from the firm's own account state. */
export function RegistryChip({ registry, prefix = "KYC" }: { registry: any; prefix?: string }) {
  if (!registry) return null;
  return (
    <span className={`chip ${REGISTRY_TONE[registry.status] ?? "text-ink-muted"}`} data-testid="kyc-registry-status">
      {prefix} {String(registry.label).toLowerCase()}
    </span>
  );
}

/** What has to happen before a timeline may be promised, and whether the answer is simulated. */
export function TimelineChips({ registry }: { registry: any }) {
  if (!registry) return null;
  return (
    <>
      {registry.timeline && <span className="chip text-warn" data-testid="kyc-registry-timeline">{registry.timeline}</span>}
      {registry.simulated && <span className="chip text-warn" data-testid="kyc-registry-simulated">simulated</span>}
    </>
  );
}

/** One party: the answer, what it holds, what is wrong, the steps and the attempts to reach the client. */
export function RegistryParty({ p, registry, steps, contacts, backlog, reasonRecorded = true, action }: {
  p: string; registry: any; steps: any[]; contacts?: any; backlog?: any; reasonRecorded?: boolean; action?: React.ReactNode;
}) {
  return (
    <div className="space-y-1.5 px-3 py-2" data-testid="kyc-registry-party">
      <div className="flex flex-wrap items-center gap-1.5">
        <span className="mr-auto text-sm text-ink">{p}</span>
        <RegistryChip registry={registry} prefix="Registry:" />
        {registry.answered_on && <span className="chip text-ink-muted tabular-nums">{registry.agency ?? registry.registry} {registry.answered_on}</span>}
        {registry.simulated && <span className="chip text-warn">simulated</span>}
        {backlog?.age_days != null && <span className="chip text-ink-muted tabular-nums" title={backlog.reasons.join(", ")}>open {backlog.age_days}d</span>}
        {action}
      </div>
      {(registry.holds?.length > 0 || registry.deficiencies?.length > 0 || !reasonRecorded) && (
        <div className="flex flex-wrap gap-1.5">
          {(registry.deficiencies ?? []).map((d: any) => <span key={d.code} className="chip text-crit">{d.label}</span>)}
          {!reasonRecorded && <span className="chip text-warn">Reason not recorded</span>}
          {(registry.holds ?? []).map((h: string) => <span key={h} className="chip text-ink-muted">Holds {h}</span>)}
        </div>
      )}
      {steps.length > 0 && (
        <table className="w-full text-xs" data-testid="kyc-registry-steps">
          <tbody className="divide-y divide-border-soft">
            {steps.map((s) => (
              <tr key={s.step}>
                <td className="py-1 pr-2 text-ink-soft">{s.action}</td>
                <td className="py-1 pr-2 text-right"><span className={`chip ${STEP_TONE[s.state]}`}>{s.state_label}</span></td>
                <td className="py-1 text-right font-mono tabular-nums text-ink-muted">{s.on ?? "-"}</td>
              </tr>
            ))}
          </tbody>
        </table>
      )}
      {contacts && (backlog || contacts.attempts > 0) && (
        <div className="flex flex-wrap gap-1.5" data-testid="kyc-registry-contacts">
          {contacts.attempts > 0 ? (
            <span className="chip text-ink-muted tabular-nums">
              {contacts.attempts} contact attempt{contacts.attempts === 1 ? "" : "s"}, last {contacts.last_on} {String(contacts.last_channel ?? "").toLowerCase()}: {String(contacts.last_outcome ?? "").toLowerCase()}
            </span>
          ) : <span className="chip text-warn">No contact attempt recorded</span>}
        </div>
      )}
    </div>
  );
}

export function RegistryMix({ mix }: { mix: any[] }) {
  return (
    <div className="grid grid-cols-2 gap-2 px-3 py-2 sm:grid-cols-4" data-testid="kyc-registry-mix">
      {mix.map((m) => (
        <div key={m.status}>
          <div className="text-[10px] uppercase tracking-wide text-ink-muted">{m.label}</div>
          <div className={`font-mono text-lg tabular-nums ${REGISTRY_TONE[m.status]}`}>{m.count}
            {m.share != null && <span className="ml-1 text-xs text-ink-muted">{Math.round(m.share * 100)}%</span>}
          </div>
        </div>
      ))}
    </div>
  );
}

export function RegistryBands({ bands }: { bands: any[] }) {
  return (
    <table className="w-full text-sm" data-testid="kyc-registry-backlog">
      <thead><tr className="text-left text-[10px] uppercase tracking-wide text-ink-muted">
        <th className="px-3 py-1 font-medium">Re-KYC backlog age</th><th className="px-3 py-1 text-right font-medium">Open</th>
        <th className="px-3 py-1 text-right font-medium">No contact recorded</th>
      </tr></thead>
      <tbody className="divide-y divide-border-soft">
        {bands.map((b) => (
          <tr key={b.key}>
            <td className="px-3 py-1 text-ink-soft">{b.label}</td>
            <td className="px-3 py-1 text-right font-mono tabular-nums text-ink">{b.count}</td>
            <td className={`px-3 py-1 text-right font-mono tabular-nums ${b.no_contact ? "text-warn" : "text-ink-muted"}`}>{b.no_contact}</td>
          </tr>
        ))}
      </tbody>
    </table>
  );
}

/** The registry status of the book or of one case: what a read or a record shows back. */
export function KycRegistryCard({ result }: { result: any }) {
  if (result.applies === false) {
    return <div className="rounded-lg border border-border bg-surface px-3 py-2" data-testid="kyc-registry-card"><span className="chip text-ink-muted">Not an Indian firm</span></div>;
  }
  const one = result.case_id ? result.cases?.[0] : null;
  const named = result.recorded?.party;
  const parties: any[] = result.parties ?? [];
  // What was just recorded comes first, then whoever is stopped or in the backlog.
  const shown = [...parties].sort((a, b) => Number(b.name === named) - Number(a.name === named) || Number(!!b.backlog) - Number(!!a.backlog)).slice(0, SHOWN);
  const waiting: any[] = (result.cases ?? []).filter((c: any) => !c.timeline_ok);
  return (
    <div className="overflow-hidden rounded-lg border border-border bg-surface" data-testid="kyc-registry-card">
      <div className="flex flex-wrap items-center justify-between gap-2 border-b border-border-soft px-3 py-2">
        <div className="text-sm font-medium text-ink">{one ? `KYC registry: ${one.client}` : "KYC registry"}</div>
        <div className="flex flex-wrap gap-1.5">
          {one ? <><RegistryChip registry={one} /><TimelineChips registry={one} /></> : (
            <>
              {result.counts.backlog > 0 && <span className="chip text-warn">{result.counts.backlog} re-KYC open</span>}
              {result.counts.cases_not_looked_up > 0 && <span className="chip text-warn">{result.counts.cases_not_looked_up} cases not looked up</span>}
            </>
          )}
          {result.task_raised && <span className="chip text-ink-muted">task for {result.task_raised.owner ?? "nobody assigned"}</span>}
          {!result.connected && <span className="chip text-ink-muted" title={result.not_connected}>Lookup not connected</span>}
          {result.provider?.simulated && <span className="chip text-warn">simulated lookup</span>}
        </div>
      </div>
      {one ? (
        <div className="divide-y divide-border-soft">
          {one.parties.map((p: any) => <RegistryParty key={p.name} p={p.name} registry={p} steps={p.steps ?? []} contacts={p.contacts} />)}
        </div>
      ) : (
        <>
          <RegistryMix mix={result.mix ?? []} />
          {result.counts.backlog > 0 && <div className="border-t border-border-soft"><RegistryBands bands={result.bands} /></div>}
          <div className="divide-y divide-border-soft border-t border-border-soft">
            {shown.map((p) => (
              <RegistryParty key={p.person_id ?? p.entity_id ?? `${p.case_id}-${p.name}`} p={p.name} registry={p.registry} steps={p.steps}
                contacts={p.contacts} backlog={p.backlog} reasonRecorded={p.reason_recorded} />
            ))}
            {parties.length > SHOWN && <div className="px-3 py-2"><span className="chip text-ink-muted">+{parties.length - SHOWN} more</span></div>}
          </div>
          {waiting.length > 0 && (
            <div className="space-y-1.5 border-t border-border-soft px-3 py-2" data-testid="kyc-registry-cases">
              <div className="text-[10px] uppercase tracking-wide text-ink-muted">Cases with no timeline yet</div>
              {waiting.slice(0, SHOWN).map((c) => (
                <div key={c.case_id} className="flex flex-wrap items-center gap-1.5">
                  <span className="mr-auto text-sm text-ink">{c.client}</span>
                  <RegistryChip registry={c} /><TimelineChips registry={c} />
                </div>
              ))}
              {waiting.length > SHOWN && <span className="chip text-ink-muted">+{waiting.length - SHOWN} more</span>}
            </div>
          )}
        </>
      )}
    </div>
  );
}

/** A client's own status, in plain words: never anything about screening or monitoring. */
export function MyKycCard({ result }: { result: any }) {
  if (result.applies === false) {
    return <div className="rounded-lg border border-border bg-surface px-3 py-2" data-testid="my-kyc-card"><span className="chip text-ink-muted">Nothing to show</span></div>;
  }
  return (
    <div className="overflow-hidden rounded-lg border border-border bg-surface" data-testid="my-kyc-card">
      <div className="flex flex-wrap items-center justify-between gap-2 border-b border-border-soft px-3 py-2">
        <div className="text-sm font-medium text-ink">Your KYC</div>
        {result.adviser && <span className="chip text-ink-muted">{result.adviser}</span>}
      </div>
      <MyKycPeople people={result.people ?? []} />
    </div>
  );
}

export function MyKycPeople({ people }: { people: any[] }) {
  return (
    <div className="divide-y divide-border-soft">
      {people.length === 0 && <div className="px-3 py-2"><span className="chip text-ink-muted">Nothing on file</span></div>}
      {people.map((p) => (
        <div key={p.name} className="space-y-1.5 px-3 py-2" data-testid="my-kyc-person">
          <div className="flex flex-wrap items-center gap-1.5">
            <span className="mr-auto text-sm text-ink">{p.name}</span>
            <span className={`chip ${REGISTRY_TONE[p.status]}`}>{p.label}</span>
            <span className="chip text-ink-muted">{p.meaning}</span>
            {p.checked_on && <span className="chip text-ink-muted tabular-nums">checked {p.checked_on}</span>}
            {p.simulated && <span className="chip text-warn">simulated</span>}
          </div>
          {(p.reasons.length > 0 || p.refresh) && (
            <div className="flex flex-wrap gap-1.5">
              {p.reasons.map((r: string) => <span key={r} className="chip text-crit">{r}</span>)}
              {p.refresh && <span className="chip text-warn">{p.refresh}</span>}
            </div>
          )}
          {p.steps.length > 0 && (
            <table className="w-full text-xs" data-testid="my-kyc-steps">
              <tbody className="divide-y divide-border-soft">
                {p.steps.map((s: any) => (
                  <tr key={s.action}>
                    <td className="py-1 pr-2 text-ink-soft">{s.action}</td>
                    <td className="py-1 text-right"><span className={`chip ${STEP_TONE[s.state]}`}>{s.state_label}</span></td>
                  </tr>
                ))}
              </tbody>
            </table>
          )}
        </div>
      ))}
    </div>
  );
}
