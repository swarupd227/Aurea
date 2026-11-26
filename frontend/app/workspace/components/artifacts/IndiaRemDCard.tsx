"use client";

import { money } from "@/lib/format";

// The India remainders: a PMS client's value per unit with its high-water mark, return and verifier's letter, a fee line and whether it was
// debited, a manager's own staff, a minor's guardian papers, the KYC review period by band, and the real estate gains choice. Figures and
// chips only: the agent's message carries the sentences, and every source, caveat and reason is a chip's title.

const VERIFY_TONE: Record<string, string> = { verified: "text-ok", not_verified: "text-warn" };
const VERIFY_TEXT: Record<string, string> = { verified: "Verified", not_verified: "Not verified" };
const pct = (v: number | null | undefined) => (v == null ? "Not assessed" : `${(v * 100).toFixed(2)}%`);

/** Each PMS client: the unit ledger's latest value and high-water mark, the quarter's return from it, the verifier's letter, and each confirmed fee line. */
export function PmsLedgerRows({ data }: { data: any }) {
  const lines: any[] = data.lines ?? [];
  return (
    <div className="divide-y divide-border-soft" data-testid="pms-ledger">
      {(data.clients ?? []).map((c: any) => (
        <div key={c.mandate_id} className="flex flex-wrap items-center gap-1.5 px-3 py-1.5" data-testid="pms-ledger-client" title={c.message ?? c.basis}>
          <span className="mr-auto min-w-0 flex-1 text-sm text-ink">{c.client}</span>
          {c.count === 0 ? (
            <span className="chip text-warn" title={c.message}>No unit values: not assessed</span>
          ) : (
            <>
              <span className="chip tabular-nums text-ink-muted" title={`The latest value per unit, ${c.latest?.as_of}`}>{c.latest?.nav_per_unit.toFixed(2)} a unit</span>
              <span className="chip tabular-nums text-ink-muted" title={`The client's own high-water mark, reached ${c.high_water_mark?.as_of}`}>HWM {c.high_water_mark?.nav_per_unit.toFixed(2)}</span>
              <span className="chip tabular-nums text-ink-muted" title={c.quarter.reason ?? `Time-weighted from value per unit, ${c.quarter.start} to ${c.quarter.end}`}>{c.quarter.label} {pct(c.quarter.return)}</span>
              {(c.quarter.gaps ?? []).length > 0 && <span className="chip text-warn" title="A month is missing between two values">{c.quarter.gaps.length} gap</span>}
            </>
          )}
          <span className={`chip ${VERIFY_TONE[c.verification.state]}`} title={c.verification.said} data-testid="pms-verification-state">{VERIFY_TEXT[c.verification.state]}</span>
        </div>
      ))}
      {lines.map((l) => (
        <div key={l.id} className="flex flex-wrap items-center gap-1.5 px-3 py-1" data-testid="pms-fee-line" title={`Confirmed by ${l.confirmed_by}`}>
          <span className="mr-auto min-w-0 flex-1 text-xs text-ink-soft">{l.mandate} · {l.period_start} to {l.period_end}</span>
          <span className="chip tabular-nums text-ink-muted">{money(l.amount, l.currency)}</span>
          <span className={`chip ${l.basis === "unit ledger" ? "text-ok" : "text-warn"}`} title="Whether the fee was worked on the unit ledger or estimated on the book">{l.basis === "unit ledger" ? "Ledger" : "Estimate"}</span>
          <span className={`chip ${l.debited ? "text-ok" : "text-ink-muted"}`} title={l.debited ? `Debited by ${l.debited_by}` : "Taken only by a second person behind a Confirm card"}>{l.debited ? "Debited" : "Not debited"}</span>
        </div>
      ))}
    </div>
  );
}

/** Each minor's guardian KYC and PAN, and whether the account may trade. */
export function GuardianPaperRows({ data }: { data: any }) {
  return (
    <div className="divide-y divide-border-soft" data-testid="guardian-papers">
      {(data.minors ?? []).map((m: any) => (
        <div key={m.person_id} className="flex flex-wrap items-center gap-1.5 px-3 py-1.5" data-testid="guardian-paper">
          <span className="mr-auto text-sm text-ink">{m.name}<span className="ml-2 text-xs text-ink-muted">{m.age}</span></span>
          <span className={`chip ${m.guardian_kyc === "on_file" ? "text-ok" : "text-warn"}`} title={m.guardian ? `Guardian ${m.guardian}` : "No guardian recorded"}>
            {m.guardian_kyc === "on_file" ? "Guardian KYC on file" : m.guardian_kyc === "no_person" ? "Guardian not a person on file" : "Guardian KYC missing"}
          </span>
          <span className={`chip ${m.pan ? "text-ok" : "text-warn"}`} title="Recorded from the document with the guardian linked, shown masked">{m.pan ? `PAN ${m.pan}` : "PAN missing"}</span>
          <span className={`chip ${m.ready ? "text-ok" : "text-warn"}`} data-testid="guardian-paper-state">{m.ready ? "Ready" : "Waiting"}</span>
        </div>
      ))}
    </div>
  );
}

/** Who is recorded as a fund manager's own employee or director. */
export function ManagerStaffRows({ data }: { data: any }) {
  return (
    <div className="divide-y divide-border-soft" data-testid="manager-staff">
      {(data.people ?? []).map((p: any) => (
        <div key={`${p.person_id}-${p.manager}`} className="flex flex-wrap items-center gap-1.5 px-3 py-1.5" data-testid="manager-staff-row" title={p.document}>
          <span className="mr-auto text-sm text-ink">{p.person}</span>
          <span className="chip text-ink-muted">{p.manager}</span>
          <span className="chip text-ink-muted">{p.role_label}</span>
          <span className={`chip ${p.state === "in_force" ? "text-ok" : "text-ink-muted"}`} title={`From ${p.effective_on}`}>{p.state_label}</span>
        </div>
      ))}
    </div>
  );
}

/** The KYC review period by risk band, each with its months and whose it is. */
export function KycReviewChips({ data }: { data: any }) {
  return (
    <div className="flex flex-wrap items-center gap-1.5" data-testid="kyc-review-period">
      {(["high", "medium", "low"] as const).map((b) => {
        const v = data.bands?.[b];
        if (!v) return null;
        return (
          <span key={b} className={`chip tabular-nums ${v.source === "the firm's setting" ? "text-ok" : "text-ink-muted"}`} title={`${v.source}. ${data.default_source ?? ""}`} data-testid="kyc-review-band">
            {b} {v.months} mo
          </span>
        );
      })}
    </div>
  );
}

/** The real estate gains choice, the tax each way and which is lower. */
export function RealEstateChoice({ data }: { data: any }) {
  if (data.state === "not_assessed") {
    return <div className="px-3 py-2" data-testid="real-estate-choice"><span className="chip text-warn" title={data.message}>Not assessed</span></div>;
  }
  return (
    <div className="flex flex-wrap items-center gap-1.5 px-3 py-2" data-testid="real-estate-choice" title={data.not_modelled}>
      <span className="chip tabular-nums text-ink-muted">Gain {money(data.gain, "INR")}</span>
      <span className="chip tabular-nums text-ink-muted">12.5% {money(data.tax_without_indexation, "INR")}</span>
      {data.tax_with_indexation != null && <span className="chip tabular-nums text-ink-muted">Indexed 20% {money(data.tax_with_indexation, "INR")}</span>}
      <span className="chip tabular-nums text-ok">Tax {money(data.tax, "INR")}</span>
      <span className="chip text-warn">Estimate</span>
    </div>
  );
}

/** A tool result for the conversation: whichever of the five it is. */
export function IndiaRemDCard({ result }: { result: any }) {
  if (result.applies === false) {
    return <div className="rounded-lg border border-border bg-surface px-3 py-2"><span className="chip text-ink-muted">Not available for this firm</span></div>;
  }
  const kind = Array.isArray(result.clients) ? "ledger" : Array.isArray(result.minors) ? "papers" : Array.isArray(result.people) ? "staff"
    : result.bands ? "kyc" : result.changes ? "kyc_set" : result.debited ? "ledger" : "estate";
  const title = { ledger: "PMS unit ledger", papers: "Guardian papers", staff: "Manager staff", kyc: "KYC review period", kyc_set: "KYC review period", estate: "Real estate gains" }[kind];
  return (
    <div className="overflow-hidden rounded-lg border border-border bg-surface" data-testid="india-rem-d-card">
      <div className="flex flex-wrap items-center justify-between gap-2 border-b border-border-soft px-3 py-2">
        <div className="text-sm font-medium text-ink">{title}</div>
        {kind === "papers" && (result.waiting ?? []).length > 0 && <span className="chip text-warn">{result.waiting.length} waiting</span>}
        {kind === "ledger" && result.counts && <span className="chip text-ink-muted">{result.counts.verified} verified</span>}
      </div>
      {kind === "ledger" && <PmsLedgerRows data={result.clients ? result : { clients: [], lines: result.lines }} />}
      {kind === "papers" && <GuardianPaperRows data={result} />}
      {kind === "staff" && <ManagerStaffRows data={result} />}
      {kind === "kyc" && <div className="px-3 py-2"><KycReviewChips data={result} /></div>}
      {kind === "kyc_set" && <div className="px-3 py-2"><span className="chip text-ok">Saved {result.changes.length} change</span></div>}
      {kind === "estate" && <RealEstateChoice data={result} />}
    </div>
  );
}
