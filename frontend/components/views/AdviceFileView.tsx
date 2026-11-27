"use client";

import { Card, Empty, ErrorState, SkeletonList } from "@/components/ui";
import { useApi } from "@/lib/hooks";
import { withRef } from "@/lib/refs";
import { reviewDay } from "@/app/workspace/components/artifacts/ReviewsCard";
import { ADVICE_CATEGORY, AdviceStatusChip, ImplementationChips, adviceRate } from "@/app/workspace/components/artifacts/AdviceFileCard";
import { AdviceHoldChips, HeldHouseholdChips, RetentionChip, RiskBasisChips } from "@/app/workspace/components/artifacts/AdvicePracticeCard";
import { AdviceAuditSection } from "./AdviceAuditSection";
import { ViewLink, useViewNav } from "./ViewNav";
import { AllClients, ViewHeader } from "./ViewHeader";

/** Where a recommendation may go next, as the server's rule has it (aurea_core/advice_file.MOVES). */
const MOVES: Record<string, string[]> = { open: ["accepted", "declined", "implemented"], accepted: ["declined", "implemented"], declined: ["accepted"], implemented: [] };

function Tile({ label, value, tone = "text-ink" }: { label: string; value: string | number; tone?: string }) {
  return <Card><div className="tile-label">{label}</div><div className={`font-mono text-2xl tabular-nums ${tone}`}>{value}</div></Card>;
}

/** The recommendations advisers made to clients and what became of each, with the implementation rate. Everything on
 * it is recorded through the conversation, from the adviser's words and the evidence: in the pane its buttons are
 * sentences handed to the conversation, so each goes through its tool and Confirm card. A client never sees it. */
export function AdviceFileView({ household = null }: { household?: string | null }) {
  const nav = useViewNav();
  const { data, loading, error, refetch } = useApi<any>(household ? `/api/advice-file?household=${household}` : "/api/advice-file", [household]);
  if (error) return <div className="view-container"><Card><ErrorState what="the advice file" message={error} onRetry={refetch} /></Card></div>;
  if (loading || !data || (data.household_id ?? null) !== household) return <div className="view-container"><SkeletonList count={3} /></div>;
  const canAsk = !!nav.say && !!data.can_record;
  const o = data.implementation.overall;

  const basis = (
    <Card className="mb-4">
      <div className="flex flex-wrap gap-1.5" data-testid="advice-basis">
        <span className="chip text-ink-muted">rate: implemented ÷ made</span>
        <span className="chip text-ink-muted">{data.implementation.since ? `made ${reviewDay(data.implementation.since)} – ${reviewDay(data.implementation.until)}` : "made at any time"}</span>
        <span className="chip text-ink-muted">added, never edited</span>
        <span className="chip text-ink-muted">not on file does not mean not advised</span>
        <span className="chip text-ink-muted">staff only</span>
        <span className="chip text-ink-muted" title="The firm's assumption set today">assumptions {data.assumption_version}</span>
        <RetentionChip retention={data.retention} />
        <AdviceHoldChips gate={data.pre_advice} />
      </div>
      {(data.held ?? []).length > 0 && <div className="mt-1.5"><HeldHouseholdChips held={data.held} /></div>}
    </Card>
  );

  if (!household) {
    const s = data.summary;
    return (
      <div className="view-container">
        <ViewHeader title="Advice file" />
        <div className="mb-4 grid grid-cols-2 gap-3 cq-md:grid-cols-4" data-testid="advice-summary">
          <Tile label="Made" value={s.made} />
          <Tile label="Implemented" value={adviceRate(o.rate)} tone={o.rate === null ? "text-ink-muted" : "text-ink"} />
          <Tile label="Overdue" value={s.overdue} tone={s.overdue ? "text-crit" : "text-ink-muted"} />
          <Tile label="Revisit due" value={s.revisit_due} tone={s.revisit_due ? "text-warn" : "text-ink-muted"} />
        </div>
        {basis}
        <Card className="mb-4"><ImplementationChips implementation={data.implementation} /></Card>
        {data.retention && <AdviceAuditSection />}
        {data.households.length === 0 ? <Empty>No recommendations on file in this period.</Empty> : (
          <Card className="mb-4 overflow-hidden p-0">
            <div className="overflow-x-auto">
              <table className="w-full text-sm" data-testid="advice-households">
                <thead><tr className="text-left text-[10px] uppercase tracking-wide text-ink-muted">
                  <th className="px-4 py-2 font-medium">Client</th>
                  {["Made", "Implemented", "Accepted", "Declined", "Open", "Overdue"].map((h) => <th key={h} className="px-4 py-2 text-right font-medium">{h}</th>)}
                </tr></thead>
                <tbody className="divide-y divide-border-soft">
                  {data.households.map((h: any) => (
                    <tr key={h.household_id}>
                      <td className="px-4 py-2"><ViewLink href={`/studio/advice-file?household=${h.household_id}`} className="text-ink hover:underline">{h.household}</ViewLink></td>
                      {[h.made, h.implemented, h.accepted, h.declined, h.open].map((v: number, i: number) => <td key={i} className="px-4 py-2 text-right font-mono text-xs tabular-nums text-ink-muted">{v}</td>)}
                      <td className={`px-4 py-2 text-right font-mono text-xs tabular-nums ${h.overdue ? "text-crit" : "text-ink-muted"}`}>{h.overdue}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          </Card>
        )}
        <Rows rows={data.recommendations} showClient canAsk={canAsk} say={nav.say} />
      </div>
    );
  }

  return (
    <div className="view-container">
      <ViewHeader title={`Advice file · ${data.household}`} actions={<>
        {canAsk && (
          <button className="btn-outline text-xs" data-testid="advice-record"
            onClick={() => nav.say?.(withRef(`Record a recommendation for ${data.household}.`, "household", data.household_id))}>
            Record a recommendation
          </button>
        )}
        <AllClients href="/studio/advice-file" />
      </>} />
      <div className="mb-4 grid grid-cols-2 gap-3 cq-md:grid-cols-4" data-testid="advice-summary">
        <Tile label="On file" value={o.made} />
        <Tile label="Implemented" value={adviceRate(o.rate)} tone={o.rate === null ? "text-ink-muted" : "text-ink"} />
        <Tile label="Overdue" value={data.overdue} tone={data.overdue ? "text-crit" : "text-ink-muted"} />
        <Tile label="Revisit due" value={data.revisit_due} tone={data.revisit_due ? "text-warn" : "text-ink-muted"} />
      </div>
      {basis}
      <Card className="mb-4"><ImplementationChips implementation={data.implementation} /></Card>
      <Rows rows={data.recommendations} canAsk={canAsk} say={nav.say} detail />
    </div>
  );
}

function Rows({ rows, showClient = false, detail = false, canAsk, say }: {
  rows: any[]; showClient?: boolean; detail?: boolean; canAsk: boolean; say: ((text: string) => void) | null;
}) {
  if (rows.length === 0) return <Empty>No recommendation on file.</Empty>;
  return (
    <div className="space-y-3" data-testid="advice-rows">
      {rows.map((r) => (
        <Card key={r.id} data-testid="advice-row">
          <div className="mb-2 flex flex-wrap items-center gap-1.5">
            {showClient && <ViewLink href={`/studio/advice-file?household=${r.household_id}`} className="text-sm font-medium text-ink hover:underline">{r.household}</ViewLink>}
            <span className="chip text-ink">{ADVICE_CATEGORY[r.category] ?? r.category}</span>
            <AdviceStatusChip status={r.status} on={r.status_on} />
            {r.overdue && <span className="chip text-crit">overdue {reviewDay(r.due_on)}</span>}
            {r.revisit_due && <span className="chip text-warn">revisit due {reviewDay(r.revisit_on)}</span>}
          </div>
          <div className="mb-2 text-sm text-ink">{r.recommendation}</div>
          <div className="mb-2 flex flex-wrap gap-1.5">
            {r.person && <span className="chip text-ink-muted">{r.person}</span>}
            <span className="chip text-ink-muted">owner {r.owner ?? "none"}</span>
            <span className="chip text-ink-muted">made {reviewDay(r.recommended_on)}</span>
            {r.due_on && !r.overdue && <span className="chip text-ink-muted">due {reviewDay(r.due_on)}</span>}
            {r.revisit_on && !r.revisit_due && <span className="chip text-ink-muted">revisit {reviewDay(r.revisit_on)}</span>}
            <span className="chip text-ink-muted">facts as of {reviewDay(r.data_as_of)}</span>
            <span className="chip text-ink-muted">assumptions {r.assumption_version}</span>
            <RiskBasisChips r={r} />
          </div>
          {detail && (
            <div className="mb-2 overflow-x-auto">
              <table className="w-full text-xs" data-testid="advice-detail">
                <tbody className="divide-y divide-border-soft">
                  <tr><td className="w-32 py-1.5 pr-3 align-top text-ink-muted">Rested on</td><td className="py-1.5 text-ink">{r.facts}</td></tr>
                  {r.alternatives.map((a: any, i: number) => (
                    <tr key={i}><td className="py-1.5 pr-3 align-top text-ink-muted">Rejected</td><td className="py-1.5 text-ink">{a.option} <span className="text-ink-muted">· {a.why_rejected}</span></td></tr>
                  ))}
                  {r.history.map((h: any) => (
                    <tr key={h.seq} data-testid="advice-history-row">
                      <td className="py-1.5 pr-3 align-top"><AdviceStatusChip status={h.status} on={h.happened_on} /></td>
                      <td className="py-1.5 text-ink-muted">{h.evidence_kind ? `${h.evidence_kind}: ${h.evidence}` : "—"} · {h.recorded_by}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}
          {canAsk && say && (MOVES[r.status] ?? []).length > 0 && (
            <div className="flex flex-wrap gap-1.5">
              {MOVES[r.status].map((to) => (
                <button key={to} className="btn-outline text-xs" data-testid={`advice-mark-${to}`}
                  onClick={() => say(withRef(`Record that ${r.household}'s ${(ADVICE_CATEGORY[r.category] ?? r.category).toLowerCase()} recommendation "${r.recommendation}" was ${to}.`, "advice", r.id))}>
                  {to === "implemented" ? "Implemented" : `Client ${to}`}
                </button>
              ))}
            </div>
          )}
        </Card>
      ))}
    </div>
  );
}
