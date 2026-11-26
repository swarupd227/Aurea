"use client";

import { Card, Empty, ErrorState, SkeletonList } from "@/components/ui";
import { useApi } from "@/lib/hooks";
import { withRef } from "@/lib/refs";
import {
  RegistryBands, RegistryChip, RegistryMix, RegistryParty, TimelineChips,
} from "@/app/workspace/components/artifacts/IndiaKycRegistryCard";
import { AllClients, ViewHeader } from "./ViewHeader";
import { ViewLink, useViewNav } from "./ViewNav";

/** The KYC registry status, India (B-386): the book's mix, the re-KYC backlog by age, each on-hold client's steps, and the open
 * cases with their registry status before any timeline. Beside the conversation each action is Astra's tool behind its Confirm
 * card. */
export function KycRegistryView({ household = null }: { household?: string | null }) {
  const nav = useViewNav();
  const { data, loading, error, refetch } = useApi<any>(`/api/kyc-registry${household ? `?household_id=${household}` : ""}`, [household]);
  if (error) return <div className="view-container"><Card><ErrorState what="the KYC registry status" message={error} onRetry={refetch} /></Card></div>;
  if (loading || !data) return <div className="view-container"><SkeletonList count={4} /></div>;
  if (!data.applies) return <div className="view-container"><ViewHeader title="KYC registry" /><Empty>Not an Indian firm.</Empty></div>;

  const say = (sentence: string, kind: "person" | "entity" | "onboarding_case", id: string) => nav.say?.(withRef(sentence, kind, id));
  const button = (label: string, testId: string, onClick: () => void) => (
    <button type="button" className="btn-outline text-xs" data-testid={testId} onClick={onClick}>{label}</button>
  );
  const actions = (p: any) => {
    const id = p.person_id ?? p.entity_id ?? p.case_id;
    if (!nav.say || !data.can_record || !id) return null;
    const kind = p.person_id ? "person" : p.entity_id ? "entity" : "onboarding_case";
    const who = kind === "onboarding_case" ? `${p.name}, a party on an onboarding case` : p.name;
    return (
      <span className="flex gap-1.5">
        {data.connected && button("Look up", "kyc-registry-lookup", () => say(`Look up the KYC registry status for ${who}.`, kind, id))}
        {button("Record answer", "kyc-registry-record", () => say(`Record the KYC registry's answer for ${who}. Ask me what the answer says and where it is filed.`, kind, id))}
        {p.backlog && button("Log contact", "kyc-registry-contact", () => say(`Record a KYC contact attempt with ${who}. Ask me the channel, the outcome and the day.`, kind, id))}
        {p.steps?.length > 0 && button("Update step", "kyc-registry-step", () => say(`Update a KYC remediation step for ${who}. Read the registry status first and ask me which step and where it stands.`, kind, id))}
      </span>
    );
  };

  return (
    <div className="view-container">
      <ViewHeader
        title="KYC registry"
        meta={<>
          <span className="chip text-ink-muted">as of {data.as_of}</span>
          {!data.connected && <span className="chip text-ink-muted" title={data.not_connected}>Lookup not connected</span>}
          {data.provider?.simulated && <span className="chip text-warn" data-testid="kyc-registry-provider-simulated">simulated lookup</span>}
        </>}
        actions={household ? <AllClients href="/studio/kyc-registry" /> : undefined}
      />

      <Card className="mb-4 overflow-hidden p-0" data-testid="kyc-registry-cases-card">
        <div className="border-b border-border-soft px-3 py-2 tile-label">Open cases</div>
        {data.cases.length === 0 ? <div className="px-3 py-2"><span className="chip text-ink-muted">No open case</span></div> : (
          <div className="divide-y divide-border-soft">
            {data.cases.map((c: any) => (
              <div key={c.case_id} className="flex flex-wrap items-center gap-1.5 px-3 py-2" data-testid="kyc-registry-case">
                <ViewLink href={`/studio/onboarding/${c.case_id}`} className="mr-auto text-sm text-ink underline-offset-2 hover:underline">{c.client}</ViewLink>
                <RegistryChip registry={c} />
                <TimelineChips registry={c} />
                {nav.say && data.can_record && c.status === "not_looked_up" && (data.connected
                  ? button("Look up", "kyc-registry-case-lookup", () => say(`Look up the KYC registry status for ${c.client}'s onboarding case.`, "onboarding_case", c.case_id))
                  : button("Record answer", "kyc-registry-case-record", () => say(`Record the KYC registry's answer for ${c.client}'s onboarding case. Ask me what it says and where it is filed.`, "onboarding_case", c.case_id)))}
              </div>
            ))}
          </div>
        )}
      </Card>

      <Card className="mb-4 overflow-hidden p-0">
        <div className="border-b border-border-soft px-3 py-2 tile-label">Status of the book</div>
        {data.counts.parties === 0 ? <div className="px-3 py-2"><span className="chip text-ink-muted">No clients</span></div> : (
          <>
            <RegistryMix mix={data.mix} />
            <div className="border-t border-border-soft"><RegistryBands bands={data.bands} /></div>
          </>
        )}
      </Card>

      {[["Re-KYC backlog", data.backlog, "kyc-registry-backlog-list"],
        ["Everyone else", data.parties.filter((p: any) => !p.backlog), "kyc-registry-others"]].map(([title, rows, testId]: any) => (
        rows.length > 0 && (
          <Card key={title} className="mb-4 overflow-hidden p-0" data-testid={testId}>
            <div className="border-b border-border-soft px-3 py-2 tile-label">{title}</div>
            <div className="divide-y divide-border-soft">
              {rows.map((p: any) => (
                <RegistryParty key={p.person_id ?? p.entity_id ?? `${p.case_id}-${p.name}`} p={p.name} registry={p.registry} steps={p.steps}
                  contacts={p.contacts} backlog={p.backlog} reasonRecorded={p.reason_recorded} action={actions(p)} />
              ))}
            </div>
          </Card>
        )
      ))}
    </div>
  );
}
