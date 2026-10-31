"use client";

import { useEffect, useState } from "react";
import { RefreshCw, Undo2 } from "lucide-react";
import { Breadcrumb, Card, ErrorState, Spinner, StatusBadge } from "@/components/ui";
import { AllocationDonut } from "@/components/Charts";
import { useAgentRunner } from "@/components/AgentConsole";
import RecommendationCard from "@/components/RecommendationCard";
import { Markdown } from "@/app/workspace/components/Markdown";
import { useApi } from "@/lib/hooks";
import { api } from "@/lib/api";
import { withRef } from "@/lib/refs";
import { ViewLink, useViewNav } from "./ViewNav";
import { ViewHeader, chip } from "./ViewHeader";
import { ReportState } from "./ReportsView";

/** One client report, as the client would read it, and what can happen to it next. A report reaches
 * a client only by a person approving its proposal, shown below it. In the work pane, redrafting and
 * withdrawing are the conversation's tools, behind their Confirm card; on a page they act directly. */
export function ReportView({ id }: { id: string }) {
  const nav = useViewNav();
  const runner = useAgentRunner();
  const { data: r, loading, error, refetch } = useApi<any>(`/api/engage/reports/${id}`, [id]);
  const [rec, setRec] = useState<any>(null);
  const [confirming, setConfirming] = useState(false);
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState<string | null>(null);

  useEffect(() => {
    if (!r?.awaiting_decision) return setRec(null);
    api(`/api/studio/recommendations/${r.recommendation_id}`).then(setRec).catch(() => setRec(null));
  }, [r?.awaiting_decision, r?.recommendation_id]);

  if (loading) return <Spinner />;
  if (error || !r) return <Card><ErrorState what="this report" message={error ?? "Not found"} onRetry={refetch} /></Card>;

  function redraft() {
    if (nav.say) return nav.say(withRef(`Draft a fresh client report for ${r.household_name}.`, "household", r.household_id));
    runner.run({ agentKey: "research_reporting", subjectType: "household", subjectId: r.household_id, label: "Research note" });
  }

  async function withdraw() {
    if (nav.say) return nav.say(withRef(`Withdraw the report "${r.title}" from ${r.household_name}.`, "report", id));
    if (!confirming) return setConfirming(true);
    setBusy(true);
    setErr(null);
    try {
      await api(`/api/studio/recommendations/${r.recommendation_id}/rollback`, { body: {} });
      setConfirming(false);
      await refetch();
    } catch (e: any) {
      setErr(e.message);
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="view-container">
      {!nav.inPane && <Breadcrumb items={[{ label: "Reports", href: "/studio/reports" }, { label: r.title }]} />}
      <ViewHeader
        title={r.title}
        meta={
          <>
            <ViewLink href={`/studio/clients/${r.household_id}`} className={`${chip} hover:text-ink`}>{r.household_name}</ViewLink>
            {r.period && <span className={`${chip} tabular-nums`}>{r.period}</span>}
            <StatusBadge status={r.status} />
            <ReportState report={r} />
            {r.published_at && <span className={`${chip} tabular-nums`}>Published {new Date(r.published_at).toLocaleDateString()}</span>}
          </>
        }
        actions={
          <>
            {r.can_withdraw && (
              <button className="btn-outline text-xs" data-testid="report-withdraw" disabled={busy} onClick={withdraw}>
                <Undo2 size={13} /> {confirming ? "Confirm withdraw" : "Withdraw"}
              </button>
            )}
            {r.status === "draft" && (
              <button className="btn-outline text-xs" data-testid="report-redraft" onClick={redraft}>
                <RefreshCw size={13} /> Redraft
              </button>
            )}
          </>
        }
      />
      {err && <div className="mb-3 text-xs text-crit">{err}</div>}

      <Card>
        {r.data?.allocation && (
          <div className="mb-5 border-b border-border-soft pb-5" data-testid="report-allocation">
            <AllocationDonut data={r.data.allocation} size={160} />
          </div>
        )}
        <div className="space-y-5" data-testid="report-sections">
          {r.sections?.map((s: any, i: number) => (
            <section key={i}>
              <h3 className="mb-1 font-semibold text-ink">{s.heading}</h3>
              <Markdown text={s.body} className="text-sm text-ink-soft" />
            </section>
          ))}
        </div>
      </Card>

      {rec?.status === "proposed" && (
        <div className="mt-5">
          <RecommendationCard rec={rec} inReport onDecided={(u) => { setRec(u); refetch(); nav.changed(); }} />
        </div>
      )}
    </div>
  );
}
