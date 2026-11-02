"use client";

import { Card, Empty, ErrorState, SkeletonList } from "@/components/ui";
import { money } from "@/lib/format";
import { useApi } from "@/lib/hooks";
import {
  COUNTDOWN_LABEL, CountdownAnalysis, CountdownHousehold, URGENCY_LABEL, URGENCY_TONE, countdown, extraTax,
} from "@/app/workspace/components/artifacts/RegulatoryCountdownCard";
import { HouseholdPicker } from "./BehaviouralView";
import { ViewLink } from "./ViewNav";
import { AllClients, ViewHeader } from "./ViewHeader";

const ROUTE = "/studio/regulatory-countdown";

function Tile({ label, value, sub, tone = "text-ink" }: { label: string; value: React.ReactNode; sub?: React.ReactNode; tone?: string }) {
  return (
    <div className="rounded-lg border border-border-soft px-3 py-2">
      <div className="tile-label">{label}</div>
      <div className={`font-mono text-lg tabular-nums ${tone}`}>{value}</div>
      {sub != null && <div className="mt-0.5 text-xs text-ink-muted">{sub}</div>}
    </div>
  );
}

function Lines({ rows, cur }: { rows: [string, number | null | undefined, string?][]; cur: string }) {
  return (
    <div className="divide-y divide-border-soft text-sm">
      {rows.map(([label, v, tone]) => (
        <div key={label} className="flex items-center justify-between gap-3 py-1.5">
          <span className={tone ?? "text-ink-muted"}>{label}</span>
          <span className={`font-mono tabular-nums ${tone ?? "text-ink"}`}>{v ? money(v, cur) : "—"}</span>
        </div>
      ))}
    </div>
  );
}

const amount = (v: number, cur: string) => (v > 0 ? money(v, cur) : "none");

function Figures({ a }: { a: CountdownAnalysis }) {
  const f = a.figures;
  const cur = a.currency;
  if (a.type === "us_estate_tax_sunset") {
    return (
      <>
        <div className="mb-3 grid grid-cols-2 gap-2 cq-md:grid-cols-4">
          <Tile label="Gross estate" value={money(f.gross_estate, cur)} sub={f.married ? "couple" : "individual"} />
          <Tile label="Exemption now" value={money(f.post_sunset_exemption, cur)} sub={f.married ? "combined" : undefined} />
          <Tile label="Exposure" value={amount(f.exposure_post_sunset, cur)} tone={f.exposure_post_sunset > 0 ? "text-warn" : "text-ok"} />
          <Tile label="Additional tax at risk" value={amount(f.additional_tax_at_risk, cur)} tone={f.additional_tax_at_risk > 0 ? "text-crit" : "text-ok"} />
        </div>
        <div className="tile-label mb-1">Estate</div>
        <Lines cur={cur} rows={[
          ["Managed portfolio", f.portfolio],
          ["Held away", f.held_away],
          ["Retirement accounts", f.retirement_accounts],
          ["Real estate", f.real_estate],
          ["Gross estate", f.gross_estate, "text-ink"],
          ["Exemption before sunset", f.current_exemption],
          ["Exemption after sunset", f.post_sunset_exemption],
        ]} />
      </>
    );
  }
  if (a.type === "uk_iht_pension_inclusion") {
    return (
      <>
        <div className="mb-3 grid grid-cols-2 gap-2 cq-md:grid-cols-4">
          <Tile label="Pension pots" value={money(f.pension_total, cur)} />
          <Tile label="Threshold" value={money(f.threshold, cur)} sub={f.married ? "NRB + RNRB · couple" : "NRB + RNRB"} />
          <Tile label="IHT now" value={money(f.iht_current, cur)} />
          <Tile label="Additional IHT" value={amount(f.additional_iht, cur)} tone={f.additional_iht > 0 ? "text-crit" : "text-ok"} />
        </div>
        <div className="mb-3 grid grid-cols-2 gap-2">
          <Tile label="Estate now" value={money(f.current_estate, cur)} sub={`IHT ${money(f.iht_current, cur)}`} />
          <Tile label={`Estate from ${a.deadline}`} value={money(f.post_2027_estate, cur)} sub={`IHT ${money(f.iht_post_2027, cur)}`} tone={f.additional_iht > 0 ? "text-crit" : "text-ink"} />
        </div>
        <Lines cur={cur} rows={[
          ["Managed portfolio", f.portfolio],
          ["ISA holdings", f.isa_total],
          ["Pension pots", f.pension_total, f.pension_total > 0 ? "text-crit" : undefined],
          ["Nil-rate band", f.nrb_total],
          ["Residence nil-rate band", f.rnrb_claimable],
        ]} />
        {Array.isArray(f.lpa_missing) && f.lpa_missing.length > 0 && (
          <div className="mt-3 flex flex-wrap gap-1.5" data-testid="countdown-lpa-missing">
            {f.lpa_missing.map((n: string) => <span key={n} className="chip bg-warn-bg text-warn">no LPA · {n}</span>)}
          </div>
        )}
      </>
    );
  }
  if (a.type === "uk_bpr_apr_cap") {
    return (
      <div className="mb-1 grid grid-cols-2 gap-2 cq-md:grid-cols-4">
        <Tile label="Qualifying assets" value={money(f.estimated_qualifying_assets, cur)} sub="estimate · alternatives as proxy" />
        <Tile label="Cap per person" value={money(f.cap, cur)} />
        <Tile label="Above cap" value={amount(f.cap_exposure, cur)} tone={f.cap_exposure > 0 ? "text-warn" : "text-ok"} />
        <Tile label="Additional IHT" value={amount(f.estimated_additional_iht, cur)} sub="estimate" tone={f.estimated_additional_iht > 0 ? "text-crit" : "text-ok"} />
      </div>
    );
  }
  if (a.type === "uk_fca_targeted_support") {
    return (
      <div className="mb-1 grid grid-cols-2 gap-2">
        <Tile label="Household portfolio" value={money(f.total_portfolio_value, cur)} />
        <Tile label="People in household" value={f.client_count} />
      </div>
    );
  }
  return null;
}

function Analysis({ a }: { a: CountdownAnalysis }) {
  return (
    <Card className="mb-4" data-testid="countdown-analysis">
      <div className="mb-3 flex flex-wrap items-start justify-between gap-3">
        <div className="min-w-0 flex-1">
          <div className="text-sm font-medium text-ink">{a.title}</div>
          <div className="mt-0.5 text-xs text-ink-muted">{a.regulation}</div>
        </div>
        <div className="text-right">
          <div className={`font-mono text-2xl tabular-nums ${URGENCY_TONE[a.urgency] ?? "text-ink"}`}>{a.days_remaining > 0 ? a.days_remaining : "in force"}</div>
          <div className="font-mono text-xs tabular-nums text-ink-muted">{a.days_remaining > 0 ? `days to ${a.deadline}` : `since ${a.deadline}`}</div>
        </div>
      </div>
      <div className="mb-3 flex flex-wrap gap-1.5">
        <span className={`chip bg-border-soft ${URGENCY_TONE[a.urgency] ?? "text-ink-muted"}`}>{URGENCY_LABEL[a.urgency] ?? a.urgency}</span>
        {a.figures?.affected != null && (
          <span className={`chip bg-border-soft ${a.figures.affected ? "text-crit" : "text-ok"}`}>{a.figures.affected ? "affected" : "not affected"}</span>
        )}
        <span className="chip bg-border-soft text-ink-muted">{a.currency}</span>
      </div>
      <Figures a={a} />
      {a.actions.length > 0 && (
        <div className="mt-4">
          <div className="tile-label mb-1">Planning actions</div>
          <ol className="divide-y divide-border-soft" data-testid="countdown-actions">
            {a.actions.map((x, i) => (
              <li key={i}>
                <details>
                  <summary className="flex cursor-pointer items-center gap-2 py-1.5 text-sm text-ink">
                    <span className="w-5 shrink-0 font-mono text-xs tabular-nums text-ink-muted">{x.priority}</span>
                    <span className="min-w-0 flex-1">{x.action}</span>
                  </summary>
                  <ul className="mb-2 ml-7 space-y-1 text-xs leading-relaxed">
                    <li className="text-ink-soft">{x.detail}</li>
                    <li className="text-ok">{x.estimated_benefit}</li>
                  </ul>
                </details>
              </li>
            ))}
          </ol>
        </div>
      )}
    </Card>
  );
}

function FirmWide() {
  const { data, loading, error, refetch } = useApi<{ results: CountdownHousehold[]; total: number }>("/api/core/regulatory-countdown/firm");
  if (error) return <Card className="mb-4"><ErrorState what="the firm's countdowns" message={error} onRetry={refetch} /></Card>;
  if (loading || !data) return <div className="mb-4"><SkeletonList count={3} /></div>;
  return (
    <>
      <h2 className="mb-2 flex items-center gap-2 text-sm font-medium text-ink">
        Tracked deadlines <span className="chip bg-border-soft text-ink-muted">{data.total}</span>
      </h2>
      {data.results.length === 0 ? <Card className="mb-4"><Empty>No household has a tracked deadline.</Empty></Card> : (
        <Card className="mb-4 overflow-hidden p-0">
          <div className="divide-y divide-border-soft" data-testid="countdown-firm">
            {data.results.map((h) => (
              <div key={h.household_id} className="px-4 py-2">
                <div className="flex items-center gap-2">
                  <ViewLink href={`${ROUTE}?household=${h.household_id}`} className="truncate text-sm text-ink hover:underline">{h.household_name}</ViewLink>
                  <span className="chip bg-border-soft text-ink-muted">{h.jurisdiction}</span>
                </div>
                <div className="mt-1 flex flex-wrap gap-1.5">
                  {h.analyses.map((a, i) => {
                    const extra = extraTax(a);
                    return (
                      <span key={i} className={`chip bg-border-soft font-mono ${URGENCY_TONE[a.urgency] ?? "text-ink-muted"}`}>
                        {COUNTDOWN_LABEL[a.type] ?? a.title} · {countdown(a)}{extra ? ` · +${money(extra, a.currency)}` : ""}
                      </span>
                    );
                  })}
                </div>
              </div>
            ))}
          </div>
        </Card>
      )}
    </>
  );
}

/** The dated rule changes a household's market faces, with what each does to its figures. With no
 * household, every household that has one across the firm, and whom else to open. */
export function RegulatoryCountdownView({ household = null }: { household?: string | null }) {
  const { data, loading, error, refetch } = useApi<CountdownHousehold>(
    household ? `/api/core/households/${household}/regulatory-countdown` : null, [household]);

  if (!household) {
    return (
      <div className="view-container">
        <ViewHeader title="Regulatory countdown" />
        <FirmWide />
        <h2 className="mb-2 text-sm font-medium text-ink">All households</h2>
        <HouseholdPicker route={ROUTE} testId="countdown-clients" />
      </div>
    );
  }
  if (error) return <div className="view-container"><Card><ErrorState what="the regulatory countdown" message={error} onRetry={refetch} /></Card></div>;
  // Moving between clients, the last response is still here for a render.
  if (loading || !data || data.household_id !== household) return <div className="view-container"><SkeletonList count={3} /></div>;

  return (
    <div className="view-container">
      <ViewHeader
        title={`Regulatory countdown · ${data.household_name}`}
        meta={<>
          <span className="chip bg-border-soft text-ink-soft">{data.jurisdiction}</span>
          <span className="chip bg-border-soft text-ink-muted">{data.analyses.length} tracked</span>
        </>}
        actions={<AllClients href={ROUTE} />}
      />
      {data.analyses.length === 0
        ? <Card><Empty>No deadline tracked for {data.jurisdiction}.</Empty></Card>
        : data.analyses.map((a, i) => <Analysis key={i} a={a} />)}
    </div>
  );
}
