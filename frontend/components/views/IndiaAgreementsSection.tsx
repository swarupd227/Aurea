"use client";

import { Card, ErrorState, SkeletonList } from "@/components/ui";
import { useApi } from "@/lib/hooks";
import { withRef } from "@/lib/refs";
import { FirmAgreements, HouseholdAgreements, TrustCases } from "@/app/workspace/components/artifacts/AgreementFileCard";
import { AcceptanceClients, AcceptanceLines, AcceptanceSchemes } from "@/app/workspace/components/artifacts/FundAcceptanceCard";
import { ViewLink, useViewNav } from "./ViewNav";

/** B-391: the agreement file on the client file, for one household or the firm. Hidden outside India. In the pane its
 * buttons are sentences handed to the conversation, so each goes through its tool and Confirm card. */
export function IndiaAgreementsSection({ household = null }: { household?: string | null }) {
  const nav = useViewNav();
  const { data, error, refetch } = useApi<any>(household ? `/api/india-agreements?household=${household}` : "/api/india-agreements", [household]);
  // A role that does not read it sees nothing here.
  if (error) return /role/i.test(error) ? null : <Card className="mb-4"><ErrorState what="the agreement file" message={error} onRetry={refetch} /></Card>;
  if (!data) return <SkeletonList count={1} />;
  if (!data.applies || (household ? data.household_id !== household : data.scope !== "firm")) return null;
  const say = data.can_record ? nav.say : null;
  const ask = (text: string) => say?.(household ? withRef(text, "household", household) : text);
  const trustAsk = (c: any) => say?.(`Record the beneficial owners of ${c.case} as parties (case ${c.case_id}).`);
  return (
    <Card className="mb-4 overflow-hidden p-0" data-testid="india-agreements">
      <div className="flex flex-wrap items-center gap-1.5 border-b border-border-soft px-4 py-2.5">
        <div className="mr-auto text-sm font-medium text-ink">Agreements and disclosures</div>
        <span className="chip text-ink-muted">holds no order · rules assumed</span>
        {say && household && (
          <>
            <button className="btn-outline text-xs" data-testid="agreement-record-item" onClick={() => ask(`Record a PMS, AIF or demat paper for ${data.household}.`)}>Record a paper</button>
            <button className="btn-outline text-xs" data-testid="agreement-record-disclosure" onClick={() => ask(`Record a disclosure given to ${data.household}.`)}>Record disclosure</button>
            <button className="btn-outline text-xs" data-testid="agreement-record-drawdown" onClick={() => ask(`Record an AIF drawdown or payment for ${data.household}.`)}>Record drawdown</button>
            <button className="btn-outline text-xs" data-testid="agreement-record-estamp" onClick={() => ask(`Record an e-stamp certificate for ${data.household}.`)}>Record e-stamp</button>
          </>
        )}
      </div>
      {household ? <HouseholdAgreements view={data} /> : (
        <>
          <FirmAgreements view={data} link={(h) => <ViewLink href={`/studio/client-file?household=${h.household_id}`} className="hover:underline">{h.household}</ViewLink>} />
          <TrustCases cases={data.trusts} onRecord={say ? trustAsk : undefined} />
        </>
      )}
    </Card>
  );
}

/** B-391: the fund-house acceptance list on the product shelf. Hidden outside India. */
export function IndiaAcceptanceSection() {
  const nav = useViewNav();
  const { data, error, refetch } = useApi<any>("/api/india-agreements/acceptance", []);
  if (error) return /role/i.test(error) ? null : <Card className="mb-4"><ErrorState what="the acceptance list" message={error} onRetry={refetch} /></Card>;
  if (!data?.applies) return null;
  const say = data.can_record ? nav.say : null;
  return (
    <Card className="mb-4 overflow-hidden p-0" data-testid="india-acceptance">
      <div className="flex flex-wrap items-center gap-1.5 border-b border-border-soft px-4 py-2.5">
        <div className="mr-auto text-sm font-medium text-ink">Fund-house acceptance · US and Canada residents</div>
        <span className="chip text-ink-muted">stops purchases only · nothing on file bars nothing</span>
        {say && <button className="btn-outline text-xs" data-testid="acceptance-record" onClick={() => say("Record a fund-house acceptance line for US or Canada residents.")}>Record a line</button>}
      </div>
      <AcceptanceClients view={data} />
      <div className="border-t border-border-soft"><AcceptanceLines view={data} /></div>
      <div className="border-t border-border-soft"><AcceptanceSchemes view={data} /></div>
    </Card>
  );
}
