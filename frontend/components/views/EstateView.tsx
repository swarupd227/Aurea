"use client";

import { Card, Empty, ErrorState, SkeletonList } from "@/components/ui";
import { useApi } from "@/lib/hooks";
import { money, pct, titleCase } from "@/lib/format";
import { ViewLink } from "./ViewNav";
import { AllClients, ViewHeader } from "./ViewHeader";

const ROUTE = "/studio/estate";

const CLASS_LABELS: Record<string, string> = {
  equity: "Public equity",
  fixed_income: "Fixed income",
  alternatives: "Alternatives / PE",
  property: "Property",
  cash: "Cash",
  commodity: "Commodities",
  multi_asset: "Multi-asset",
};

const RISK_TONE: Record<string, string> = { high: "text-crit", moderate: "text-warn", low: "text-ok" };
const SEV_TONE: Record<string, string> = { high: "text-crit", medium: "text-warn", low: "text-ink-muted" };
const READY_TONE: Record<string, string> = { ready: "text-ok", developing: "text-warn", early: "text-ink-muted" };
const READY_TEXT: Record<string, string> = { ready: "Ready", developing: "Developing", early: "Early stage" };

const scoreTone = (s: number, hi: number, mid: number) => (s >= hi ? "bg-ok" : s >= mid ? "bg-warn" : "bg-crit");

function Bar({ value, tone }: { value: number; tone: string }) {
  return (
    <div className="h-1.5 w-full overflow-hidden rounded-full bg-border-soft">
      <div className={`h-full rounded-full ${tone}`} style={{ width: `${Math.max(0, Math.min(100, value))}%` }} />
    </div>
  );
}

/** Estate and succession for a household: what the estate holds, the gaps, its trusts and heirs.
 * With no household, a picker of every client. */
export function EstateView({ household = null }: { household?: string | null }) {
  return household ? <One id={household} /> : <Picker />;
}

function Picker() {
  const { data, loading, error, refetch } = useApi<any[]>("/api/core/households");
  if (error) return <div className="view-container"><Card><ErrorState what="households" message={error} onRetry={refetch} /></Card></div>;
  if (loading || !data) return <div className="view-container"><SkeletonList count={4} /></div>;
  return (
    <div className="view-container">
      <ViewHeader title="Estate & succession" meta={<span className="chip text-ink-muted">{data.length} households</span>} />
      {data.length === 0 ? <Empty>No households.</Empty> : (
        <Card className="overflow-hidden p-0">
          <ul className="divide-y divide-border-soft" data-testid="estate-households">
            {data.map((h: any) => (
              <li key={h.id}>
                <ViewLink href={`${ROUTE}?household=${h.id}`} data-testid="estate-household"
                  className="flex flex-wrap items-center justify-between gap-2 px-4 py-2.5 hover:bg-border-soft">
                  <span className="min-w-0 flex-1 truncate text-sm text-ink">{h.name}</span>
                  {h.segment && <span className="chip text-ink-muted">{titleCase(h.segment)}</span>}
                  <span className="font-mono text-sm tabular-nums text-ink-muted">{money(h.total_value)}</span>
                </ViewLink>
              </li>
            ))}
          </ul>
        </Card>
      )}
    </div>
  );
}

function One({ id }: { id: string }) {
  const path = `/api/core/households/${id}/estate`;
  const { data, loading, error, refetch } = useApi<any>(path, [path]);
  if (error) return <div className="view-container"><Card><ErrorState what="the estate" message={error} onRetry={refetch} /></Card></div>;
  if (loading || !data) return <div className="view-container"><SkeletonList count={4} /></div>;

  const risk = data.succession_risk ?? {};
  const gaps: any[] = data.succession_gaps ?? [];
  const trusts: any[] = data.trust_structures ?? [];
  const heirs: any[] = data.heir_readiness ?? [];
  const classes = Object.entries(data.wealth_breakdown ?? {}) as [string, any][];
  const step = data.step_up_basis;

  return (
    <div className="view-container">
      <ViewHeader
        title={`Estate · ${data.household_name}`}
        meta={<>
          <span className={`chip ${RISK_TONE[risk.level] ?? "text-ink-muted"}`} data-testid="estate-risk">{titleCase(risk.level ?? "—")} risk · {risk.score ?? "—"}/100</span>
          {data.generated_at && <span className="chip text-ink-muted">as of {data.generated_at}</span>}
        </>}
        actions={<AllClients href={ROUTE} />}
      />

      <div className="mb-4 grid grid-cols-2 gap-3 cq-md:grid-cols-4" data-testid="estate-summary">
        {[
          ["Estate value", money(data.total_estate_value), "text-ink"],
          ["Gaps", gaps.length, gaps.length ? "text-warn" : "text-ok"],
          ["High severity", risk.high_severity_count ?? 0, risk.high_severity_count ? "text-crit" : "text-ink-muted"],
          ["Trusts · heirs", `${trusts.length} · ${heirs.length}`, "text-ink"],
        ].map(([label, v, tone]: any) => (
          <Card key={label}>
            <div className="tile-label">{label}</div>
            <div className={`font-mono text-xl tabular-nums ${tone}`}>{v}</div>
          </Card>
        ))}
      </div>

      <h2 className="mb-2 text-sm font-medium text-ink">Wealth breakdown</h2>
      {classes.length === 0 ? <Empty>Nothing held.</Empty> : (
        <Card className="mb-4" data-testid="estate-breakdown">
          <div className="space-y-2">
            {classes.map(([cls, info]) => (
              <div key={cls}>
                <div className="flex items-baseline justify-between gap-2 text-sm">
                  <span className="truncate text-ink-muted">{CLASS_LABELS[cls] ?? titleCase(cls)}</span>
                  <span className="whitespace-nowrap font-mono tabular-nums text-ink">
                    {money(info.value)} <span className="text-xs text-ink-muted">{pct(info.pct, 0)}</span>
                  </span>
                </div>
                <Bar value={(info.pct ?? 0) * 100} tone="bg-ink-muted" />
              </div>
            ))}
          </div>
        </Card>
      )}

      {step && (
        <Card className="mb-4" data-testid="estate-step-up">
          <div className="mb-2 flex flex-wrap gap-1.5"><span className="chip text-ink-muted">IRC §1014 step-up in basis</span></div>
          <div className="grid grid-cols-2 gap-3">
            <div>
              <div className="tile-label">Unrealised gain</div>
              <div className="font-mono text-lg tabular-nums text-ink">{money(step.unrealised_gain)}</div>
            </div>
            <div>
              <div className="tile-label">Step-up at death</div>
              <div className="font-mono text-lg tabular-nums text-ok">{money(step.step_up_benefit)}</div>
            </div>
          </div>
        </Card>
      )}

      <h2 className="mb-2 text-sm font-medium text-ink">Succession gaps</h2>
      {gaps.length === 0 ? <Empty>No gaps found.</Empty> : (
        <Card className="mb-4 overflow-hidden p-0">
          <ul className="divide-y divide-border-soft" data-testid="estate-gaps">
            {gaps.map((g: any, i: number) => (
              <li key={i} className="flex items-start justify-between gap-2 px-4 py-2" title={g.detail}>
                <span className="min-w-0 text-sm text-ink">{g.title}</span>
                <span className={`chip shrink-0 ${SEV_TONE[g.severity] ?? "text-ink-muted"}`}>{titleCase(g.severity)}</span>
              </li>
            ))}
          </ul>
        </Card>
      )}

      <h2 className="mb-2 text-sm font-medium text-ink">Trusts and legal structures</h2>
      {trusts.length === 0 ? <Empty>No trust or entity on file.</Empty> : (
        <div className="mb-4 space-y-3" data-testid="estate-trusts">
          {trusts.map((t: any) => (
            <Card key={t.id}>
              <div className="flex items-start justify-between gap-2">
                <div className="min-w-0">
                  <div className="truncate text-sm text-ink">{t.name}</div>
                  <div className="text-xs text-ink-muted">{titleCase(t.entity_type ?? "")}{t.jurisdiction ? ` · ${t.jurisdiction}` : ""}</div>
                </div>
                <div className="shrink-0 text-right">
                  <div className="font-mono text-sm tabular-nums text-ink">{t.governance_score}/100</div>
                  <div className="text-[10px] uppercase tracking-wide text-ink-muted">governance on file</div>
                </div>
              </div>
              <div className="mt-2"><Bar value={t.governance_score} tone={scoreTone(t.governance_score, 75, 50)} /></div>
              <div className="mt-2 flex flex-wrap gap-1.5">
                <span className={`chip ${t.settlor ? "text-ink-muted" : "text-warn"}`}>settlor {t.settlor ?? "not on file"}</span>
                <span className={`chip ${t.trustees?.length ? "text-ink-muted" : "text-warn"}`}>
                  trustees {t.trustees?.length ? t.trustees.join(", ") : "not on file"}
                </span>
                <span className={`chip ${t.beneficiaries_count ? "text-ink-muted" : "text-warn"}`}>beneficiaries {t.beneficiaries_count ?? 0}</span>
                {t.established && <span className="chip text-ink-muted">est. {t.established}</span>}
                {t.review_date && <span className="chip text-ink-muted">reviewed {t.review_date}</span>}
              </div>
            </Card>
          ))}
        </div>
      )}

      <h2 className="mb-2 text-sm font-medium text-ink">Next-generation heirs</h2>
      {heirs.length === 0 ? <Empty>No next-generation heir on file.</Empty> : (
        <div className="space-y-3" data-testid="estate-heirs">
          {heirs.map((h: any) => (
            <Card key={h.id}>
              <div className="flex items-center justify-between gap-2">
                <div className="min-w-0 truncate text-sm text-ink">{h.name}</div>
                <div className="flex shrink-0 items-center gap-1.5">
                  <span className="font-mono text-sm tabular-nums text-ink">{h.readiness_score}/100</span>
                  <span className={`chip ${READY_TONE[h.readiness_level] ?? "text-ink-muted"}`}>{READY_TEXT[h.readiness_level] ?? h.readiness_level}</span>
                </div>
              </div>
              <div className="mt-2"><Bar value={h.readiness_score} tone={scoreTone(h.readiness_score, 70, 40)} /></div>
              <div className="mt-2 flex flex-wrap gap-1.5">
                <span className={`chip ${h.has_mandate ? "text-ok" : "text-ink-muted"}`}>{h.has_mandate ? "✓" : "○"} mandate</span>
                <span className={`chip ${h.has_goals ? "text-ok" : "text-ink-muted"}`}>{h.has_goals ? "✓" : "○"} goals</span>
                <span className={`chip ${h.has_profile ? "text-ok" : "text-ink-muted"}`}>{h.has_profile ? "✓" : "○"} risk profile</span>
              </div>
            </Card>
          ))}
        </div>
      )}
    </div>
  );
}
