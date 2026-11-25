"use client";

import { money } from "@/lib/format";

// India batch C: a mutual fund order's rail and NAV day, advice kept apart from distribution with the direct-against-regular comparison, each
// adviser's NISM certificate, and advertisement pre-approval. Figures and chips only: the agent's message carries the sentences, and every
// source, caveat and reason is a chip's title.

const NAV_TONE: Record<string, string> = { matches: "text-ok", differs: "text-crit", not_assessed: "text-warn" };
const NAV_TEXT: Record<string, string> = { matches: "NAV day matches", differs: "NAV day differs", not_assessed: "Not assessed" };
const CERT_TONE: Record<string, string> = { valid: "text-ok", due_soon: "text-warn", expired: "text-crit", not_on_file: "text-warn", not_required: "text-ink-muted" };
const AD_TONE: Record<string, string> = { approved: "text-ok", rejected: "text-crit", pending: "text-warn" };

/** Each recorded mutual fund order: the order date and the NAV date apart, the rail and the registrar, and what the cut-off rule gives. */
export function MfOrderRows({ data }: { data: any }) {
  return (
    <div className="divide-y divide-border-soft" data-testid="mf-orders">
      {(data.orders ?? []).map((o: any) => (
        <div key={o.order_id} className="flex flex-wrap items-center gap-1.5 px-3 py-1.5" data-testid="mf-order" title={o.likely_cause ?? (o.needs ? `Needs ${o.needs}` : (o.basis ?? []).join(" ")) }>
          <span className="mr-auto text-sm text-ink">{o.symbol} · {o.side}</span>
          <span className="chip text-ink-muted" title="The rail the order travelled by">{o.rail_label}</span>
          <span className="chip text-ink-muted" title={o.folio ? `Folio ${o.folio}` : "Folio not recorded"}>{o.rta_label}</span>
          <span className="chip tabular-nums text-ink-muted" title={`Taken at ${o.order_time}`}>Order {o.order_on}</span>
          <span className="chip tabular-nums text-ink-muted" title="As the registrar reports it">NAV {o.nav_date}</span>
          {o.expected_nav_date && o.state === "differs" && <span className="chip tabular-nums text-ink-muted" title="The day the cut-off rule gives">Rule {o.expected_nav_date}</span>}
          {o.amount != null && <span className="chip tabular-nums text-ink-muted">{money(o.amount, "INR")}</span>}
          <span className={`chip ${NAV_TONE[o.state]}`} data-testid="mf-order-state">{NAV_TEXT[o.state]}</span>
        </div>
      ))}
      {(data.folios ?? []).map((f: any) => (
        <div key={`${f.account}-${f.folio}`} className="flex flex-wrap items-center gap-1.5 px-3 py-1" data-testid="mf-folio">
          <span className="mr-auto min-w-0 flex-1 text-xs text-ink-soft">{f.account} · {f.amc ?? "Fund house"}</span>
          <span className="chip text-ink-muted">Folio {f.folio}</span>
          <span className="chip text-ink-muted" title="The registrar that keeps the folio">{f.rta}</span>
        </div>
      ))}
      {data.unrecorded > 0 && <div className="px-3 py-1.5"><span className="chip text-warn" title="Mutual fund orders on the blotter with no rail on file">{data.unrecorded} without a rail recorded</span></div>}
    </div>
  );
}

/** The firm's group, the flags for compliance, and each holding's direct against regular cost. */
export function SeparationRows({ data }: { data: any }) {
  const check = data.check;
  const comparison = data.comparison;
  return (
    <div className="divide-y divide-border-soft" data-testid="separation">
      {(data.entities ?? []).map((e: any) => (
        <div key={e.name} className="flex flex-wrap items-center gap-1.5 px-3 py-1.5" data-testid="group-entity" title={e.document}>
          <span className="mr-auto text-sm text-ink">{e.name}</span>
          <span className="chip text-ink-muted">{e.relationship_label}</span>
          <span className={`chip ${e.holds_arn ? "text-warn" : "text-ink-muted"}`} title={e.arn ?? undefined}>{e.holds_arn ? "Holds an ARN" : "No ARN"}</span>
        </div>
      ))}
      {check && check.state === "not_assessed" && <div className="px-3 py-1.5"><span className="chip text-warn" title={check.said}>Not assessed: no group entity on file</span></div>}
      {(check?.flags ?? []).map((f: any) => (
        <div key={`${f.household_id}-${f.entity}`} className="flex flex-wrap items-center gap-1.5 px-3 py-1.5" data-testid="separation-flag" title={f.said}>
          <span className="mr-auto text-sm text-ink">{f.household} · {f.entity}</span>
          <span className="chip tabular-nums text-ink-muted" title="The statement compliance recorded">Statement {f.statement_on}</span>
          {f.amount != null && <span className="chip tabular-nums text-ink-muted">{money(f.amount, "INR")}</span>}
          <span className="chip text-warn">Flag for compliance</span>
        </div>
      ))}
      {(check?.facts_needed ?? []).map((f: any) => (
        <div key={`${f.household_id}-${f.entity}-n`} className="flex flex-wrap items-center gap-1.5 px-3 py-1.5" data-testid="separation-needs" title={f.needs}>
          <span className="mr-auto text-sm text-ink-soft">{f.household} · {f.entity}</span>
          <span className="chip text-warn">Needs a fact first</span>
        </div>
      ))}
      {(comparison?.holdings ?? []).map((h: any) => (
        <div key={`${h.account}-${h.symbol}`} className="flex flex-wrap items-center gap-1.5 px-3 py-1.5" data-testid="plan-comparison" title={h.needs ?? comparison.said}>
          <span className="mr-auto text-sm text-ink">{h.scheme} · {h.plan}</span>
          {h.direct && <span className="chip tabular-nums text-ink-muted" title={`Direct, ${h.direct.as_of}`}>Direct {h.direct.ter}%</span>}
          {h.regular && <span className="chip tabular-nums text-ink-muted" title={`Regular, ${h.regular.as_of}`}>Regular {h.regular.ter}%</span>}
          {h.state === "assessed" ? (
            <>
              <span className="chip tabular-nums text-ink-muted">{h.gap_points} points</span>
              {h.yearly_difference != null ? <span className="chip tabular-nums text-warn" title="On the holding's value at its latest real price">{money(h.yearly_difference, h.currency)} a year</span>
                : <span className="chip text-ink-muted" title={h.value_note}>No real price</span>}
            </>
          ) : <span className="chip text-warn">Not assessed</span>}
        </div>
      ))}
    </div>
  );
}

/** Each adviser's NISM certificates and whether each stands today. */
export function CertificationRows({ data }: { data: any }) {
  return (
    <div className="divide-y divide-border-soft" data-testid="certifications">
      {(data.people ?? []).map((p: any) => (
        <div key={p.user_id} className="flex flex-wrap items-center gap-1.5 px-3 py-1.5" data-testid="certification-person">
          <span className="mr-auto text-sm text-ink">{p.name}</span>
          {(p.certificates ?? []).map((c: any) => (
            <span key={c.kind} className={`chip tabular-nums ${CERT_TONE[c.state]}`} data-testid="certificate-state"
                  title={c.expires_on ? `${c.label} ${c.number}, expires ${c.expires_on}` : `${c.label}: nothing recorded`}>
              {c.label.replace("NISM Series ", "")} · {c.state_label}
            </span>
          ))}
          {(p.certificates ?? []).length === 0 && <span className="chip text-ink-muted">None required</span>}
        </div>
      ))}
    </div>
  );
}

/** Marketing copy submitted for approval, and what compliance decided. */
export function AdApprovalRows({ data }: { data: any }) {
  return (
    <div className="divide-y divide-border-soft" data-testid="ad-approvals">
      {(data.submissions ?? []).map((a: any) => (
        <div key={a.id} className="flex flex-wrap items-center gap-1.5 px-3 py-1.5" data-testid="ad-approval" title={a.reason ?? undefined}>
          <span className="mr-auto text-sm text-ink">{a.title}</span>
          <span className="chip text-ink-muted">{a.channel_label}</span>
          {a.blocks > 0 && <span className="chip text-crit" title="Blocking findings of the advertising check">{a.blocks} blocking</span>}
          {a.flags > 0 && <span className="chip text-warn" title="Flagged findings of the advertising check">{a.flags} flagged</span>}
          <span className="chip tabular-nums text-ink-muted" title="The first twelve characters of the SHA-256 of the text as submitted">{a.text_hash}</span>
          {a.approval_id && <span className="chip tabular-nums text-ink-muted" title={a.approval_id}>Approval {a.approval_id.slice(0, 8)}</span>}
          <span className={`chip ${AD_TONE[a.state]}`} data-testid="ad-approval-state">{a.state_label}</span>
        </div>
      ))}
    </div>
  );
}

/** A tool result for the conversation: whichever of the four it is. */
export function IndiaBatchCCard({ result }: { result: any }) {
  if (result.applies === false) {
    return <div className="rounded-lg border border-border bg-surface px-3 py-2"><span className="chip text-ink-muted">Not an Indian firm</span></div>;
  }
  if (typeof result.state === "string" && !Array.isArray(result.orders) && !Array.isArray(result.submissions)) {
    return (
      <div className="rounded-lg border border-border bg-surface px-3 py-2" data-testid="india-batch-c-card">
        <span className={`chip ${result.state === "approved" ? "text-ok" : "text-crit"}`} title={result.said}>Approval: {String(result.state).replace("_", " ")}</span>
      </div>
    );
  }
  const kind = Array.isArray(result.orders) ? "orders" : Array.isArray(result.entities) ? "separation" : Array.isArray(result.submissions) ? "ads" : "certs";
  const title = { orders: "Mutual fund order rails", separation: "Advice and distribution", ads: "Advertisement approvals", certs: "Adviser certifications" }[kind];
  return (
    <div className="overflow-hidden rounded-lg border border-border bg-surface" data-testid="india-batch-c-card">
      <div className="flex flex-wrap items-center justify-between gap-2 border-b border-border-soft px-3 py-2">
        <div className="text-sm font-medium text-ink">{title}</div>
        {kind === "orders" && result.breaks > 0 && <span className="chip text-crit">{result.breaks} NAV day differs</span>}
        {kind === "ads" && result.pending > 0 && <span className="chip text-warn">{result.pending} waiting</span>}
      </div>
      {kind === "orders" && <MfOrderRows data={result} />}
      {kind === "separation" && <SeparationRows data={result} />}
      {kind === "ads" && <AdApprovalRows data={result} />}
      {kind === "certs" && <CertificationRows data={result} />}
    </div>
  );
}
