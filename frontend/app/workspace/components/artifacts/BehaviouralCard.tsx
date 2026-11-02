import { money, pct } from "@/lib/format";

// The read_behavioural card: one household's decision history, the bias it points to, the language
// signals found in meetings and client messages, and the drawdown against cost. Figures and chips
// only; the coaching sentences and the draft client message stay on the view, and the agent's
// message carries the words.

export interface BehaviouralDecision {
  title: string;
  agent_key: string;
  status: string;
  decided_at: string | null;
  decision_note: string | null;
}

export interface BehaviouralSignal {
  type: string;
  signal: string;
  source: string;
  description: string;
}

export interface BehaviouralCoaching {
  bias: string;
  severity: string;
  score: number | null;
  framing: string;
  avoid: string;
}

export interface BehaviouralPayload {
  household_id: string;
  household_name: string;
  decision_profile: {
    total: number;
    approved: number;
    modified: number;
    dismissed: number;
    approve_rate: number;
    modify_rate: number;
    dismiss_rate: number;
    bias_scores: Record<string, number>;
    dominant_bias: string | null;
    recent_decisions: BehaviouralDecision[];
  };
  bias_signals: BehaviouralSignal[];
  stress: {
    total_value: number;
    total_cost: number;
    unrealised_pnl: number;
    /** Percent units: -12.5 is 12.5% below cost. */
    drawdown_pct: number;
    is_stressed: boolean;
    /** Percent units; absent when there is no cost basis to measure against. */
    stress_threshold_pct?: number;
  };
  stress_draft_message: { draft_message: string; is_draft: boolean; trigger: string; tone: string; action_required: string } | null;
  coaching_advice: BehaviouralCoaching[];
  summary: {
    total_recs_analysed: number;
    decided_recs: number;
    dominant_bias: string | null;
    bias_signals_count: number;
    coaching_points: number;
    is_stressed: boolean;
    drawdown_pct: number;
  };
  generated_at: string;
  message?: string;
}

export const BIAS_LABELS: Record<string, string> = {
  loss_aversion: "Loss aversion",
  status_quo_bias: "Status quo bias",
  overconfidence: "Overconfidence",
  recency_bias: "Recency bias",
  anchoring: "Anchoring",
};

export const biasLabel = (b: string | null | undefined) => (b ? BIAS_LABELS[b] ?? b.replace(/_/g, " ") : "None");

/** Tone for a 0-1 bias score: above 0.6 is the high band, above 0.3 is where coaching begins. */
export const scoreTone = (s: number) => (s > 0.6 ? "text-crit" : s > 0.3 ? "text-warn" : "text-ink-muted");

export const SEVERITY_TONE: Record<string, string> = { high: "text-crit", medium: "text-warn", low: "text-ink-muted" };

/** Drawdown in percent units, signed. */
export const drawdown = (p: number) => `${p >= 0 ? "+" : ""}${pct(p / 100)}`;

const num = (v: unknown) => typeof v === "number" && !isNaN(v);

export function isBehavioural(r: any): boolean {
  const dp = r?.decision_profile;
  const s = r?.summary;
  return !!r
    && typeof r.household_name === "string"
    && !!dp && ["total", "approved", "modified", "dismissed", "approve_rate", "modify_rate", "dismiss_rate"].every((k) => num(dp[k]))
    && !!dp.bias_scores && typeof dp.bias_scores === "object" && Object.values(dp.bias_scores).every(num)
    && !!s && ["total_recs_analysed", "decided_recs", "bias_signals_count", "drawdown_pct"].every((k) => num(s[k]))
    && typeof s.is_stressed === "boolean"
    && !!r.stress && num(r.stress.total_value) && num(r.stress.total_cost)
    && Array.isArray(r.bias_signals) && r.bias_signals.every((x: any) => typeof x?.type === "string")
    && Array.isArray(r.coaching_advice) && r.coaching_advice.every((c: any) => typeof c?.bias === "string" && typeof c?.severity === "string");
}

const Chip = ({ children, tone = "text-ink-muted" }: { children: React.ReactNode; tone?: string }) => (
  <span className={`inline-block rounded border border-border px-1.5 py-0.5 font-mono text-[11px] ${tone}`}>{children}</span>
);

function Tile({ label, value, tone = "text-ink", hint }: { label: string; value: React.ReactNode; tone?: string; hint?: React.ReactNode }) {
  return (
    <div className="px-3 py-2">
      <div className="font-mono text-[11px] uppercase tracking-wider text-ink-muted">{label}</div>
      <div className={`mt-0.5 font-mono text-lg tabular-nums ${tone}`}>{value}</div>
      {hint && <div className="mt-0.5 font-mono text-[11px] tabular-nums text-ink-muted">{hint}</div>}
    </div>
  );
}

export function BehaviouralCard({ result }: { result: BehaviouralPayload }) {
  const dp = result.decision_profile;
  const s = result.summary;
  const st = result.stress;
  const signalTypes = Array.from(new Set(result.bias_signals.map((x) => x.type)));
  return (
    <div className="overflow-hidden rounded-lg border border-border bg-surface" data-testid="behavioural-card">
      <div className="flex items-center justify-between gap-2 border-b border-border-soft px-3 py-2">
        <div className="truncate text-sm font-medium text-ink">Behavioural · {result.household_name}</div>
        <Chip tone={s.is_stressed ? "text-crit" : "text-ok"}>{s.is_stressed ? "stressed" : "within tolerance"}</Chip>
      </div>
      <div className="grid grid-cols-2 divide-x divide-border-soft border-b border-border-soft">
        <Tile label="Decided" value={s.decided_recs} hint={`of ${s.total_recs_analysed} analysed`} />
        <Tile label="Vs cost" value={drawdown(s.drawdown_pct)} tone={s.is_stressed ? "text-crit" : s.drawdown_pct < 0 ? "text-warn" : "text-ok"}
          hint={`${money(st.total_value)} · cost ${money(st.total_cost)}`} />
      </div>
      <div className="flex flex-wrap gap-1.5 border-b border-border-soft px-3 py-1.5">
        <Chip tone="text-ok">approved {dp.approved} · {pct(dp.approve_rate, 0)}</Chip>
        <Chip tone="text-warn">modified {dp.modified} · {pct(dp.modify_rate, 0)}</Chip>
        <Chip tone="text-crit">dismissed {dp.dismissed} · {pct(dp.dismiss_rate, 0)}</Chip>
      </div>
      <div className="flex flex-wrap gap-1.5 border-b border-border-soft px-3 py-1.5">
        <Chip tone={s.dominant_bias ? "text-ink" : "text-ink-muted"}>dominant: {biasLabel(s.dominant_bias)}</Chip>
        {Object.entries(dp.bias_scores).map(([b, v]) => (
          <Chip key={b} tone={scoreTone(v)}>{biasLabel(b)} {pct(v, 0)}</Chip>
        ))}
      </div>
      <div className="flex flex-wrap gap-1.5 px-3 py-1.5">
        <Chip tone={s.bias_signals_count ? "text-warn" : "text-ink-muted"}>signals {s.bias_signals_count}</Chip>
        {signalTypes.map((t) => <Chip key={t}>{biasLabel(t)}</Chip>)}
        {result.coaching_advice.map((c, i) => (
          <Chip key={`${c.bias}-${i}`} tone={SEVERITY_TONE[c.severity] ?? "text-ink-muted"}>coach: {c.bias} · {c.severity}</Chip>
        ))}
        {result.stress_draft_message && <Chip tone="text-warn">draft client message · not sent</Chip>}
      </div>
    </div>
  );
}
