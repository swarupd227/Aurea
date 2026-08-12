"use client";
import { useEffect, useState, useCallback } from "react";
import { api } from "@/lib/api";
import {
  Brain, AlertTriangle, TrendingDown, MessageSquare, BarChart2,
  RefreshCw, ChevronDown, ChevronRight, Info, CheckCircle2, Zap,
} from "lucide-react";

// ── Types ─────────────────────────────────────────────────────────────────────

interface BiasScore { loss_aversion: number; status_quo_bias: number; overconfidence: number }

interface RecentDecision {
  title: string; agent_key: string; status: string;
  decided_at: string | null; decision_note: string | null;
}

interface DecisionProfile {
  total: number; approved: number; modified: number; dismissed: number;
  approve_rate: number; modify_rate: number; dismiss_rate: number;
  bias_scores: BiasScore;
  dominant_bias: string | null;
  recent_decisions: RecentDecision[];
}

interface BiasSignal {
  type: string; signal: string; source: string; description: string;
}

interface Stress {
  total_value: number; total_cost: number; unrealised_pnl: number;
  drawdown_pct: number; is_stressed: boolean; stress_threshold_pct: number;
}

interface StressDraft {
  draft_message: string; is_draft: boolean; trigger: string;
  tone: string; action_required: string;
}

interface CoachingAdvice {
  bias: string; severity: string; score: number | null;
  framing: string; avoid: string;
}

interface HouseholdResult {
  household_id: string; household_name: string;
  decision_profile: DecisionProfile;
  bias_signals: BiasSignal[];
  stress: Stress;
  stress_draft_message: StressDraft | null;
  coaching_advice: CoachingAdvice[];
  summary: {
    total_recs_analysed: number; decided_recs: number;
    dominant_bias: string | null; bias_signals_count: number;
    coaching_points: number; is_stressed: boolean; drawdown_pct: number;
  };
  generated_at: string;
}

interface Household { id: string; name: string; total_value: number }

// ── Utilities ─────────────────────────────────────────────────────────────────

const BIAS_LABELS: Record<string, string> = {
  loss_aversion: "Loss aversion",
  status_quo_bias: "Status quo bias",
  overconfidence: "Overconfidence",
  recency_bias: "Recency bias",
  anchoring: "Anchoring",
};

const BIAS_COLOR: Record<string, string> = {
  loss_aversion: "text-red-600 dark:text-red-400 bg-red-50 dark:bg-red-900/20",
  status_quo_bias: "text-amber-600 dark:text-amber-400 bg-amber-50 dark:bg-amber-900/20",
  overconfidence: "text-purple-600 dark:text-purple-400 bg-purple-50 dark:bg-purple-900/20",
  recency_bias: "text-blue-600 dark:text-blue-400 bg-blue-50 dark:bg-blue-900/20",
  anchoring: "text-orange-600 dark:text-orange-400 bg-orange-50 dark:bg-orange-900/20",
};

const SEV_COLOR: Record<string, string> = {
  high: "bg-red-100 dark:bg-red-900/30 text-red-700 dark:text-red-300",
  medium: "bg-amber-100 dark:bg-amber-900/30 text-amber-700 dark:text-amber-300",
  low: "bg-neutral-100 dark:bg-neutral-700 text-neutral-600 dark:text-neutral-300",
};

function scoreBar(score: number) {
  const pct = Math.round(score * 100);
  const color = pct > 60 ? "bg-red-500" : pct > 30 ? "bg-amber-400" : "bg-emerald-400";
  return (
    <div className="flex items-center gap-2">
      <div className="flex-1 bg-neutral-200 dark:bg-neutral-700 rounded-full h-1.5">
        <div className={`${color} h-1.5 rounded-full transition-all`} style={{ width: `${pct}%` }} />
      </div>
      <span className="text-xs font-mono text-neutral-600 dark:text-neutral-400 w-8 text-right">{pct}%</span>
    </div>
  );
}

// ── Sub-components ────────────────────────────────────────────────────────────

function SummaryCard({
  icon: Icon, label, value, sub, color,
}: { icon: any; label: string; value: string; sub?: string; color: string }) {
  return (
    <div className="bg-white dark:bg-neutral-800 border border-neutral-200 dark:border-neutral-700 rounded-xl p-4 flex gap-3">
      <div className={`w-10 h-10 rounded-lg flex items-center justify-center flex-shrink-0 ${color}`}>
        <Icon className="w-5 h-5" />
      </div>
      <div className="min-w-0">
        <p className="text-xs text-neutral-500 dark:text-neutral-400 font-medium">{label}</p>
        <p className="text-lg font-semibold text-neutral-900 dark:text-neutral-100 truncate">{value}</p>
        {sub && <p className="text-xs text-neutral-400 dark:text-neutral-500 mt-0.5">{sub}</p>}
      </div>
    </div>
  );
}

function Panel({ title, children }: { title: string; children: React.ReactNode }) {
  return (
    <div className="bg-white dark:bg-neutral-800 border border-neutral-200 dark:border-neutral-700 rounded-xl p-5">
      <h3 className="font-semibold text-neutral-900 dark:text-neutral-100 mb-4">{title}</h3>
      {children}
    </div>
  );
}

function DecisionProfilePanel({ data }: { data: DecisionProfile }) {
  const [expanded, setExpanded] = useState(false);
  const scores = data.bias_scores;

  return (
    <Panel title="Decision profile">
      <div className="grid grid-cols-3 gap-3 mb-4">
        {[
          { label: "Approved", value: data.approved, pct: data.approve_rate, color: "text-emerald-600 dark:text-emerald-400" },
          { label: "Modified", value: data.modified, pct: data.modify_rate, color: "text-amber-600 dark:text-amber-400" },
          { label: "Dismissed", value: data.dismissed, pct: data.dismiss_rate, color: "text-red-600 dark:text-red-400" },
        ].map(({ label, value, pct, color }) => (
          <div key={label} className="bg-neutral-50 dark:bg-neutral-700/50 rounded-lg p-3 text-center">
            <div className={`text-xl font-bold ${color}`}>{value}</div>
            <div className="text-xs text-neutral-500 dark:text-neutral-400">{label}</div>
            <div className="text-xs text-neutral-400 dark:text-neutral-500">{(pct * 100).toFixed(0)}%</div>
          </div>
        ))}
      </div>

      <div className="space-y-2.5 mb-4">
        <div className="text-xs font-medium text-neutral-500 dark:text-neutral-400 mb-2">Bias scores (from decision history)</div>
        {Object.entries(scores).map(([bias, score]) => (
          <div key={bias}>
            <div className="flex justify-between text-xs mb-1">
              <span className={`font-medium px-1.5 py-0.5 rounded text-xs ${BIAS_COLOR[bias] || "text-neutral-600"}`}>
                {BIAS_LABELS[bias] || bias}
              </span>
            </div>
            {scoreBar(score)}
          </div>
        ))}
      </div>

      {data.dominant_bias && (
        <div className={`text-xs rounded-lg px-3 py-2 ${BIAS_COLOR[data.dominant_bias] || "bg-neutral-100"}`}>
          Dominant bias detected: <strong>{BIAS_LABELS[data.dominant_bias] || data.dominant_bias}</strong>
        </div>
      )}

      {data.recent_decisions.length > 0 && (
        <div className="mt-4">
          <button
            onClick={() => setExpanded((v) => !v)}
            className="flex items-center gap-1 text-xs text-neutral-500 hover:text-neutral-700 dark:hover:text-neutral-300"
          >
            {expanded ? <ChevronDown className="w-3 h-3" /> : <ChevronRight className="w-3 h-3" />}
            Recent decisions ({data.recent_decisions.length})
          </button>
          {expanded && (
            <div className="mt-2 space-y-1.5">
              {data.recent_decisions.map((d, i) => (
                <div key={i} className="flex items-start gap-2 text-xs border-b border-neutral-100 dark:border-neutral-700 pb-1.5">
                  <span className={`mt-0.5 px-1.5 py-0.5 rounded text-[10px] font-medium capitalize ${
                    d.status === "approved" || d.status === "executed" ? "bg-emerald-100 dark:bg-emerald-900/30 text-emerald-700 dark:text-emerald-300" :
                    d.status === "dismissed" ? "bg-red-100 dark:bg-red-900/30 text-red-700 dark:text-red-300" :
                    "bg-amber-100 dark:bg-amber-900/30 text-amber-700 dark:text-amber-300"
                  }`}>
                    {d.status}
                  </span>
                  <div className="flex-1 min-w-0">
                    <div className="text-neutral-800 dark:text-neutral-200 truncate">{d.title}</div>
                    {d.decision_note && (
                      <div className="text-neutral-400 dark:text-neutral-500 truncate">{d.decision_note}</div>
                    )}
                  </div>
                  {d.decided_at && (
                    <span className="text-neutral-400 shrink-0">{d.decided_at.slice(0, 10)}</span>
                  )}
                </div>
              ))}
            </div>
          )}
        </div>
      )}
    </Panel>
  );
}

function BiasSignalsPanel({ signals }: { signals: BiasSignal[] }) {
  if (signals.length === 0)
    return (
      <Panel title="Communication signals">
        <div className="text-sm text-neutral-400 py-4 text-center">
          No bias signals detected in transcripts or messages.
        </div>
      </Panel>
    );

  return (
    <Panel title="Communication signals">
      <p className="text-xs text-neutral-500 dark:text-neutral-400 mb-3">
        Detected in meeting transcripts and client messages.
      </p>
      <div className="space-y-2">
        {signals.map((s, i) => (
          <div key={i} className="rounded-lg border border-neutral-100 dark:border-neutral-700 p-3">
            <div className="flex items-center gap-2 mb-1">
              <span className={`text-xs font-medium px-1.5 py-0.5 rounded ${BIAS_COLOR[s.type] || "bg-neutral-100 text-neutral-600"}`}>
                {BIAS_LABELS[s.type] || s.type}
              </span>
              <span className="text-xs text-neutral-400 dark:text-neutral-500">{s.source}</span>
            </div>
            <p className="text-xs text-neutral-700 dark:text-neutral-300">{s.description}</p>
            <p className="text-[11px] text-neutral-400 dark:text-neutral-500 mt-0.5 font-mono">
              trigger: "{s.signal}"
            </p>
          </div>
        ))}
      </div>
    </Panel>
  );
}

function StressPanel({ stress, draft }: { stress: Stress; draft: StressDraft | null }) {
  const [showDraft, setShowDraft] = useState(false);
  const drawdown = stress.drawdown_pct;
  const isStressed = stress.is_stressed;

  return (
    <Panel title="Portfolio stress">
      <div className="flex items-center gap-3 mb-4">
        <div className={`w-2 h-2 rounded-full ${isStressed ? "bg-red-500" : "bg-emerald-500"}`} />
        <span className={`text-sm font-medium ${isStressed ? "text-red-600 dark:text-red-400" : "text-emerald-600 dark:text-emerald-400"}`}>
          {isStressed ? "Portfolio stressed" : "Within tolerance"}
        </span>
        <span className="text-xs text-neutral-400 ml-auto">
          threshold: {stress.stress_threshold_pct?.toFixed(0)}%
        </span>
      </div>

      <div className="grid grid-cols-3 gap-3 mb-4">
        {[
          { label: "Current value", value: `$${(stress.total_value / 1_000_000).toFixed(2)}M` },
          { label: "Cost basis", value: `$${(stress.total_cost / 1_000_000).toFixed(2)}M` },
          {
            label: "Drawdown",
            value: drawdown >= 0 ? `+${drawdown.toFixed(1)}%` : `${drawdown.toFixed(1)}%`,
            color: drawdown < 0 ? "text-red-600 dark:text-red-400" : "text-emerald-600 dark:text-emerald-400",
          },
        ].map(({ label, value, color }) => (
          <div key={label} className="bg-neutral-50 dark:bg-neutral-700/50 rounded-lg p-3">
            <div className="text-xs text-neutral-500 dark:text-neutral-400 mb-1">{label}</div>
            <div className={`text-sm font-semibold ${color || "text-neutral-900 dark:text-neutral-100"}`}>{value}</div>
          </div>
        ))}
      </div>

      {draft && (
        <div className="border border-amber-200 dark:border-amber-800 rounded-lg overflow-hidden">
          <button
            onClick={() => setShowDraft((v) => !v)}
            className="w-full flex items-center gap-2 px-3 py-2.5 bg-amber-50 dark:bg-amber-900/20 text-left"
          >
            <Zap className="w-3.5 h-3.5 text-amber-600 dark:text-amber-400" />
            <span className="text-xs font-medium text-amber-700 dark:text-amber-300">Stress playbook message ready</span>
            <span className="text-xs text-amber-500 ml-1">— {draft.trigger}</span>
            <span className="ml-auto">{showDraft ? <ChevronDown className="w-3.5 h-3.5 text-amber-500" /> : <ChevronRight className="w-3.5 h-3.5 text-amber-500" />}</span>
          </button>
          {showDraft && (
            <div className="p-3 bg-amber-50/50 dark:bg-amber-900/10 space-y-2">
              <p className="text-xs text-amber-800 dark:text-amber-200 leading-relaxed">{draft.draft_message}</p>
              <p className="text-[11px] text-amber-600 dark:text-amber-400 flex items-center gap-1">
                <Info className="w-3 h-3" /> {draft.action_required}
              </p>
            </div>
          )}
        </div>
      )}

      {!draft && !isStressed && (
        <p className="text-xs text-neutral-400 dark:text-neutral-500">
          Stress playbook activates when drawdown exceeds {stress.stress_threshold_pct?.toFixed(0)}%.
        </p>
      )}
    </Panel>
  );
}

function CoachingPanel({ advice }: { advice: CoachingAdvice[] }) {
  if (advice.length === 0)
    return (
      <Panel title="Adviser coaching">
        <div className="text-sm text-neutral-400 py-4 text-center">
          No coaching advice — no significant bias patterns detected.
        </div>
      </Panel>
    );

  return (
    <Panel title="Adviser coaching">
      <p className="text-xs text-neutral-500 dark:text-neutral-400 mb-3">
        Framing guidance for the next client conversation.
      </p>
      <div className="space-y-3">
        {advice.map((c, i) => (
          <div key={i} className="border border-neutral-200 dark:border-neutral-700 rounded-lg overflow-hidden">
            <div className="flex items-center gap-2 px-3 py-2 bg-neutral-50 dark:bg-neutral-700/50">
              <span className="text-sm font-medium text-neutral-800 dark:text-neutral-200">{c.bias}</span>
              <span className={`text-[10px] font-medium px-1.5 py-0.5 rounded ${SEV_COLOR[c.severity] || SEV_COLOR.low}`}>
                {c.severity}
              </span>
              {c.score !== null && (
                <span className="ml-auto text-xs font-mono text-neutral-400">{(c.score * 100).toFixed(0)}%</span>
              )}
            </div>
            <div className="px-3 py-2.5 space-y-2">
              <div>
                <div className="text-[10px] font-semibold text-emerald-600 dark:text-emerald-400 uppercase tracking-wider mb-0.5">
                  How to frame it
                </div>
                <p className="text-xs text-neutral-700 dark:text-neutral-300 leading-relaxed">{c.framing}</p>
              </div>
              <div>
                <div className="text-[10px] font-semibold text-red-500 dark:text-red-400 uppercase tracking-wider mb-0.5">
                  What to avoid
                </div>
                <p className="text-xs text-neutral-500 dark:text-neutral-400 leading-relaxed">{c.avoid}</p>
              </div>
            </div>
          </div>
        ))}
      </div>
    </Panel>
  );
}

// ── Main page ─────────────────────────────────────────────────────────────────

export default function BehaviouralPage() {
  const [households, setHouseholds] = useState<Household[]>([]);
  const [selectedId, setSelectedId] = useState<string>("");
  const [data, setData] = useState<HouseholdResult | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    api<{ id: string; name: string; total_value: number }[]>("/api/core/households")
      .then((rows) => {
        setHouseholds(rows);
        if (rows.length) setSelectedId(rows[0].id);
      })
      .catch(() => {});
  }, []);

  const load = useCallback(async (id: string) => {
    if (!id) return;
    setLoading(true);
    setError(null);
    try {
      const result = await api<HouseholdResult>(`/api/core/households/${id}/behavioural`);
      setData(result);
    } catch (e: any) {
      setError(e.message || "Failed to load behavioural profile.");
      setData(null);
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    if (selectedId) load(selectedId);
  }, [selectedId, load]);

  const summary = data?.summary;

  return (
    <div className="space-y-6">
      {/* Header */}
      <div className="flex items-center justify-between">
        <div className="flex items-center gap-3">
          <div className="w-9 h-9 rounded-xl bg-purple-100 dark:bg-purple-900/30 flex items-center justify-center">
            <Brain className="w-5 h-5 text-purple-600 dark:text-purple-400" />
          </div>
          <div>
            <h1 className="text-xl font-semibold text-neutral-900 dark:text-neutral-100">Behavioural finance</h1>
            <p className="text-xs text-neutral-500 dark:text-neutral-400">
              Cognitive bias profiling, stress playbook, and adviser coaching
            </p>
          </div>
        </div>

        <div className="flex items-center gap-2">
          <select
            value={selectedId}
            onChange={(e) => setSelectedId(e.target.value)}
            className="text-sm border border-neutral-200 dark:border-neutral-600 rounded-lg px-3 py-1.5 bg-white dark:bg-neutral-800 text-neutral-800 dark:text-neutral-200"
          >
            {households.map((h) => (
              <option key={h.id} value={h.id}>{h.name}</option>
            ))}
          </select>
          <button
            onClick={() => load(selectedId)}
            disabled={loading}
            className="p-1.5 rounded-lg border border-neutral-200 dark:border-neutral-600 text-neutral-500 hover:text-neutral-700 dark:hover:text-neutral-300"
          >
            <RefreshCw className={`w-4 h-4 ${loading ? "animate-spin" : ""}`} />
          </button>
        </div>
      </div>

      {/* Error */}
      {error && (
        <div className="bg-red-50 dark:bg-red-900/20 border border-red-200 dark:border-red-800 rounded-xl px-4 py-3 text-sm text-red-700 dark:text-red-300">
          {error}
        </div>
      )}

      {/* Loading skeleton */}
      {loading && !data && (
        <div className="space-y-4 animate-pulse">
          <div className="grid grid-cols-2 lg:grid-cols-4 gap-4">
            {[...Array(4)].map((_, i) => (
              <div key={i} className="h-20 bg-neutral-100 dark:bg-neutral-800 rounded-xl" />
            ))}
          </div>
          <div className="grid grid-cols-1 lg:grid-cols-2 gap-4">
            {[...Array(4)].map((_, i) => (
              <div key={i} className="h-48 bg-neutral-100 dark:bg-neutral-800 rounded-xl" />
            ))}
          </div>
        </div>
      )}

      {data && summary && (
        <>
          {/* Summary cards */}
          <div className="grid grid-cols-2 lg:grid-cols-4 gap-4">
            <SummaryCard
              icon={BarChart2}
              label="Recommendations analysed"
              value={String(summary.total_recs_analysed)}
              sub={`${summary.decided_recs} decided`}
              color="bg-blue-100 dark:bg-blue-900/30 text-blue-600 dark:text-blue-400"
            />
            <SummaryCard
              icon={Brain}
              label="Dominant bias"
              value={summary.dominant_bias ? (BIAS_LABELS[summary.dominant_bias] || summary.dominant_bias) : "None detected"}
              sub={summary.decided_recs < 3 ? "Insufficient history" : undefined}
              color="bg-purple-100 dark:bg-purple-900/30 text-purple-600 dark:text-purple-400"
            />
            <SummaryCard
              icon={MessageSquare}
              label="Communication signals"
              value={String(summary.bias_signals_count)}
              sub="in transcripts & messages"
              color={summary.bias_signals_count > 0
                ? "bg-amber-100 dark:bg-amber-900/30 text-amber-600 dark:text-amber-400"
                : "bg-neutral-100 dark:bg-neutral-700 text-neutral-500"
              }
            />
            <SummaryCard
              icon={summary.is_stressed ? AlertTriangle : CheckCircle2}
              label="Portfolio stress"
              value={summary.is_stressed ? "Stressed" : "Within tolerance"}
              sub={`${summary.drawdown_pct >= 0 ? "+" : ""}${summary.drawdown_pct.toFixed(1)}% vs cost basis`}
              color={summary.is_stressed
                ? "bg-red-100 dark:bg-red-900/30 text-red-600 dark:text-red-400"
                : "bg-emerald-100 dark:bg-emerald-900/30 text-emerald-600 dark:text-emerald-400"
              }
            />
          </div>

          {/* Stressed banner */}
          {summary.is_stressed && (
            <div className="flex items-center gap-3 bg-red-50 dark:bg-red-900/20 border border-red-200 dark:border-red-800 rounded-xl px-4 py-3">
              <AlertTriangle className="w-4 h-4 text-red-500 shrink-0" />
              <p className="text-sm text-red-700 dark:text-red-300">
                Portfolio is {Math.abs(summary.drawdown_pct).toFixed(1)}% below cost basis — stress playbook should be reviewed and activated. A draft client message is ready below.
              </p>
            </div>
          )}

          {/* Panels */}
          <div className="grid grid-cols-1 lg:grid-cols-2 gap-4">
            <DecisionProfilePanel data={data.decision_profile} />
            <BiasSignalsPanel signals={data.bias_signals} />
            <StressPanel stress={data.stress} draft={data.stress_draft_message} />
            <CoachingPanel advice={data.coaching_advice} />
          </div>

          <p className="text-xs text-neutral-400 dark:text-neutral-500 text-right">
            Generated {data.generated_at} · Household: {data.household_name}
          </p>
        </>
      )}

      {!loading && !data && !error && (
        <div className="text-center py-16 text-neutral-400 dark:text-neutral-500">
          <Brain className="w-10 h-10 mx-auto mb-3 opacity-30" />
          <p className="text-sm">Select a household to view its behavioural finance profile.</p>
        </div>
      )}
    </div>
  );
}
