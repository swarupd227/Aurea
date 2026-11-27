import { money } from "@/lib/format";
import { reviewDay } from "./ReviewsCard";
import { AdviceHoldChips } from "./AdvicePracticeCard";

/** B-181: the plan of record. Figures, tables and chips: where a plan stands, what was frozen, and the firm's measures. */

export const PLAN_STATE: Record<string, string> = {
  not_started: "not started", data_incomplete: "facts missing", data_complete: "facts complete", baseline: "baseline",
  scenarios: "scenarios", in_review: "in review", reviewed: "reviewed", delivered: "delivered", implementing: "implementing",
  complete: "complete",
};

const STATE_TONE: Record<string, string> = {
  not_started: "text-ink-muted", data_incomplete: "text-warn", data_complete: "text-ink", baseline: "text-ink", scenarios: "text-ink",
  in_review: "text-info", reviewed: "text-info", delivered: "text-ok", implementing: "text-info", complete: "text-ok",
};

export function PlanStateChip({ state }: { state: string }) {
  return <span className={`chip ${STATE_TONE[state] ?? "text-ink-muted"}`} data-testid="plan-state">{PLAN_STATE[state] ?? state}</span>;
}

export function share(v: number | null | undefined): string {
  return v === null || v === undefined ? "—" : `${Math.round(v * 100)}%`;
}

/** Whether a second person reviews it, and why: above the line, within it, or the default until a line is set. */
export function ReviewNeedChip({ need }: { need: any }) {
  if (!need) return null;
  if (need.line_assumed) return <span className="chip text-ink-muted" data-testid="plan-review-need">review needed · default until a line is set</span>;
  return (
    <span className="chip text-ink-muted" data-testid="plan-review-need">
      {need.needed ? `review needed · complexity ${need.score} above ${need.line}` : `no review needed · complexity ${need.score} within ${need.line}`}
    </span>
  );
}

export function FreshnessChip({ f }: { f: any }) {
  if (!f) return null;
  return (
    <span className={`chip ${f.fresh ? "text-ok" : "text-warn"}`} data-testid="plan-freshness" title={`fresh until ${reviewDay(f.fresh_until)}`}>
      {f.fresh ? "fresh" : "stale"} · {f.age_days}d of {f.cadence_months}mo{f.cadence_assumed ? " (assumed)" : ""}
    </span>
  );
}

/** The client's plan summary (B-196): drafted from the frozen figures, with the client once its adviser approves. */
export function SummaryChip({ summary, baselineId }: { summary: any; baselineId?: string | null }) {
  if (!summary) return null;
  const earlier = baselineId && summary.baseline_id !== baselineId;
  const [tone, words] = summary.with_client ? (earlier ? ["text-warn", "summary with client · earlier baseline"] : ["text-ok", "summary with client"])
    : earlier ? ["text-warn", "summary of an earlier baseline"]
    : summary.awaiting_decision ? ["text-info", "summary drafted · adviser to approve"]
    : ["text-ink-muted", "summary draft"];
  return <span className={`chip ${tone}`} data-testid="plan-summary-chip">{words}</span>;
}

/** The headline figures a baseline freezes, as rows: frozen, and today beside it where it differs. */
export function figureRows(frozen: any, today: any | null, differences: any[] = []): { key: string; label: string; frozen: string; today: string | null }[] {
  if (!frozen) return [];
  const ccy = frozen.currency ?? undefined;
  const fmt: Record<string, (v: any) => string> = {
    retirement_success: share, needs_met: share, goal: share,
    sustainable_income: (v) => (v === null || v === undefined ? "—" : money(v, ccy)),
    funded_ratio: (v) => (v === null || v === undefined ? "—" : Number(v).toFixed(2)),
    assumption_version: (v) => (v ?? "—"),
  };
  const moved: Record<string, any> = {};
  for (const d of differences) moved[d.figure === "goal" ? `goal:${d.goal}` : d.figure] = d;
  const rows = [
    { key: "retirement_success", label: "Retirement success", v: frozen.retirement?.success_probability },
    { key: "sustainable_income", label: "Sustainable a year", v: frozen.retirement?.sustainable_income },
    { key: "needs_met", label: "Needs met together", v: frozen.tiers?.success },
    { key: "funded_ratio", label: "Funded ratio", v: frozen.funded?.ratio },
    { key: "assumption_version", label: "Assumption version", v: frozen.assumption_version },
    ...(frozen.goals ?? []).map((g: any) => ({ key: `goal:${g.name}`, label: g.name, v: g.success_probability, goal: true })),
  ];
  const added = today ? (today.goals ?? []).filter((g: any) => !(frozen.goals ?? []).some((f: any) => f.id === g.id)) : [];
  return [
    ...rows.map((r: any) => {
      const f = fmt[r.goal ? "goal" : r.key];
      const d = moved[r.key];
      return { key: r.key, label: r.label, frozen: f(r.v), today: today ? (d ? f(d.today) : f(r.v)) : null };
    }),
    ...added.map((g: any) => ({ key: `goal:${g.name}`, label: g.name, frozen: "—", today: share(g.success_probability) })),
  ];
}

export function FiguresTable({ frozen, today, differences }: { frozen: any; today?: any; differences?: any[] }) {
  const rows = figureRows(frozen, today ?? null, differences ?? []);
  if (!rows.length) return null;
  return (
    <div className="overflow-x-auto">
      <table className="w-full text-sm" data-testid="plan-record-figures">
        <thead><tr className="text-left text-[10px] uppercase tracking-wide text-ink-muted">
          <th className="px-4 py-2 font-medium">Figure</th><th className="px-4 py-2 text-right font-medium">Frozen</th>
          {today && <th className="px-4 py-2 text-right font-medium">Today</th>}
        </tr></thead>
        <tbody className="divide-y divide-border-soft">
          {rows.map((r) => (
            <tr key={r.key}>
              <td className="px-4 py-2 text-ink">{r.label}</td>
              <td className="px-4 py-2 text-right font-mono text-xs tabular-nums text-ink">{r.frozen}</td>
              {today && <td className={`px-4 py-2 text-right font-mono text-xs tabular-nums ${r.today !== r.frozen ? "text-warn" : "text-ink-muted"}`}>{r.today}</td>}
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

/** The firm's measures, as chips. */
export function PlanMetricsChips({ m }: { m: any }) {
  return (
    <div className="flex flex-wrap gap-1.5" data-testid="plan-metrics">
      <span className="chip text-ink">coverage {share(m.coverage?.share)} · {m.coverage?.delivered}/{m.coverage?.active}</span>
      <span className="chip text-ink-muted">fresh {share(m.freshness?.share)} · {m.freshness?.fresh}/{m.freshness?.delivered}</span>
      <span className="chip text-ink-muted">QC pass {m.qc?.pass_rate === null ? "no reviews" : share(m.qc?.pass_rate)} · {m.qc?.passed}/{m.qc?.reviews}</span>
      <span className="chip text-ink-muted">scenario depth {m.scenario_depth?.median ?? "—"} median · {m.scenario_depth?.mean ?? "—"} mean</span>
    </div>
  );
}

export function PlanRecordCard({ result }: { result: any }) {
  if (result?.household_id == null && result?.coverage) {
    const by = Object.entries(result.by_state ?? {}).filter(([, n]) => (n as number) > 0);
    return (
      <div className="overflow-hidden rounded-lg border border-border bg-surface" data-testid="plan-record-card">
        <div className="flex flex-wrap items-center justify-between gap-2 border-b border-border-soft px-3 py-2">
          <span className="text-sm font-medium text-ink">Plans of record</span>
          <span className="chip text-ink-muted">{result.scope === "own" ? "your households" : "the firm"}</span>
        </div>
        <div className="space-y-2 px-3 py-2">
          <PlanMetricsChips m={result} />
          <div className="flex flex-wrap gap-1.5">
            {by.map(([state, n]) => <span key={state} className="chip text-ink-muted">{PLAN_STATE[state] ?? state} {n as number}</span>)}
          </div>
        </div>
      </div>
    );
  }
  const frozen = result?.baseline?.figures;
  return (
    <div className="overflow-hidden rounded-lg border border-border bg-surface" data-testid="plan-record-card">
      <div className="flex flex-wrap items-center justify-between gap-2 border-b border-border-soft px-3 py-2">
        <span className="text-sm font-medium text-ink">Plan of record · {result?.household}</span>
        <div className="flex flex-wrap gap-1.5">
          <PlanStateChip state={result?.state} />
          {result?.version ? <span className="chip tabular-nums text-ink-muted">v{result.version}</span> : null}
        </div>
      </div>
      <div className="space-y-2 px-3 py-2">
        <div className="flex flex-wrap gap-1.5">
          <span className="chip text-ink-muted">{result?.owner} next</span>
          <ReviewNeedChip need={result?.review_need} />
          <FreshnessChip f={result?.freshness} />
          {result?.tasks ? <span className="chip text-ink-muted">{result.open_tasks}/{result.tasks} tasks open</span> : null}
          {(result?.data?.missing ?? []).map((m: any) => <span key={m.text} className="chip text-warn">missing {m.text}</span>)}
          {result?.sent_back && <span className="chip text-warn" title={result.sent_back.findings}>sent back {reviewDay(result.sent_back.happened_on)}</span>}
          <SummaryChip summary={result?.summary} baselineId={result?.baseline?.id} />
          <AdviceHoldChips gate={result?.pre_advice} />
        </div>
        {frozen && <div className="-mx-3"><FiguresTable frozen={frozen} today={result?.today} differences={result?.differences} /></div>}
      </div>
    </div>
  );
}
