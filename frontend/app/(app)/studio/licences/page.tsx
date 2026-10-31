"use client";
import PageHeader from "@/components/PageHeader";
import { Card, Empty, Spinner } from "@/components/ui";
import { ConsentChips, ConsentPanel } from "@/components/ConsentPanel";
import { useApi } from "@/lib/hooks";
import { titleCase } from "@/lib/format";
import { LicenceChips } from "@/app/workspace/components/artifacts/LicenceCard";

export default function Licences() {
  const { data, loading, refetch } = useApi<any>("/api/licences");
  if (loading || !data) return <Spinner />;
  if (!data.applies) {
    return (
      <div>
        <PageHeader title="Licences" sub="What a firm's licence lets an agent do." />
        <Empty>No licences on file</Empty>
      </div>
    );
  }
  const waiting: any[] = data.awaiting_consent;
  const mandates: any[] = data.mandates;
  const consenting = mandates.filter((m) => m.per_trade_consent).length;

  return (
    <div>
      <PageHeader title="Licences" sub="What each licence lets an agent do, client by client." />

      <div className="mb-4 grid grid-cols-2 gap-3 lg:grid-cols-4" data-testid="licence-summary">
        {[
          ["Licences held", data.licences.length, "text-ink"],
          ["Agent ceiling", (data.ceiling ?? "").replace("tier_", "Tier "), "text-ink"],
          ["Client consents", `${consenting} of ${mandates.length}`, "text-ink"],
          ["Waiting on clients", waiting.length, waiting.length ? "text-warn" : "text-ink-muted"],
        ].map(([label, v, tone]: any) => (
          <Card key={label}>
            <div className="tile-label">{label}</div>
            <div className={`mt-1 text-2xl font-semibold tabular-nums ${tone}`}>{v}</div>
          </Card>
        ))}
      </div>

      <Card className="mb-4">
        <div className="tile-label mb-2">Held by this firm</div>
        <div className="divide-y divide-border-soft" data-testid="licence-matrix">
          {data.licences.map((l: any) => (
            <div key={l.key} className="flex flex-wrap items-center gap-1.5 py-2">
              <span className="text-sm font-medium text-ink mr-auto">{l.name}</span>
              <LicenceChips l={l} />
              <span className="chip text-ink-muted">{l.max_tier.replace("tier_", "Tier ")} at most</span>
              <span className="chip text-ink-muted">{l.citation}</span>
            </div>
          ))}
        </div>
      </Card>

      <Card className="mb-4">
        <div className="tile-label mb-2">Waiting on clients</div>
        {waiting.length === 0
          ? <div className="font-mono text-xs text-ink-muted">nothing waiting</div>
          : (
            <div className="-mx-4 divide-y divide-border" data-testid="licence-waiting">
              {waiting.map((c) => (
                <div key={c.id}>
                  <div className="flex flex-wrap items-center gap-1.5 px-4 py-2">
                    <span className="text-sm text-ink mr-auto">{c.subject}</span>
                    <span className="chip text-ink-muted">Approved by {c.requested_by}</span>
                  </div>
                  <ConsentPanel recId={c.recommendation_id} consent={c} demonstration={data.demonstration} onDone={() => refetch()} />
                </div>
              ))}
            </div>
          )}
      </Card>

      {data.recent_consents.length > 0 && (
        <Card className="mb-4">
          <div className="tile-label mb-2">Answers on record</div>
          <div className="divide-y divide-border-soft" data-testid="licence-answers">
            {data.recent_consents.map((c: any) => (
              <div key={c.id} className="flex flex-wrap items-center gap-1.5 py-2">
                <span className="text-sm text-ink mr-auto">{c.subject}</span>
                <ConsentChips consent={c} />
                <span className="chip text-ink-muted">{c.recorded_by}</span>
              </div>
            ))}
          </div>
        </Card>
      )}

      <Card>
        <div className="tile-label mb-2">Mandates by track</div>
        <div className="overflow-x-auto">
          <table className="w-full text-sm" data-testid="licence-mandates">
            <thead>
              <tr className="text-left text-[10px] uppercase tracking-wide text-ink-muted">
                <th className="py-1.5 pr-3 font-medium">Mandate</th>
                <th className="py-1.5 pr-3 font-medium">Household</th>
                <th className="py-1.5 pr-3 font-medium">Type</th>
                <th className="py-1.5 pr-3 font-medium">Track</th>
              </tr>
            </thead>
            <tbody className="divide-y divide-border-soft">
              {mandates.map((m) => (
                <tr key={m.mandate_id}>
                  <td className="py-1.5 pr-3 text-ink">{m.mandate}</td>
                  <td className="py-1.5 pr-3 text-ink-muted">{m.household ?? "—"}</td>
                  <td className="py-1.5 pr-3 text-ink-muted">{titleCase(m.mandate_type)}</td>
                  <td className="py-1.5 pr-3 text-ink-soft">{m.licence_name ?? "—"}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </Card>
    </div>
  );
}
