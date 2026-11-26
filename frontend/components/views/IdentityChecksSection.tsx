"use client";

import { Card } from "@/components/ui";
import { useApi } from "@/lib/hooks";
import { withRef } from "@/lib/refs";
import { CheckPaths, CheckRow } from "@/app/workspace/components/artifacts/IdentityChecksCard";
import { useViewNav } from "./ViewNav";

/** The words a button hands the conversation for one check on one party. */
function actionsFor(nav: any, data: any, r: any) {
  const id = r.person_id ?? r.entity_id ?? r.case_id;
  if (!nav.say || !id) return null;
  const kind = r.person_id ? "person" : r.entity_id ? "entity" : "onboarding_case";
  const what = `the ${r.kind} check for ${r.party}${r.last4 ? ` (account ending ${r.last4})` : ""}`;
  const say = (sentence: string) => nav.say?.(withRef(sentence, kind, id));
  return (
    <span className="flex gap-1.5">
      {r.can_retry && data.connected && data.can_run !== false && (
        <button type="button" className="btn-outline text-xs" data-testid="identity-check-retry" onClick={() => say(`Retry ${what}.`)}>Retry</button>
      )}
      {!r.passed && data.can_decide !== false && (
        <button type="button" className="btn-outline text-xs" data-testid="identity-check-by-hand"
          onClick={() => say(`Record ${what} as checked by hand. Ask me the result, the document and the name it shows.`)}>Checked by hand</button>
      )}
    </span>
  );
}

/** Identity and bank checks on the KYC registry page: the human queue first, then retries, with what happens on each failure. */
export function IdentityChecksSection({ household = null }: { household?: string | null }) {
  const nav = useViewNav();
  const { data } = useApi<any>(`/api/kyc-registry/checks${household ? `?household_id=${household}` : ""}`, [household]);
  if (!data?.applies) return null;
  const open = data.checks.filter((r: any) => !r.passed);
  return (
    <Card className="mb-4 overflow-hidden p-0" data-testid="identity-checks-section">
      <div className="flex flex-wrap items-center gap-1.5 border-b border-border-soft px-3 py-2">
        <span className="mr-auto tile-label">Identity and bank checks</span>
        <span className={`chip ${data.counts.queued ? "text-crit" : "text-ink-muted"}`}>{data.counts.queued} in queue</span>
        {data.counts.retry > 0 && <span className="chip text-warn">{data.counts.retry} to retry</span>}
        <span className="chip text-ink-muted">{data.counts.passed} passed</span>
        {!data.connected && <span className="chip text-ink-muted" title={data.not_connected}>No service connected</span>}
        {data.provider?.simulated && <span className="chip text-warn">simulated service</span>}
        <span className="chip text-ink-muted" title={data.thresholds}>Name match tolerance assumed</span>
      </div>
      {open.length === 0 ? <div className="px-3 py-2"><span className="chip text-ink-muted">Nothing waiting</span></div> : (
        <div className="divide-y divide-border-soft" data-testid="identity-checks-queue">
          {open.map((r: any) => <CheckRow key={`${r.person_id ?? r.entity_id ?? r.case_id}-${r.party}-${r.kind}-${r.last4 ?? ""}`} r={r} action={actionsFor(nav, data, r)} />)}
        </div>
      )}
      <div className="border-t border-border-soft"><CheckPaths defined={data.defined} /></div>
    </Card>
  );
}

/** One case's checks, with a way to start one beside the conversation. */
export function CaseIdentityChecks({ data, caseId, name }: { data: any; caseId: string; name: string }) {
  const nav = useViewNav();
  const say = (sentence: string) => nav.say?.(withRef(sentence, "onboarding_case", caseId));
  return (
    <div className="card mb-4 overflow-hidden p-0" data-testid="onboarding-case-checks">
      <div className="flex flex-wrap items-center gap-1.5 border-b border-border-soft px-3 py-2">
        <span className="mr-auto text-sm font-semibold text-ink">Identity and bank checks</span>
        {data.checks.length === 0 && <span className="chip text-warn">None run</span>}
        {data.counts.queued > 0 && <span className="chip text-crit">{data.counts.queued} in queue</span>}
        {data.provider?.simulated && <span className="chip text-warn">simulated service</span>}
        {nav.say && [["pan", "PAN"], ["bank", "Bank"], ["vipv", "Video"]].map(([kind, label]) => (
          <button key={kind} type="button" className="btn-outline text-xs" data-testid={`onboarding-case-check-${kind}`}
            onClick={() => say(data.connected
              ? `Run the ${kind} check on ${name}'s onboarding case. Ask me for anything it needs.`
              : `Record the ${kind} check on ${name}'s onboarding case as checked by hand. Ask me the result, the document and the name it shows.`)}>
            {label}
          </button>
        ))}
      </div>
      {data.checks.length === 0
        ? <CheckPaths defined={data.defined} />
        : <div className="divide-y divide-border-soft">{data.checks.map((r: any) => <CheckRow key={`${r.party}-${r.kind}-${r.last4 ?? ""}`} r={r} action={actionsFor(nav, data, r)} />)}</div>}
    </div>
  );
}
