"use client";

import { Card, Empty, ErrorState, SkeletonList } from "@/components/ui";
import { useApi } from "@/lib/hooks";
import { withRef } from "@/lib/refs";
import { reviewDay } from "@/app/workspace/components/artifacts/ReviewsCard";
import {
  AuditMetricChips, CapacityChips, CheckChips, HeldHouseholdChips, ResultChip, RetentionChip,
} from "@/app/workspace/components/artifacts/AdvicePracticeCard";
import { useViewNav } from "./ViewNav";

/** B-399: the Investment Adviser's operating model on the advice file: the registration line, retention, who is held
 * before advice and compliance's sample audit. In the pane compliance's buttons are sentences handed to the
 * conversation, so each goes through its tool and Confirm card. */
export function AdviceAuditSection() {
  const nav = useViewNav();
  const { data, loading, error, refetch } = useApi<any>("/api/advice-file/audit");
  if (error) return <Card className="mb-4"><ErrorState what="the advice audit" message={error} onRetry={refetch} /></Card>;
  if (loading || !data) return <SkeletonList count={1} />;
  if (!data.applies) return null;
  const say = data.can_record ? nav.say : null;
  return (
    <div className="mb-4 space-y-3" data-testid="advice-audit">
      <Card>
        <div className="mb-2 flex flex-wrap items-center gap-1.5">
          <span className="tile-label mr-auto">Investment Adviser operating model</span>
          {say && (
            <button className="btn-outline text-xs" data-testid="advice-audit-draw" onClick={() => say("Draw a sample of advice files for audit.")}>
              Draw a sample
            </button>
          )}
        </div>
        <div className="space-y-2">
          <CapacityChips c={data.capacity} />
          <div className="flex flex-wrap gap-1.5"><RetentionChip retention={data.retention} /></div>
          <HeldHouseholdChips held={data.pre_advice?.held} />
          {data.audits.length > 0 && <AuditMetricChips m={data.metrics} items={data.items} />}
        </div>
      </Card>
      {data.audits.length === 0 ? <Empty>No sample drawn.</Empty> : data.audits.slice(0, 3).map((a: any) => (
        <Card key={a.audit_id} className="overflow-hidden p-0">
          <div className="flex flex-wrap items-center gap-1.5 border-b border-border-soft px-4 py-2.5">
            <span className="mr-auto text-sm font-medium text-ink">Sample {a.seq}</span>
            <span className="chip text-ink-muted">drawn {reviewDay(a.drawn_on)} · {a.drawn_by}</span>
            <span className="chip tabular-nums text-ink-muted">{a.drawn} of {a.population}</span>
            <span className="chip text-ink-muted">made {reviewDay(a.since)} – {reviewDay(a.until)}</span>
          </div>
          <div className="divide-y divide-border-soft">
            {a.files.map((f: any) => (
              <div key={f.recommendation_id} className="space-y-1.5 px-4 py-2.5" data-testid="audit-file">
                <div className="flex flex-wrap items-center gap-1.5">
                  <span className="mr-auto text-sm text-ink">{f.household}</span>
                  <span className="chip text-ink-muted">{f.category.replace("_", " ")} · {reviewDay(f.recommended_on)}</span>
                  <ResultChip result={f.result} />
                  {say && !f.result && (
                    <button className="btn-outline text-xs" data-testid="advice-audit-record"
                      onClick={() => say(withRef(`Record my audit result for ${f.household}'s advice file in sample ${a.seq}.`, "advice", f.recommendation_id))}>
                      Record result
                    </button>
                  )}
                </div>
                <CheckChips checks={f.checks} items={data.items} result={f.result} />
              </div>
            ))}
          </div>
        </Card>
      ))}
    </div>
  );
}
