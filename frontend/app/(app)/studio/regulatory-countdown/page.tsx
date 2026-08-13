"use client";
import { useEffect, useState } from "react";
import { api } from "@/lib/api";
import {
  Timer, AlertTriangle, CheckCircle2, TrendingUp, DollarSign,
  ChevronDown, ChevronRight, RefreshCw, Info, Flag,
} from "lucide-react";

// ── Types ─────────────────────────────────────────────────────────────────────

interface USFigures {
  gross_estate: number; portfolio: number; held_away: number;
  retirement_accounts: number; real_estate: number; married: boolean;
  current_exemption: number; post_sunset_exemption: number;
  exposure_current: number; exposure_post_sunset: number;
  additional_tax_at_risk: number; affected: boolean;
}

interface UKFigures {
  current_estate: number; portfolio: number; pension_total: number;
  isa_total: number; post_2027_estate: number; married: boolean;
  nrb_total: number; rnrb_claimable: number; threshold: number;
  iht_current: number; iht_post_2027: number; additional_iht: number;
  affected: boolean; lpa_missing: string[];
}

interface Action {
  priority: number; action: string; detail: string; estimated_benefit: string;
}

interface BPRFigures {
  estimated_qualifying_assets: number; cap: number; cap_exposure: number;
  estimated_additional_iht: number; affected: boolean;
}

interface FCAFigures {
  total_portfolio_value: number; client_count: number;
}

interface Analysis {
  type: "us_estate_tax_sunset" | "uk_iht_pension_inclusion" | "uk_bpr_apr_cap" | "uk_fca_targeted_support";
  title: string; subtitle: string; deadline: string;
  days_remaining: number; urgency: "critical" | "high" | "medium";
  summary: string; figures: USFigures | UKFigures | BPRFigures | FCAFigures; actions: Action[];
  currency: string; regulation: string; legislative_note?: string;
}

interface HouseholdResult {
  household_id: string; household_name: string;
  jurisdiction: string; analyses: Analysis[]; message?: string;
}

interface Household { id: string; name: string; total_value: number }

// ── Utilities ─────────────────────────────────────────────────────────────────

const URGENCY_CONFIG: Record<string, { label: string; cls: string; dotCls: string }> = {
  critical: { label: "Critical", cls: "bg-red-100 text-red-700 dark:bg-red-900/30 dark:text-red-400", dotCls: "bg-red-500" },
  high:     { label: "High",     cls: "bg-amber-100 text-amber-700 dark:bg-amber-900/30 dark:text-amber-400", dotCls: "bg-amber-500" },
  medium:   { label: "Monitor",  cls: "bg-blue-100 text-blue-700 dark:bg-blue-900/30 dark:text-blue-400", dotCls: "bg-blue-500" },
};

const JURISDICTION_FLAG: Record<string, string> = { NZ: "🇳🇿", US: "🇺🇸", UK: "🇬🇧", MULTI: "🌐" };

function fmt(n: number, currency: string, decimals = 0) {
  const sym = currency === "GBP" ? "£" : "$";
  if (n >= 1_000_000) return `${sym}${(n / 1_000_000).toFixed(decimals === 0 ? 1 : decimals)}M`;
  if (n >= 1_000)     return `${sym}${(n / 1_000).toFixed(0)}k`;
  return `${sym}${n.toFixed(0)}`;
}

function CountdownRing({ days, deadline }: { days: number; deadline: string }) {
  const total = deadline === "2026-01-01" ? 500 : 700;
  const pct = Math.min(1, Math.max(0, days / total));
  const r = 36, cx = 44, cy = 44, stroke = 6;
  const circ = 2 * Math.PI * r;
  const dash = circ * pct;

  const color = days < 200 ? "#ef4444" : days < 400 ? "#f59e0b" : "#3b82f6";

  return (
    <div className="flex flex-col items-center gap-1">
      <svg width={88} height={88}>
        <circle cx={cx} cy={cy} r={r} fill="none" stroke="currentColor" strokeWidth={stroke}
          className="text-ink-faint/30" />
        <circle cx={cx} cy={cy} r={r} fill="none" stroke={color} strokeWidth={stroke}
          strokeDasharray={`${dash} ${circ}`} strokeLinecap="round"
          transform={`rotate(-90 ${cx} ${cy})`} />
        <text x={cx} y={cy - 4} textAnchor="middle" fontSize={14} fontWeight={700} fill="currentColor">
          {days}
        </text>
        <text x={cx} y={cy + 10} textAnchor="middle" fontSize={9} fill="currentColor" opacity={0.6}>
          days
        </text>
      </svg>
      <span className="text-xs text-ink-muted">until {deadline}</span>
    </div>
  );
}

function SummaryCard({ label, value, sub }: { label: string; value: string; sub?: string }) {
  return (
    <div className="bg-surface rounded-lg border border-border p-4">
      <div className="text-xs text-ink-muted mb-1">{label}</div>
      <div className="text-lg font-bold text-ink">{value}</div>
      {sub && <div className="text-xs text-ink-muted mt-0.5">{sub}</div>}
    </div>
  );
}

function ActionItem({ action, open, onToggle }: { action: Action; open: boolean; onToggle: () => void }) {
  const priorityColor = action.priority === 1
    ? "bg-red-500" : action.priority === 2 ? "bg-amber-500" : "bg-blue-500";

  return (
    <div className="border border-border rounded-lg overflow-hidden">
      <button
        className="w-full flex items-center gap-3 px-4 py-3 text-left hover:bg-surface-hover transition-colors"
        onClick={onToggle}
      >
        <span className={`w-5 h-5 rounded-full flex items-center justify-center text-white text-[10px] font-bold flex-shrink-0 ${priorityColor}`}>
          {action.priority}
        </span>
        <span className="flex-1 text-sm font-medium text-ink">{action.action}</span>
        {open ? <ChevronDown size={14} className="text-ink-muted" /> : <ChevronRight size={14} className="text-ink-muted" />}
      </button>
      {open && (
        <div className="border-t border-border px-4 pb-3 pt-2 bg-surface/50 space-y-2">
          <p className="text-sm text-ink-muted">{action.detail}</p>
          <div className="flex items-start gap-2 mt-2 p-2 rounded bg-emerald-50 dark:bg-emerald-900/20">
            <TrendingUp size={13} className="text-emerald-600 dark:text-emerald-400 mt-0.5 flex-shrink-0" />
            <p className="text-xs text-emerald-700 dark:text-emerald-400">{action.estimated_benefit}</p>
          </div>
        </div>
      )}
    </div>
  );
}

function USAnalysisPanel({ analysis }: { analysis: Analysis }) {
  const f = analysis.figures as USFigures;
  const cur = analysis.currency;
  const urgency = URGENCY_CONFIG[analysis.urgency];
  const [openActions, setOpenActions] = useState<Set<number>>(new Set([0]));

  const toggle = (i: number) =>
    setOpenActions(prev => { const s = new Set(prev); s.has(i) ? s.delete(i) : s.add(i); return s; });

  return (
    <div className="space-y-4">
      {/* Header row */}
      <div className="flex items-start gap-4 p-4 bg-surface rounded-xl border border-border">
        <CountdownRing days={analysis.days_remaining} deadline={analysis.deadline} />
        <div className="flex-1 min-w-0">
          <div className="flex items-center gap-2 mb-1">
            <h3 className="font-semibold text-ink">{analysis.title}</h3>
            <span className={`inline-flex items-center gap-1 px-2 py-0.5 rounded-full text-[11px] font-semibold ${urgency.cls}`}>
              <span className={`w-1.5 h-1.5 rounded-full ${urgency.dotCls}`} />
              {urgency.label}
            </span>
          </div>
          <p className="text-xs text-ink-muted mb-2">{analysis.subtitle}</p>
          <p className="text-sm text-ink-secondary">{analysis.summary}</p>
          <p className="text-[11px] text-ink-faint mt-2">{analysis.regulation}</p>
        </div>
      </div>

      {/* Summary cards */}
      <div className="grid grid-cols-2 sm:grid-cols-4 gap-3">
        <SummaryCard label="Estimated gross estate" value={fmt(f.gross_estate, cur)} sub={f.married ? "married couple" : "individual"} />
        <SummaryCard label="Post-sunset exemption" value={fmt(f.post_sunset_exemption, cur)} sub="combined" />
        <SummaryCard label="Post-sunset exposure" value={fmt(f.exposure_post_sunset, cur)} sub={f.exposure_post_sunset > 0 ? "taxable" : "within threshold"} />
        <SummaryCard
          label="Additional tax at risk"
          value={f.additional_tax_at_risk > 0 ? fmt(f.additional_tax_at_risk, cur) : "None"}
          sub={f.additional_tax_at_risk > 0 ? "at 40% federal rate" : "no exposure"}
        />
      </div>

      {/* Estate breakdown */}
      <div className="bg-surface rounded-xl border border-border p-4">
        <h4 className="text-sm font-semibold text-ink mb-3">Estate composition</h4>
        <div className="space-y-2">
          {[
            { label: "Investment portfolio", val: f.portfolio },
            { label: "Held-away assets", val: f.held_away },
            { label: "Retirement accounts (IRA / 401k / Roth)", val: f.retirement_accounts },
            { label: "Real estate", val: f.real_estate },
          ].map(({ label, val }) => (
            <div key={label} className="flex items-center justify-between text-sm">
              <span className="text-ink-muted">{label}</span>
              <span className={`font-medium ${val > 0 ? "text-ink" : "text-ink-faint"}`}>
                {val > 0 ? fmt(val, cur) : "—"}
              </span>
            </div>
          ))}
          <div className="border-t border-border pt-2 flex items-center justify-between text-sm font-semibold">
            <span className="text-ink">Gross estate</span>
            <span className="text-ink">{fmt(f.gross_estate, cur)}</span>
          </div>
        </div>
        <div className="mt-3 grid grid-cols-2 gap-3 text-sm">
          <div className="p-2 rounded-lg bg-emerald-50 dark:bg-emerald-900/20">
            <div className="text-[11px] text-emerald-700 dark:text-emerald-400 font-medium mb-0.5">Current exemption</div>
            <div className="font-bold text-emerald-700 dark:text-emerald-300">{fmt(f.current_exemption, cur)}</div>
          </div>
          <div className="p-2 rounded-lg bg-red-50 dark:bg-red-900/20">
            <div className="text-[11px] text-red-700 dark:text-red-400 font-medium mb-0.5">Post-sunset exemption</div>
            <div className="font-bold text-red-700 dark:text-red-300">{fmt(f.post_sunset_exemption, cur)}</div>
          </div>
        </div>
      </div>

      {/* Actions */}
      {analysis.actions.length > 0 && (
        <div className="bg-surface rounded-xl border border-border p-4">
          <h4 className="text-sm font-semibold text-ink mb-3">Recommended planning actions</h4>
          <div className="space-y-2">
            {analysis.actions.map((a, i) => (
              <ActionItem key={i} action={a} open={openActions.has(i)} onToggle={() => toggle(i)} />
            ))}
          </div>
        </div>
      )}
    </div>
  );
}

function UKAnalysisPanel({ analysis }: { analysis: Analysis }) {
  const f = analysis.figures as UKFigures;
  const cur = analysis.currency;
  const urgency = URGENCY_CONFIG[analysis.urgency];
  const [openActions, setOpenActions] = useState<Set<number>>(new Set([0]));

  const toggle = (i: number) =>
    setOpenActions(prev => { const s = new Set(prev); s.has(i) ? s.delete(i) : s.add(i); return s; });

  return (
    <div className="space-y-4">
      {/* Header row */}
      <div className="flex items-start gap-4 p-4 bg-surface rounded-xl border border-border">
        <CountdownRing days={analysis.days_remaining} deadline={analysis.deadline} />
        <div className="flex-1 min-w-0">
          <div className="flex items-center gap-2 mb-1">
            <h3 className="font-semibold text-ink">{analysis.title}</h3>
            <span className={`inline-flex items-center gap-1 px-2 py-0.5 rounded-full text-[11px] font-semibold ${urgency.cls}`}>
              <span className={`w-1.5 h-1.5 rounded-full ${urgency.dotCls}`} />
              {urgency.label}
            </span>
          </div>
          <p className="text-xs text-ink-muted mb-2">{analysis.subtitle}</p>
          <p className="text-sm text-ink-secondary">{analysis.summary}</p>
          <p className="text-[11px] text-ink-faint mt-2">{analysis.regulation}</p>
        </div>
      </div>

      {/* Summary cards */}
      <div className="grid grid-cols-2 sm:grid-cols-4 gap-3">
        <SummaryCard label="Pension assets" value={fmt(f.pension_total, cur)} sub="entering IHT estate" />
        <SummaryCard label="IHT threshold" value={fmt(f.threshold, cur)} sub={f.married ? "NRB + RNRB (couple)" : "NRB + RNRB"} />
        <SummaryCard label="Current IHT" value={fmt(f.iht_current, cur)} sub="before April 2027" />
        <SummaryCard
          label="Additional IHT"
          value={f.additional_iht > 0 ? fmt(f.additional_iht, cur) : "None"}
          sub={f.additional_iht > 0 ? "from pension inclusion" : "no pension exposure"}
        />
      </div>

      {/* IHT position */}
      <div className="bg-surface rounded-xl border border-border p-4">
        <h4 className="text-sm font-semibold text-ink mb-3">IHT position before and after April 2027</h4>
        <div className="space-y-2">
          {[
            { label: "Investment portfolio", val: f.portfolio },
            { label: "ISA holdings (included in estate)", val: f.isa_total },
            { label: "Pension pots — entering estate April 2027", val: f.pension_total, highlight: true },
          ].map(({ label, val, highlight }) => (
            <div key={label} className="flex items-center justify-between text-sm">
              <span className={highlight ? "text-red-600 dark:text-red-400 font-medium" : "text-ink-muted"}>{label}</span>
              <span className={highlight && val > 0 ? "font-bold text-red-600 dark:text-red-400" : "font-medium text-ink"}>
                {val > 0 ? fmt(val, cur) : "—"}
              </span>
            </div>
          ))}
          <div className="border-t border-border pt-2 grid grid-cols-2 gap-4 mt-2">
            <div>
              <div className="text-[11px] text-ink-muted mb-1">Estate now</div>
              <div className="font-semibold text-ink">{fmt(f.current_estate, cur)}</div>
              <div className="text-[11px] text-emerald-700 dark:text-emerald-400 mt-0.5">
                IHT: {fmt(f.iht_current, cur)}
              </div>
            </div>
            <div>
              <div className="text-[11px] text-ink-muted mb-1">Estate from April 2027</div>
              <div className="font-semibold text-ink">{fmt(f.post_2027_estate, cur)}</div>
              <div className="text-[11px] text-red-600 dark:text-red-400 mt-0.5">
                IHT: {fmt(f.iht_post_2027, cur)}
                {f.additional_iht > 0 && (
                  <span className="ml-1 font-bold">(+{fmt(f.additional_iht, cur)})</span>
                )}
              </div>
            </div>
          </div>
        </div>

        {f.lpa_missing.length > 0 && (
          <div className="mt-3 p-3 rounded-lg border border-amber-300 bg-amber-50 dark:bg-amber-900/20 dark:border-amber-700">
            <div className="flex items-start gap-2">
              <AlertTriangle size={14} className="text-amber-600 dark:text-amber-400 mt-0.5 flex-shrink-0" />
              <div>
                <div className="text-xs font-semibold text-amber-700 dark:text-amber-400">No LPA registered</div>
                <div className="text-xs text-amber-600 dark:text-amber-500 mt-0.5">
                  {f.lpa_missing.join(", ")} {f.lpa_missing.length === 1 ? "has" : "have"} no Lasting Power of Attorney.
                  Without a Property & Finance LPA, family cannot act on pension decisions if capacity is lost.
                </div>
              </div>
            </div>
          </div>
        )}
      </div>

      {/* Actions */}
      {analysis.actions.length > 0 && (
        <div className="bg-surface rounded-xl border border-border p-4">
          <h4 className="text-sm font-semibold text-ink mb-3">Recommended planning actions</h4>
          <div className="space-y-2">
            {analysis.actions.map((a, i) => (
              <ActionItem key={i} action={a} open={openActions.has(i)} onToggle={() => toggle(i)} />
            ))}
          </div>
        </div>
      )}
    </div>
  );
}

function GenericAnalysisPanel({ analysis }: { analysis: Analysis }) {
  const urgency = URGENCY_CONFIG[analysis.urgency] || URGENCY_CONFIG.medium;
  const [openActions, setOpenActions] = useState<Set<number>>(new Set([0]));
  const toggle = (i: number) =>
    setOpenActions(prev => { const s = new Set(prev); s.has(i) ? s.delete(i) : s.add(i); return s; });

  // BPR-specific figures
  const bpr = analysis.type === "uk_bpr_apr_cap" ? analysis.figures as BPRFigures : null;
  // FCA-specific figures
  const fca = analysis.type === "uk_fca_targeted_support" ? analysis.figures as FCAFigures : null;

  return (
    <div className="space-y-4">
      <div className="flex items-start gap-4 p-4 bg-surface rounded-xl border border-border">
        {analysis.days_remaining > 0
          ? <CountdownRing days={analysis.days_remaining} deadline={analysis.deadline} />
          : (
            <div className="flex flex-col items-center gap-1 w-[88px] flex-shrink-0">
              <div className="w-16 h-16 rounded-full bg-red-100 dark:bg-red-900/30 flex items-center justify-center">
                <AlertTriangle size={28} className="text-red-600 dark:text-red-400" />
              </div>
              <span className="text-xs text-ink-muted">In force</span>
            </div>
          )
        }
        <div className="flex-1 min-w-0">
          <div className="flex items-center gap-2 mb-1">
            <h3 className="font-semibold text-ink">{analysis.title}</h3>
            <span className={`inline-flex items-center gap-1 px-2 py-0.5 rounded-full text-[11px] font-semibold ${urgency.cls}`}>
              <span className={`w-1.5 h-1.5 rounded-full ${urgency.dotCls}`} />
              {urgency.label}
            </span>
          </div>
          <p className="text-xs text-ink-muted mb-2">{analysis.subtitle}</p>
          <p className="text-sm text-ink-secondary">{analysis.summary}</p>
          <p className="text-[11px] text-ink-faint mt-2">{analysis.regulation}</p>
          {analysis.legislative_note && (
            <div className="flex items-start gap-1.5 mt-2 p-2 rounded bg-blue-50 dark:bg-blue-900/20">
              <Info size={11} className="text-blue-600 dark:text-blue-400 mt-0.5 flex-shrink-0" />
              <p className="text-xs text-blue-700 dark:text-blue-300">{analysis.legislative_note}</p>
            </div>
          )}
        </div>
      </div>

      {bpr && (
        <div className="grid grid-cols-2 sm:grid-cols-4 gap-3">
          <SummaryCard label="Qualifying assets (est.)" value={fmt(bpr.estimated_qualifying_assets, "GBP")} sub="alternatives proxy" />
          <SummaryCard label="Cap per individual" value="£1M" sub="from April 2027" />
          <SummaryCard label="Exposure above cap" value={bpr.cap_exposure > 0 ? fmt(bpr.cap_exposure, "GBP") : "None"} sub={bpr.cap_exposure > 0 ? "at reduced 50% relief" : "within cap"} />
          <SummaryCard label="Est. additional IHT" value={bpr.estimated_additional_iht > 0 ? fmt(bpr.estimated_additional_iht, "GBP") : "None"} sub={bpr.estimated_additional_iht > 0 ? "at 20% effective rate" : "no incremental IHT"} />
        </div>
      )}

      {fca && (
        <div className="grid grid-cols-2 gap-3">
          <SummaryCard label="Household portfolio" value={fmt(fca.total_portfolio_value, "GBP")} sub="in scope for targeted support" />
          <SummaryCard label="Client count" value={String(fca.client_count)} sub="potential targeted support clients" />
        </div>
      )}

      {analysis.actions.length > 0 && (
        <div className="bg-surface rounded-xl border border-border p-4">
          <h4 className="text-sm font-semibold text-ink mb-3">Recommended planning actions</h4>
          <div className="space-y-2">
            {analysis.actions.map((a, i) => (
              <ActionItem key={i} action={a} open={openActions.has(i)} onToggle={() => toggle(i)} />
            ))}
          </div>
        </div>
      )}
    </div>
  );
}

function NoCountdownPanel({ result }: { result: HouseholdResult }) {
  return (
    <div className="flex flex-col items-center justify-center py-12 text-center">
      <CheckCircle2 size={36} className="text-emerald-500 mb-3" />
      <p className="text-sm font-medium text-ink mb-1">
        {JURISDICTION_FLAG[result.jurisdiction] || "🌐"} {result.jurisdiction} jurisdiction
      </p>
      <p className="text-sm text-ink-muted max-w-sm">
        {result.message || "No critical regulatory deadlines are currently tracked for this jurisdiction."}
      </p>
    </div>
  );
}

// ── Main page ──────────────────────────────────────────────────────────────────

export default function RegulatoryCountdownPage() {
  const [households, setHouseholds] = useState<Household[]>([]);
  const [selectedId, setSelectedId] = useState<string>("");
  const [result, setResult] = useState<HouseholdResult | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    api("/api/core/households").then((d) => {
      const list = Array.isArray(d) ? d : (d.households || []);
      setHouseholds(list);
      if (list.length) setSelectedId(list[0].id);
    });
  }, []);

  const load = async (id: string) => {
    if (!id) return;
    setLoading(true);
    setError(null);
    try {
      const data = await api(`/api/core/households/${id}/regulatory-countdown`);
      setResult(data);
    } catch (e: any) {
      setError(e?.message || "Failed to load analysis");
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => { if (selectedId) load(selectedId); }, [selectedId]);

  const jur = result?.jurisdiction || "";
  const jurCls =
    jur === "US" ? "bg-red-100 text-red-700 dark:bg-red-900/30 dark:text-red-400" :
    jur === "UK" ? "bg-purple-100 text-purple-700 dark:bg-purple-900/30 dark:text-purple-400" :
    "bg-blue-100 text-blue-700 dark:bg-blue-900/30 dark:text-blue-400";

  return (
    <div className="max-w-4xl mx-auto px-4 py-6 space-y-6">
      {/* Page header */}
      <div className="flex items-center justify-between">
        <div className="flex items-center gap-3">
          <Timer size={20} className="text-accent" />
          <div>
            <h1 className="text-lg font-bold text-ink">Regulatory Countdown</h1>
            <p className="text-xs text-ink-muted">Jurisdiction-aware deadline intelligence</p>
          </div>
        </div>
        <button
          onClick={() => load(selectedId)}
          disabled={loading}
          className="flex items-center gap-1.5 px-3 py-1.5 text-xs rounded-lg border border-border hover:bg-surface-hover transition-colors disabled:opacity-50"
        >
          <RefreshCw size={12} className={loading ? "animate-spin" : ""} />
          Refresh
        </button>
      </div>

      {/* Household selector */}
      <div className="flex items-center gap-3">
        <select
          className="input max-w-xs"
          value={selectedId}
          onChange={(e) => setSelectedId(e.target.value)}
        >
          {households.map((h) => (
            <option key={h.id} value={h.id}>{h.name}</option>
          ))}
        </select>
        {result && (
          <span className={`inline-flex items-center gap-1 px-2.5 py-1 rounded-full text-xs font-semibold ${jurCls}`}>
            {JURISDICTION_FLAG[result.jurisdiction] || "🌐"} {result.jurisdiction}
          </span>
        )}
      </div>

      {/* Content */}
      {loading && (
        <div className="flex items-center justify-center py-16">
          <RefreshCw size={20} className="animate-spin text-ink-muted" />
        </div>
      )}

      {error && (
        <div className="flex items-center gap-2 p-4 rounded-lg border border-red-300 bg-red-50 dark:bg-red-900/20 dark:border-red-700">
          <AlertTriangle size={16} className="text-red-600 dark:text-red-400" />
          <span className="text-sm text-red-700 dark:text-red-400">{error}</span>
        </div>
      )}

      {!loading && !error && result && (
        <>
          {result.analyses && result.analyses.length > 0
            ? (
              <div className="space-y-8">
                {result.analyses.map((analysis, i) => (
                  <div key={i}>
                    {i > 0 && <div className="border-t border-border" />}
                    {analysis.type === "us_estate_tax_sunset" && <USAnalysisPanel analysis={analysis} />}
                    {analysis.type === "uk_iht_pension_inclusion" && <UKAnalysisPanel analysis={analysis} />}
                    {(analysis.type === "uk_bpr_apr_cap" || analysis.type === "uk_fca_targeted_support") && (
                      <GenericAnalysisPanel analysis={analysis} />
                    )}
                  </div>
                ))}
              </div>
            )
            : <NoCountdownPanel result={result} />
          }
        </>
      )}

      {/* Info footer */}
      <div className="flex items-start gap-2 p-3 rounded-lg bg-surface border border-border">
        <Info size={13} className="text-ink-muted mt-0.5 flex-shrink-0" />
        <p className="text-xs text-ink-muted">
          Jurisdiction is set per-household in the household values. Firm-level default is configured in Admin → Firm settings.
          US and UK analyses reflect 2024/25 tax law; always verify with a qualified adviser before client conversations.
        </p>
      </div>
    </div>
  );
}
