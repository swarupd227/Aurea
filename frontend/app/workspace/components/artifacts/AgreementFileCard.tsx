import { money } from "@/lib/format";
import { reviewDay } from "./ReviewsCard";
import { Stance } from "./FundAcceptanceCard";

/** B-391: India's agreements, disclosures and the NRI checklist, per relationship. Figures, tables and chips. */

const STATE: Record<string, [string, string]> = {
  on_file: ["on file", "text-ok"], not_on_file: ["not on file", "text-warn"], breach: ["fails", "text-crit"],
  not_asked: ["not asked", "text-ink-muted"], recorded: ["EUIN", "text-ok"], execution_only: ["execution only", "text-ok"],
  direct_plan: ["direct plan", "text-ink-muted"], missing: ["no EUIN", "text-crit"], paid: ["paid", "text-ok"],
  unpaid: ["unpaid", "text-warn"], overdue: ["overdue", "text-crit"],
};

export function StateChip({ state, label, title }: { state: string; label?: string; title?: string | null }) {
  const [text, tone] = STATE[state] ?? [state.replace(/_/g, " "), "text-ink-muted"];
  return <span className={`chip ${tone}`} title={title ?? undefined}>{label ?? text}</span>;
}

function Head({ children, chips }: { children: React.ReactNode; chips?: React.ReactNode }) {
  return (
    <div className="flex flex-wrap items-center gap-1.5 px-3 pt-2">
      <span className="mr-auto text-[11px] font-medium uppercase tracking-wide text-ink-muted">{children}</span>
      {chips}
    </div>
  );
}

function Items({ items }: { items: any[] }) {
  return (
    <div className="flex flex-wrap gap-1.5 px-3 py-1.5">
      {items.filter((i) => i.required || i.state !== "not_on_file").map((i) => (
        <span key={i.item} className={`chip ${STATE[i.state]?.[1] ?? ""}`}
          title={i.why ?? (i.dated_on ? `${i.answer_label} · ${reviewDay(i.dated_on)} · ${i.document}` : undefined)}>
          {i.label} · {i.state === "on_file" ? i.answer_label : STATE[i.state]?.[0]}
        </span>
      ))}
    </div>
  );
}

export function MutualFunds({ funds }: { funds: any }) {
  if (!funds.schemes.length && !funds.commission.asked) return null;
  return (
    <div className="pb-1.5" data-testid="agreement-funds">
      <Head chips={<>
        <span className={`chip tabular-nums ${funds.missing_euin ? "text-crit" : "text-ok"}`}>{funds.missing_euin} without EUIN</span>
        <StateChip state={funds.commission.state} label={`commission disclosure ${STATE[funds.commission.state]?.[0]}`} title={funds.commission.why} />
      </>}>Mutual funds</Head>
      {funds.orders.length > 0 && (
        <div className="overflow-x-auto">
          <table className="w-full text-xs" data-testid="agreement-orders">
            <tbody className="divide-y divide-border-soft">
              {funds.orders.slice(0, 8).map((o: any) => (
                <tr key={o.order_id}>
                  <td className="px-3 py-1 text-ink" title={o.account}>{o.symbol}</td>
                  <td className="px-3 py-1 text-ink-muted">{o.side} · {reviewDay(o.on)}</td>
                  <td className="px-3 py-1 text-right"><StateChip state={o.euin_state} label={o.euin_state === "recorded" ? o.euin : undefined} title={o.document} /></td>
                </tr>
              ))}
            </tbody>
          </table>
          {funds.orders.length > 8 && <div className="px-3 py-1"><span className="chip text-ink-muted">+{funds.orders.length - 8} more</span></div>}
        </div>
      )}
      {funds.schemes.length > 0 && (
        <div className="flex flex-wrap gap-1.5 px-3 py-1.5" data-testid="agreement-schemes">
          {funds.schemes.map((s: any) => (
            <span key={s.symbol} className={`chip ${s.given.length === 2 ? "text-ok" : "text-warn"}`}
              title={`held by the firm: ${["sid", "kim"].filter((k) => s.on_file[k]).join(", ").toUpperCase() || "none"}`}>
              {s.symbol} · given {s.given.length ? s.given.join(", ").toUpperCase() : "nothing recorded"}
            </span>
          ))}
        </div>
      )}
    </div>
  );
}

export function PmsPapers({ pms }: { pms: any[] }) {
  if (!pms.length) return null;
  return (
    <div data-testid="agreement-pms">
      {pms.map((m) => (
        <div key={m.mandate_id}>
          <Head chips={<span className="chip text-ink-muted">{m.agreement_signed_on ? `agreement ${reviewDay(m.agreement_signed_on)}` : "agreement not on file"}</span>}>PMS · {m.mandate}</Head>
          <Items items={m.items} />
        </div>
      ))}
    </div>
  );
}

export function AifPapers({ aif }: { aif: any[] }) {
  if (!aif.length) return null;
  return (
    <div data-testid="agreement-aif">
      {aif.map((c) => (
        <div key={c.commitment_id} className="pb-1.5">
          <Head chips={<>
            <span className="chip tabular-nums text-ink-muted">{money(c.commitment, "INR")} committed</span>
            <span className="chip tabular-nums text-ink-muted">{money(c.uncalled, "INR")} uncalled</span>
            {c.unpaid > 0 && <span className={`chip tabular-nums ${c.overdue ? "text-crit" : "text-warn"}`}>{money(c.unpaid, "INR")} unpaid</span>}
            {c.below_minimum && <span className="chip text-warn">below minimum</span>}
          </>}>AIF · {c.fund} · {c.investor}</Head>
          <Items items={c.items} />
          {c.drawdowns.length > 0 && (
            <table className="w-full text-xs" data-testid="agreement-drawdowns">
              <tbody className="divide-y divide-border-soft">
                {c.drawdowns.map((d: any) => (
                  <tr key={d.drawdown_id} title={d.document}>
                    <td className="px-3 py-1 text-ink">Drawdown {d.number}</td>
                    <td className="px-3 py-1 text-ink-muted">due {reviewDay(d.due_on)}</td>
                    <td className="px-3 py-1 text-right font-mono tabular-nums text-ink">{money(d.amount, "INR")}</td>
                    <td className="px-3 py-1 text-right"><StateChip state={d.state} label={d.state === "paid" ? "paid" : `${money(d.unpaid, "INR")} ${d.state}`} /></td>
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

export function Broking({ broking }: { broking: any[] }) {
  if (!broking.length) return null;
  return (
    <div data-testid="agreement-broking">
      {broking.map((a) => (
        <div key={a.account_id}>
          <Head>Broking and demat · {a.account}</Head>
          <Items items={a.items} />
        </div>
      ))}
    </div>
  );
}

export function Stamps({ stamps }: { stamps: any[] }) {
  if (!stamps.length) return null;
  return (
    <div className="pb-1.5" data-testid="agreement-estamps">
      <Head>E-stamp</Head>
      <table className="w-full text-xs">
        <tbody className="divide-y divide-border-soft">
          {stamps.map((e) => (
            <tr key={e.certificate_number} title={`${e.parties} · ${e.document}`}>
              <td className="px-3 py-1 text-ink">{e.instrument}</td>
              <td className="px-3 py-1 font-mono text-ink-muted">{e.certificate_number}</td>
              <td className="px-3 py-1 text-ink-muted">{e.state} · {reviewDay(e.stamped_on)}</td>
              <td className="px-3 py-1 text-right font-mono tabular-nums text-ink">{money(e.duty, "INR")}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

export function NriIntake({ people }: { people: any[] }) {
  if (!people.length) return null;
  return (
    <div data-testid="agreement-nri">
      {people.map((p) => (
        <div key={p.person_id} className="pb-1.5">
          <Head chips={p.reached.length ? <>
            <span className={`chip tabular-nums ${p.refused ? "text-crit" : "text-ink-muted"}`}>{p.refused} refused</span>
            <span className="chip tabular-nums text-ink-muted">{p.conditions} on conditions</span>
            <span className="chip tabular-nums text-ink-muted">{p.not_assessed} not assessed</span>
          </> : null}>NRI intake · {p.name}</Head>
          <div className="flex flex-wrap gap-1.5 px-3 py-1.5">
            {p.checks.map((c: any) => (
              <span key={c.key} className={`chip ${STATE[c.state]?.[1] ?? ""}`} title={c.why ?? undefined} data-testid={`nri-check-${c.key}`}>
                {c.label} · {c.state === "breach" ? "does not match the account" : c.value ?? "not on file"}
              </span>
            ))}
          </div>
          {p.acceptance.some((a: any) => a.state === "refuses" || a.state === "conditions") && (
            <div className="flex flex-wrap items-center gap-1.5 px-3 pb-1">
              {p.acceptance.filter((a: any) => a.state === "refuses" || a.state === "conditions").map((a: any) => (
                <span key={`${a.symbol}-${a.country}`} className="inline-flex items-center gap-1 text-xs text-ink">
                  {a.symbol} <Stance state={a.state} title={a.conditions ?? a.document} />
                </span>
              ))}
            </div>
          )}
        </div>
      ))}
    </div>
  );
}

export function TrustCases({ cases, onRecord }: { cases: any[]; onRecord?: (c: any) => void }) {
  const shown = cases.filter((c) => c.recorded);
  if (!shown.length) return null;
  return (
    <div data-testid="agreement-trusts">
      {shown.map((c) => (
        <div key={c.case_id} className="pb-1.5">
          <Head chips={<>
            <span className={`chip tabular-nums ${c.missing.length ? "text-crit" : "text-ok"}`}>{c.missing.length ? `${c.missing.length} not a party · activation held` : "every owner a party"}</span>
            {onRecord && c.missing.length > 0 && <button className="btn-outline text-xs" data-testid="trust-record-parties" onClick={() => onRecord(c)}>Record as parties</button>}
          </>}>Beneficial owners · {c.case}</Head>
          <div className="flex flex-wrap gap-1.5 px-3 py-1.5">
            {c.owners.map((o: any) => (
              <span key={o.name} className={`chip ${o.party_id ? "text-ok" : "text-warn"}`} title={c.document}>
                {o.name} · {o.roles_label}{o.share !== null ? ` ${o.share}%` : ""} · {o.party_id ? (o.screening ?? "").replace(/_/g, " ") : "unscreened"}
              </span>
            ))}
          </div>
        </div>
      ))}
    </div>
  );
}

export function HouseholdAgreements({ view }: { view: any }) {
  const nothing = !view.funds.schemes.length && !view.funds.commission.asked && !view.pms.length && !view.aif.length && !view.broking.length
    && !view.estamps.length && !view.nri.length && !view.trusts.length;
  return (
    <div className="divide-y divide-border-soft">
      <MutualFunds funds={view.funds} />
      <PmsPapers pms={view.pms} />
      <AifPapers aif={view.aif} />
      <Broking broking={view.broking} />
      <Stamps stamps={view.estamps} />
      <NriIntake people={view.nri} />
      <TrustCases cases={view.trusts} />
      {nothing && <div className="px-3 py-2"><span className="chip text-ink-muted">no relationship paper applies on what is on file</span></div>}
    </div>
  );
}

const COUNTS: [string, string, string][] = [
  ["drawdowns_overdue", "overdue drawdown", "text-crit"], ["pms_breaches", "PMS fails", "text-crit"], ["missing_euin", "without EUIN", "text-crit"],
  ["drawdowns_unpaid", "unpaid drawdown", "text-warn"], ["commission_missing", "commission disclosure", "text-warn"],
  ["pms_not_on_file", "PMS not on file", "text-warn"], ["broking_missing", "demat papers", "text-warn"], ["nri_issues", "NRI intake open", "text-warn"],
];

export function FirmAgreements({ view, link }: { view: any; link?: (h: any) => React.ReactNode }) {
  return (
    <div className="divide-y divide-border-soft">
      <div data-testid="agreement-households">
        {view.households.map((h: any) => (
          <div key={h.household_id} className="flex flex-wrap items-center gap-1.5 px-3 py-1.5">
            <span className="mr-auto text-sm text-ink" title={h.relationships.join(", ")}>{link ? link(h) : h.household}</span>
            {COUNTS.filter(([k]) => h.counts[k] > 0).map(([k, label, tone]) => <span key={k} className={`chip tabular-nums ${tone}`}>{h.counts[k]} {label}</span>)}
          </div>
        ))}
      </div>
      {view.nri_cases.length > 0 && (
        <div className="flex flex-wrap gap-1.5 px-3 py-2" data-testid="agreement-nri-cases">
          {view.nri_cases.map((c: any) => (
            <span key={c.case_id} className={`chip ${c.mismatch ? "text-crit" : c.intent ? "text-ok" : "text-warn"}`}>
              {c.case} · {c.registration_type === "nri_nre" ? "NRE" : "NRO"} · {c.mismatch ? "does not match intent" : c.intent_label ?? "intent not recorded"}
            </span>
          ))}
        </div>
      )}
    </div>
  );
}

export function AgreementFileCard({ result }: { result: any }) {
  if (!result) return null;
  const shell = (title: string, body: React.ReactNode, chips?: React.ReactNode) => (
    <div className="overflow-hidden rounded-lg border border-border bg-surface" data-testid="agreement-file-card">
      <div className="flex flex-wrap items-center justify-between gap-2 border-b border-border-soft px-3 py-2">
        <span className="text-sm font-medium text-ink">{title}</span>
        <div className="flex flex-wrap gap-1.5">{chips}<span className="chip text-ink-muted">rules assumed</span></div>
      </div>
      {body}
    </div>
  );
  // One trust case after its owners were recorded as parties.
  if (Array.isArray(result.added) && result.case_id) {
    return shell(`Trust parties · ${result.case}`, <TrustCases cases={[{ ...result, recorded: true }]} />,
      <span className="chip tabular-nums text-ok">{result.added.length} added and screened</span>);
  }
  if (!result.applies) return null;
  if (result.scope === "scheme") {
    return shell(`Scheme documents · ${result.symbol}`, (
      <div className="flex flex-wrap gap-1.5 px-3 py-2">
        {["sid", "kim"].map((k) => <StateChip key={k} state={result.on_file[k] ? "on_file" : "not_on_file"}
          label={`${k.toUpperCase()} ${result.on_file[k] ? reviewDay(result.on_file[k]) : "not on file"}`} />)}
      </div>
    ));
  }
  if (result.scope === "firm") {
    const t = result.totals;
    return shell("Agreement files", <><FirmAgreements view={result} /><TrustCases cases={result.trusts} /></>,
      t.unpaid_amount ? <span className="chip tabular-nums text-warn">{money(t.unpaid_amount, "INR")} drawdowns unpaid</span> : null);
  }
  return shell(`Agreement file · ${result.household}`, <HouseholdAgreements view={result} />);
}
