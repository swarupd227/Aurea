"use client";
import { useState } from "react";
import {
  Landmark, AlertTriangle, CheckCircle2, Clock, Shield, Users,
  ChevronDown, ChevronUp, TrendingUp, Building2, ArrowRight,
} from "lucide-react";
import PageHeader from "@/components/PageHeader";
import { Card, Spinner, SeverityBadge, Empty } from "@/components/ui";
import { useApi } from "@/lib/hooks";
import { money, pct } from "@/lib/format";

// ── helpers ──────────────────────────────────────────────────────────────────

function RiskBadge({ level }: { level: string }) {
  const styles: Record<string, string> = {
    high: "bg-critical/10 text-critical border border-critical/20",
    moderate: "bg-caution/10 text-caution border border-caution/20",
    low: "bg-positive/10 text-positive border border-positive/20",
  };
  const labels: Record<string, string> = {
    high: "High Risk",
    moderate: "Moderate Risk",
    low: "Low Risk",
  };
  return (
    <span className={`inline-flex items-center gap-1.5 px-2.5 py-1 rounded-full text-xs font-semibold ${styles[level] || styles.moderate}`}>
      {level === "high" ? <AlertTriangle className="h-3 w-3" /> : level === "low" ? <CheckCircle2 className="h-3 w-3" /> : <Clock className="h-3 w-3" />}
      {labels[level] || level}
    </span>
  );
}

function ReadinessBadge({ level }: { level: string }) {
  const styles: Record<string, string> = {
    ready: "bg-positive/10 text-positive",
    developing: "bg-caution/10 text-caution",
    early: "bg-navy-100 text-navy-600",
  };
  return (
    <span className={`chip ${styles[level] || styles.early}`}>
      {level === "ready" ? "Ready" : level === "developing" ? "Developing" : "Early stage"}
    </span>
  );
}

function WealthBar({ label, value, total, color }: { label: string; value: number; total: number; color: string }) {
  const pctVal = total > 0 ? (value / total) * 100 : 0;
  return (
    <div className="flex items-center gap-3 py-1">
      <div className="w-32 text-sm text-ink-muted shrink-0">{label}</div>
      <div className="flex-1 h-2 bg-navy-100 rounded-full overflow-hidden">
        <div className={`h-full rounded-full ${color}`} style={{ width: `${pctVal}%` }} />
      </div>
      <div className="w-20 text-right text-sm font-medium text-ink tabular-nums">{money(value)}</div>
      <div className="w-10 text-right text-xs text-ink-muted tabular-nums">{pct(pctVal / 100, 0)}</div>
    </div>
  );
}

const CLASS_COLORS: Record<string, string> = {
  equity: "bg-navy-600",
  fixed_income: "bg-navy-300",
  alternatives: "bg-gold-600",
  property: "bg-emerald-500",
  cash: "bg-navy-200",
  commodity: "bg-amber-400",
  multi_asset: "bg-slate-400",
};

const CLASS_LABELS: Record<string, string> = {
  equity: "Public equity",
  fixed_income: "Fixed income",
  alternatives: "Alternatives / PE",
  property: "Property",
  cash: "Cash",
  commodity: "Commodities",
  multi_asset: "Multi-asset",
};

// ── Household estate card ─────────────────────────────────────────────────────

function HouseholdEstateCard({ household }: { household: any }) {
  const { data, loading } = useApi<any>(`/api/core/households/${household.id}/estate`);
  const [expanded, setExpanded] = useState(false);

  if (loading) return (
    <Card className="mb-4">
      <div className="flex items-center gap-2 text-sm text-ink-muted py-2">
        <span className="h-3 w-3 rounded-full border border-navy-300 border-t-navy-700 animate-spin" />
        Analysing {household.name}…
      </div>
    </Card>
  );

  if (!data) return null;

  const { succession_risk, succession_gaps, trust_structures, heir_readiness, wealth_breakdown, total_estate_value } = data;
  const highGaps = (succession_gaps || []).filter((g: any) => g.severity === "high");

  return (
    <Card className="mb-4">
      {/* Header */}
      <div className="flex items-start justify-between gap-4">
        <div className="flex-1 min-w-0">
          <div className="flex items-center gap-2 flex-wrap">
            <h3 className="text-base font-semibold text-ink">{data.household_name}</h3>
            <RiskBadge level={succession_risk?.level || "low"} />
          </div>
          <p className="text-sm text-ink-muted mt-0.5">
            Total estate value: {money(total_estate_value)}
            {succession_gaps?.length > 0 && (
              <span className="ml-2">· {succession_gaps.length} gap{succession_gaps.length !== 1 ? "s" : ""} identified</span>
            )}
          </p>
        </div>
        <button
          onClick={() => setExpanded(!expanded)}
          className="flex items-center gap-1 text-sm text-navy-700 hover:text-navy-900 shrink-0"
        >
          {expanded ? <ChevronUp className="h-4 w-4" /> : <ChevronDown className="h-4 w-4" />}
          {expanded ? "Less" : "Detail"}
        </button>
      </div>

      {/* Quick gap chips */}
      {highGaps.length > 0 && (
        <div className="flex flex-wrap gap-1.5 mt-3">
          {highGaps.map((g: any, i: number) => (
            <span key={i} className="inline-flex items-center gap-1 px-2 py-0.5 rounded-full bg-critical/8 text-critical text-xs font-medium">
              <AlertTriangle className="h-3 w-3" /> {g.title}
            </span>
          ))}
        </div>
      )}

      {/* Expanded detail */}
      {expanded && (
        <div className="mt-5 space-y-6 border-t border-navy-100 pt-5">

          {/* Wealth breakdown */}
          {Object.keys(wealth_breakdown || {}).length > 0 && (
            <div>
              <h4 className="text-sm font-semibold text-ink mb-3 flex items-center gap-1.5">
                <TrendingUp className="h-4 w-4 text-navy-400" /> Wealth breakdown
              </h4>
              <div className="space-y-0.5">
                {Object.entries(wealth_breakdown).map(([cls, info]: [string, any]) => (
                  <WealthBar
                    key={cls}
                    label={CLASS_LABELS[cls] || cls}
                    value={info.value}
                    total={total_estate_value}
                    color={CLASS_COLORS[cls] || "bg-navy-400"}
                  />
                ))}
              </div>
            </div>
          )}

          {/* Succession gaps */}
          {succession_gaps?.length > 0 && (
            <div>
              <h4 className="text-sm font-semibold text-ink mb-3 flex items-center gap-1.5">
                <AlertTriangle className="h-4 w-4 text-caution" /> Succession gaps
              </h4>
              <div className="space-y-2">
                {succession_gaps.map((gap: any, i: number) => (
                  <div key={i} className="rounded-lg border border-navy-100 p-3 bg-surface">
                    <div className="flex items-start justify-between gap-2">
                      <span className="text-sm font-medium text-ink">{gap.title}</span>
                      <SeverityBadge severity={gap.severity} />
                    </div>
                    <p className="text-xs text-ink-muted mt-1">{gap.detail}</p>
                    {gap.recommendation && (
                      <p className="text-xs text-navy-700 mt-1.5 flex items-start gap-1">
                        <ArrowRight className="h-3 w-3 mt-0.5 shrink-0 text-navy-400" />
                        {gap.recommendation}
                      </p>
                    )}
                  </div>
                ))}
              </div>
            </div>
          )}

          {/* Trust structures */}
          {trust_structures?.length > 0 && (
            <div>
              <h4 className="text-sm font-semibold text-ink mb-3 flex items-center gap-1.5">
                <Shield className="h-4 w-4 text-navy-400" /> Trust & legal structures
              </h4>
              <div className="space-y-3">
                {trust_structures.map((t: any) => (
                  <div key={t.id} className="rounded-lg border border-navy-100 p-3 bg-surface">
                    <div className="flex items-start justify-between gap-2">
                      <div>
                        <div className="text-sm font-medium text-ink">{t.name}</div>
                        <div className="text-xs text-ink-muted capitalize">{t.entity_type} · {t.jurisdiction}</div>
                      </div>
                      <div className="text-right shrink-0">
                        <div className="text-sm font-semibold text-ink">{t.governance_score}/100</div>
                        <div className="text-xs text-ink-muted">governance</div>
                      </div>
                    </div>
                    {/* Governance mini-bar */}
                    <div className="mt-2 h-1.5 w-full bg-navy-100 rounded-full overflow-hidden">
                      <div
                        className={`h-full rounded-full ${t.governance_score >= 75 ? "bg-positive" : t.governance_score >= 50 ? "bg-caution" : "bg-critical"}`}
                        style={{ width: `${t.governance_score}%` }}
                      />
                    </div>
                    <div className="mt-2 flex flex-wrap gap-x-4 gap-y-1 text-xs text-ink-muted">
                      {t.settlor && <span>Settlor: <span className="text-ink">{t.settlor}</span></span>}
                      {t.trustees?.length > 0 && <span>Trustees: <span className="text-ink">{t.trustees.join(", ")}</span></span>}
                      {t.beneficiaries_count > 0 && <span>{t.beneficiaries_count} beneficiar{t.beneficiaries_count === 1 ? "y" : "ies"}</span>}
                      {t.established && <span>Est. {t.established}</span>}
                    </div>
                    {t.compliance_note && (
                      <p className="text-xs text-navy-600 mt-1.5 bg-navy-50 rounded px-2 py-1">{t.compliance_note}</p>
                    )}
                  </div>
                ))}
              </div>
            </div>
          )}

          {/* Heir readiness */}
          {heir_readiness?.length > 0 && (
            <div>
              <h4 className="text-sm font-semibold text-ink mb-3 flex items-center gap-1.5">
                <Users className="h-4 w-4 text-navy-400" /> Heir readiness
              </h4>
              <div className="space-y-3">
                {heir_readiness.map((h: any) => (
                  <div key={h.id} className="rounded-lg border border-navy-100 p-3 bg-surface">
                    <div className="flex items-center justify-between gap-2">
                      <div>
                        <div className="text-sm font-medium text-ink">{h.name}</div>
                        <div className="text-xs text-ink-muted">Next-gen heir</div>
                      </div>
                      <ReadinessBadge level={h.readiness_level} />
                    </div>
                    <div className="mt-2 h-1.5 w-full bg-navy-100 rounded-full overflow-hidden">
                      <div
                        className={`h-full rounded-full ${h.readiness_score >= 70 ? "bg-positive" : h.readiness_score >= 40 ? "bg-caution" : "bg-navy-300"}`}
                        style={{ width: `${h.readiness_score}%` }}
                      />
                    </div>
                    <div className="mt-2 flex gap-3 text-xs">
                      <span className={h.has_mandate ? "text-positive" : "text-ink-muted"}>
                        {h.has_mandate ? "✓" : "○"} Mandate
                      </span>
                      <span className={h.has_goals ? "text-positive" : "text-ink-muted"}>
                        {h.has_goals ? "✓" : "○"} Goals
                      </span>
                      <span className={h.has_profile ? "text-positive" : "text-ink-muted"}>
                        {h.has_profile ? "✓" : "○"} Risk profile
                      </span>
                    </div>
                    {h.suggested_actions?.length > 0 && (
                      <div className="mt-2 space-y-0.5">
                        {h.suggested_actions.map((a: string, i: number) => (
                          <p key={i} className="text-xs text-navy-700 flex items-start gap-1">
                            <ArrowRight className="h-3 w-3 mt-0.5 shrink-0 text-navy-400" /> {a}
                          </p>
                        ))}
                      </div>
                    )}
                  </div>
                ))}
              </div>
            </div>
          )}
        </div>
      )}
    </Card>
  );
}

// ── Main page ─────────────────────────────────────────────────────────────────

export default function EstatePage() {
  const { data: households, loading } = useApi<any[]>("/api/core/households");

  if (loading || !households) return <Spinner label="Loading estate view…" />;

  const privateWealthHouseholds = households.filter(
    (h) => h.segment === "private_wealth" || h.total_value >= 500_000
  );

  return (
    <div>
      <PageHeader
        title="Estate & Succession"
        sub="Trust governance, wealth concentration, heir readiness, and NZ Trusts Act 2019 compliance — across the book."
      />

      {/* Summary tiles */}
      <div className="grid grid-cols-2 md:grid-cols-4 gap-3 mb-6">
        <div className="card p-4">
          <div className="tile-label">Households reviewed</div>
          <div className="text-2xl font-semibold text-ink mt-1">{privateWealthHouseholds.length}</div>
          <div className="text-xs text-ink-muted mt-1">Private wealth</div>
        </div>
        <div className="card p-4">
          <div className="tile-label">Agent</div>
          <div className="text-sm font-semibold text-ink mt-1">Estate & Succession</div>
          <div className="text-xs text-ink-muted mt-1">Tier 1 · Assistive</div>
        </div>
        <div className="card p-4">
          <div className="tile-label">Regulatory context</div>
          <div className="text-sm font-semibold text-ink mt-1">NZ Trusts Act 2019</div>
          <div className="text-xs text-ink-muted mt-1">§23 review obligations</div>
        </div>
        <div className="card p-4">
          <div className="tile-label">Estate memo</div>
          <div className="text-sm font-semibold text-ink mt-1">Run agent</div>
          <div className="text-xs text-ink-muted mt-1">Via Workforce → Estate</div>
        </div>
      </div>

      {/* Per-household cards */}
      {privateWealthHouseholds.length === 0 ? (
        <Empty icon={<Landmark className="h-8 w-8" />}>
          No private wealth households found.
        </Empty>
      ) : (
        <div>
          <p className="text-xs text-ink-muted mb-4">
            Click <strong>Detail</strong> on any household to expand the full estate analysis — wealth breakdown, succession gaps, trust governance, and heir readiness.
          </p>
          {privateWealthHouseholds.map((hh: any) => (
            <HouseholdEstateCard key={hh.id} household={hh} />
          ))}
        </div>
      )}
    </div>
  );
}
