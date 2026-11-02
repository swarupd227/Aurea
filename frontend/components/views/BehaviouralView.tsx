"use client";

import { Card, Empty, ErrorState, SkeletonList } from "@/components/ui";
import { money, pct } from "@/lib/format";
import { useApi } from "@/lib/hooks";
import {
  BehaviouralPayload, SEVERITY_TONE, biasLabel, drawdown, scoreTone,
} from "@/app/workspace/components/artifacts/BehaviouralCard";
import { ViewLink } from "./ViewNav";
import { AllClients, ViewHeader } from "./ViewHeader";

const ROUTE = "/studio/behavioural";

const BAR: Record<string, string> = { "text-crit": "bg-crit", "text-warn": "bg-warn", "text-ink-muted": "bg-ok" };

const STATUS_TONE: Record<string, string> = {
  approved: "text-ok", executed: "text-ok", dismissed: "text-crit", rolled_back: "text-warn", modified: "text-warn",
};

function Tile({ label, value, sub, tone = "text-ink" }: { label: string; value: React.ReactNode; sub?: React.ReactNode; tone?: string }) {
  return (
    <Card>
      <div className="tile-label">{label}</div>
      <div className={`font-mono text-lg tabular-nums ${tone}`}>{value}</div>
      {sub != null && <div className="mt-0.5 font-mono text-xs tabular-nums text-ink-muted">{sub}</div>}
    </Card>
  );
}

/** Whom to open: every household, each a link to its own profile. */
export function HouseholdPicker({ route, testId }: { route: string; testId: string }) {
  const { data, loading, error, refetch } = useApi<any>("/api/core/households");
  if (error) return <Card><ErrorState what="households" message={error} onRetry={refetch} /></Card>;
  if (loading || !data) return <SkeletonList count={3} />;
  const rows: { id: string; name: string }[] = Array.isArray(data) ? data : data.households ?? [];
  if (rows.length === 0) return <Empty>No households.</Empty>;
  return (
    <Card className="overflow-hidden p-0">
      <div className="divide-y divide-border-soft" data-testid={testId}>
        {rows.map((h) => (
          <ViewLink key={h.id} href={`${route}?household=${h.id}`} className="block px-4 py-2 text-sm text-ink hover:underline">{h.name}</ViewLink>
        ))}
      </div>
    </Card>
  );
}

/** One household's behavioural profile: what its decisions say, what its words say, and how far its
 * book sits from cost. With no household it lists whom to open. */
export function BehaviouralView({ household = null }: { household?: string | null }) {
  const { data, loading, error, refetch } = useApi<BehaviouralPayload>(
    household ? `/api/core/households/${household}/behavioural` : null, [household]);

  if (!household) {
    return (
      <div className="view-container">
        <ViewHeader title="Behavioural finance" />
        <HouseholdPicker route={ROUTE} testId="behavioural-clients" />
      </div>
    );
  }
  if (error) return <div className="view-container"><Card><ErrorState what="the behavioural profile" message={error} onRetry={refetch} /></Card></div>;
  // Moving between clients, the last response is still here for a render.
  if (loading || !data || data.household_id !== household) return <div className="view-container"><SkeletonList count={4} /></div>;

  const dp = data.decision_profile;
  const s = data.summary;
  const st = data.stress;
  const draft = data.stress_draft_message;

  return (
    <div className="view-container">
      <ViewHeader
        title={`Behavioural finance · ${data.household_name}`}
        meta={<>
          <span className={`chip ${s.is_stressed ? "text-crit" : "text-ok"}`}>{s.is_stressed ? "stressed" : "within tolerance"}</span>
          <span className="chip text-ink-muted">generated {data.generated_at}</span>
        </>}
        actions={<AllClients href={ROUTE} />}
      />

      <div className="mb-4 grid grid-cols-2 gap-3 cq-md:grid-cols-4" data-testid="behavioural-summary">
        <Tile label="Recommendations analysed" value={s.total_recs_analysed} sub={`${s.decided_recs} decided`} />
        <Tile label="Dominant bias" value={biasLabel(s.dominant_bias)} tone={s.dominant_bias ? "text-ink" : "text-ink-muted"} />
        <Tile label="Communication signals" value={s.bias_signals_count} tone={s.bias_signals_count ? "text-warn" : "text-ink-muted"} />
        <Tile label="Vs cost basis" value={drawdown(s.drawdown_pct)} tone={s.is_stressed ? "text-crit" : s.drawdown_pct < 0 ? "text-warn" : "text-ok"} />
      </div>

      <h2 className="mb-2 text-sm font-medium text-ink">Decision profile</h2>
      <Card className="mb-4" data-testid="behavioural-decisions">
        <div className="mb-4 grid grid-cols-3 gap-3">
          {([
            ["Approved", dp.approved, dp.approve_rate, "text-ok"],
            ["Modified", dp.modified, dp.modify_rate, "text-warn"],
            ["Dismissed", dp.dismissed, dp.dismiss_rate, "text-crit"],
          ] as const).map(([label, n, rate, tone]) => (
            <div key={label}>
              <div className="tile-label">{label}</div>
              <div className={`font-mono text-lg tabular-nums ${tone}`}>{n}</div>
              <div className="font-mono text-xs tabular-nums text-ink-muted">{pct(rate, 0)}</div>
            </div>
          ))}
        </div>
        <div className="tile-label mb-2">Bias scores · from decisions</div>
        <div className="space-y-2" data-testid="behavioural-bias-scores">
          {Object.entries(dp.bias_scores).map(([b, v]) => {
            const tone = scoreTone(v);
            return (
              <div key={b} className="flex items-center gap-3 text-sm">
                <span className={`w-32 shrink-0 ${b === dp.dominant_bias ? "text-ink" : "text-ink-muted"}`}>{biasLabel(b)}</span>
                <div className="h-1.5 flex-1 rounded-full bg-border-soft">
                  <div className={`h-1.5 rounded-full ${BAR[tone]}`} style={{ width: `${Math.round(Math.min(1, Math.max(0, v)) * 100)}%` }} />
                </div>
                <span className={`w-10 text-right font-mono text-xs tabular-nums ${tone}`}>{pct(v, 0)}</span>
              </div>
            );
          })}
        </div>
      </Card>

      {dp.recent_decisions.length > 0 && (
        <>
          <h2 className="mb-2 text-sm font-medium text-ink">Recent decisions</h2>
          <Card className="mb-4 overflow-hidden p-0">
            <div className="divide-y divide-border-soft" data-testid="behavioural-recent">
              {dp.recent_decisions.map((d, i) => (
                <div key={i} className="flex items-start gap-3 px-4 py-2 text-sm">
                  <span className={`chip shrink-0 bg-border-soft ${STATUS_TONE[d.status] ?? "text-ink-muted"}`}>{d.status.replace(/_/g, " ")}</span>
                  <div className="min-w-0 flex-1">
                    <div className="truncate text-ink" title={d.title}>{d.title}</div>
                    {d.decision_note && <div className="truncate text-xs text-ink-muted" title={d.decision_note}>{d.decision_note}</div>}
                  </div>
                  <span className="shrink-0 font-mono text-xs tabular-nums text-ink-muted">{d.decided_at ? d.decided_at.slice(0, 10) : "—"}</span>
                </div>
              ))}
            </div>
          </Card>
        </>
      )}

      <h2 className="mb-2 text-sm font-medium text-ink">Communication signals · meetings and client messages</h2>
      {data.bias_signals.length === 0 ? <Card className="mb-4"><Empty>No signals found.</Empty></Card> : (
        <Card className="mb-4 overflow-hidden p-0">
          <div className="divide-y divide-border-soft" data-testid="behavioural-signals">
            {data.bias_signals.map((x, i) => (
              <div key={i} className="flex flex-wrap items-center gap-2 px-4 py-2 text-sm" title={x.description}>
                <span className="chip bg-border-soft text-ink-soft">{biasLabel(x.type)}</span>
                <span className="min-w-0 flex-1 truncate text-ink-muted">{x.source}</span>
                <span className="max-w-full truncate font-mono text-xs text-ink-muted">“{x.signal}”</span>
              </div>
            ))}
          </div>
        </Card>
      )}

      <h2 className="mb-2 text-sm font-medium text-ink">Portfolio stress</h2>
      <Card className="mb-4" data-testid="behavioural-stress">
        <div className="mb-3 flex flex-wrap gap-1.5">
          <span className={`chip ${st.is_stressed ? "text-crit" : "text-ok"}`}>{st.is_stressed ? "stressed" : "within tolerance"}</span>
          {st.stress_threshold_pct != null && <span className="chip text-ink-muted">playbook below {pct(st.stress_threshold_pct / 100, 0)}</span>}
        </div>
        <div className="grid grid-cols-2 gap-3 cq-md:grid-cols-4">
          {([
            ["Current value", money(st.total_value), "text-ink"],
            ["Cost basis", money(st.total_cost), "text-ink"],
            ["Unrealised", money(st.unrealised_pnl), st.unrealised_pnl < 0 ? "text-crit" : "text-ok"],
            ["Vs cost", drawdown(st.drawdown_pct), st.drawdown_pct < 0 ? "text-crit" : "text-ok"],
          ] as const).map(([label, v, tone]) => (
            <div key={label}>
              <div className="tile-label">{label}</div>
              <div className={`font-mono text-sm tabular-nums ${tone}`}>{v}</div>
            </div>
          ))}
        </div>
        {draft && (
          <details className="mt-4 rounded-lg border border-border" data-testid="behavioural-draft">
            <summary className="flex cursor-pointer flex-wrap items-center gap-1.5 px-3 py-2 text-sm text-ink">
              Draft client message
              <span className="chip text-warn">draft · not sent</span>
              <span className="chip text-ink-muted">{draft.trigger}</span>
              <span className="chip text-ink-muted">{draft.tone}</span>
            </summary>
            <div className="border-t border-border-soft px-3 py-2 text-xs leading-relaxed text-ink-soft">{draft.draft_message}</div>
          </details>
        )}
      </Card>

      <h2 className="mb-2 text-sm font-medium text-ink">Adviser coaching</h2>
      {data.coaching_advice.length === 0 ? <Card><Empty>No coaching points.</Empty></Card> : (
        <div className="space-y-3" data-testid="behavioural-coaching">
          {data.coaching_advice.map((c, i) => (
            <Card key={i} className="p-0">
              <div className="flex flex-wrap items-center gap-2 border-b border-border-soft px-4 py-2">
                <span className="text-sm font-medium text-ink">{c.bias}</span>
                <span className={`chip bg-border-soft ${SEVERITY_TONE[c.severity] ?? "text-ink-muted"}`}>{c.severity}</span>
                {c.score != null && <span className="ml-auto font-mono text-xs tabular-nums text-ink-muted">{pct(c.score, 0)}</span>}
              </div>
              <ul className="space-y-2 px-4 py-2 text-xs leading-relaxed">
                <li><span className="tile-label mr-2 text-ok">Frame</span><span className="text-ink-soft">{c.framing}</span></li>
                <li><span className="tile-label mr-2 text-crit">Avoid</span><span className="text-ink-muted">{c.avoid}</span></li>
              </ul>
            </Card>
          ))}
        </div>
      )}
    </div>
  );
}
