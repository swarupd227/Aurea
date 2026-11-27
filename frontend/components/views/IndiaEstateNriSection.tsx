"use client";

import { Card, ErrorState, SkeletonList } from "@/components/ui";
import { useApi } from "@/lib/hooks";
import { withRef } from "@/lib/refs";
import { RegisterAssets, TrustTriggers, WillChecks } from "@/app/workspace/components/artifacts/EstateRegisterCard";
import { NriPerson } from "@/app/workspace/components/artifacts/NriPlanningCard";
import { useViewNav } from "./ViewNav";

/** B-398: one Indian household's asset-and-wishes register, will checks and NRI planning on the client file. In the pane
 * its buttons are sentences handed to the conversation, so each goes through its tool and Confirm card. */
export function IndiaEstateNriSection({ household }: { household: string }) {
  const nav = useViewNav();
  const estate = useApi<any>(`/api/india-planning/estate?household=${household}`, [household]);
  const nri = useApi<any>(`/api/india-planning/nri?household=${household}`, [household]);
  const error = estate.error || nri.error;
  if (error) return <Card className="mb-4"><ErrorState what="the asset register" message={error} onRetry={() => { estate.refetch(); nri.refetch(); }} /></Card>;
  const data = estate.data;
  if (!data || data.household_id !== household) return <SkeletonList count={1} />;
  const say = data.can_record ? nav.say : null;
  const ask = (text: string) => say?.(withRef(text, "household", household));
  const people: any[] = nri.data?.household_id === household ? nri.data.people : [];
  return (
    <>
      <Card className="mb-4 overflow-hidden p-0" data-testid="india-register">
        <div className="flex flex-wrap items-center gap-1.5 border-b border-border-soft px-4 py-2.5">
          <div className="mr-auto text-sm font-medium text-ink">Asset and wishes register</div>
          <span className={`chip tabular-nums ${data.counts.missing_nominations ? "text-warn" : "text-ok"}`}>{data.counts.missing_nominations} no nomination</span>
          <span className="chip text-ink-muted">for the lawyer · not advice</span>
          {say && <button className="btn-outline text-xs" data-testid="register-wish" onClick={() => ask(`Record a wish on ${data.household}'s asset register.`)}>Record a wish</button>}
          {say && <button className="btn-outline text-xs" data-testid="register-will-check" onClick={() => ask(`Record a check of the will against ${data.household}'s asset register.`)}>Record will check</button>}
        </div>
        <RegisterAssets view={data} />
        <div className="border-t border-border-soft"><WillChecks view={data} /></div>
        <div className="space-y-1.5 border-t border-border-soft px-4 py-2.5">
          <TrustTriggers view={data} />
          {data.wishes.length > 0 && (
            <table className="w-full text-xs" data-testid="register-wishes">
              <tbody className="divide-y divide-border-soft">
                {data.wishes.map((w: any) => (
                  <tr key={w.id} title={w.document}>
                    <td className="py-1 pr-3 align-top text-ink-muted">{w.person} · {w.kind_label}</td>
                    <td className="py-1 text-ink">{w.asset_label ? `${w.asset_label}: ` : ""}{w.words}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          )}
        </div>
      </Card>
      {people.length > 0 && (
        <Card className="mb-4 overflow-hidden p-0" data-testid="india-nri">
          <div className="flex flex-wrap items-center gap-1.5 border-b border-border-soft px-4 py-2.5">
            <div className="mr-auto text-sm font-medium text-ink">NRI planning</div>
            <span className="chip text-ink-muted">treaty relief not modelled</span>
            {say && <button className="btn-outline text-xs" data-testid="nri-certificate" onClick={() => ask(`Record a tax residency certificate for ${data.household}.`)}>Record certificate</button>}
            {say && <button className="btn-outline text-xs" data-testid="nri-move-record" onClick={() => ask(`Record a move in or out of India for ${data.household}.`)}>Record a move</button>}
          </div>
          <div className="divide-y divide-border-soft">{people.map((p) => <NriPerson key={p.person_id} p={p} />)}</div>
        </Card>
      )}
    </>
  );
}
