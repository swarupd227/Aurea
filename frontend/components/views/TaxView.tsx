"use client";

import { useState } from "react";
import type { ReactNode } from "react";
import { Card, Empty, ErrorState, SkeletonList } from "@/components/ui";
import { useApi } from "@/lib/hooks";
import { firmMarket, money } from "@/lib/format";
import { TaxPositionCard } from "@/app/workspace/components/artifacts/TaxPositionCard";
import { ViewLink } from "./ViewNav";
import { AllClients, ViewHeader } from "./ViewHeader";

/** Tax intelligence, read beside the conversation: the firm's tax book, or one household's report in
 * its own market (NZ, US, UK or India). Reads only. Every figure is an estimate for a qualified person
 * to check; the reasoning behind a finding is the agent's to say, so each sits in a tooltip here. */
export function TaxView({ household = null }: { household?: string | null }) {
  return household ? <HouseholdTax key={household} id={household} /> : <TaxBook />;
}

// ── Shared pieces ──────────────────────────────────────────────────────────────────────────────

const JURISDICTION: Record<string, string> = {
  US: "US · SEC/FINRA",
  UK: "UK · FCA",
  NZ: "NZ · FMA",
  IN: "IN · Income-tax Act 2025",
};

const pctFig = (n: number | null | undefined) => (n == null ? "—" : `${Number(n).toFixed(1)}%`);
const words = (s: string | null | undefined) => (s ?? "").replace(/_/g, " ");
const qty = (n: number) => Number(n).toLocaleString();

function Chip({ children, tone = "text-ink-muted", title, testId }: { children: ReactNode; tone?: string; title?: string; testId?: string }) {
  return <span className={`chip bg-border-soft ${tone}`} title={title} data-testid={testId}>{children}</span>;
}

function Tiles({ children, testId }: { children: ReactNode; testId?: string }) {
  return <div className="mb-4 grid grid-cols-2 gap-3 cq-md:grid-cols-4" data-testid={testId}>{children}</div>;
}

function Tile({ label, value, caption, tone = "text-ink" }: { label: string; value: ReactNode; caption?: ReactNode; tone?: string }) {
  return (
    <div className="card min-w-0 p-4">
      <div className="tile-label truncate">{label}</div>
      <div className={`mt-1 truncate font-mono text-xl tabular-nums ${tone}`}>{value}</div>
      {caption && <div className="mt-0.5 truncate text-xs text-ink-muted">{caption}</div>}
    </div>
  );
}

function Section({ title, count, chips, note, testId, children }: {
  title: string; count?: number; chips?: ReactNode; note?: string; testId?: string; children: ReactNode;
}) {
  return (
    <div className="card mb-4 min-w-0 overflow-hidden" data-testid={testId}>
      <div className="flex flex-wrap items-center gap-1.5 border-b border-border-soft px-4 py-2.5">
        <h2 className="mr-auto text-sm font-medium text-ink" title={note}>
          {title}
          {count !== undefined && <span className="ml-2 font-mono text-xs tabular-nums text-ink-muted">{count}</span>}
        </h2>
        {chips}
      </div>
      {children}
    </div>
  );
}

function None({ children }: { children: ReactNode }) {
  return <div className="px-4 py-5 text-center text-xs text-ink-muted">{children}</div>;
}

function Sub({ children }: { children: ReactNode }) {
  return <div className="border-b border-border-soft px-4 pb-1.5 pt-3 text-[10px] font-medium uppercase tracking-wide text-ink-muted">{children}</div>;
}

/** One row of a list: who or what on the left, the figure on the right. */
function Row({ title, sub, chips, figure, caption, figureTone = "text-ink", hint }: {
  title: ReactNode; sub?: ReactNode; chips?: ReactNode; figure?: ReactNode; caption?: ReactNode; figureTone?: string; hint?: string;
}) {
  return (
    <div className="flex items-start justify-between gap-3 px-4 py-2.5" title={hint}>
      <div className="min-w-0">
        <div className="truncate text-sm text-ink">{title}</div>
        {sub && <div className="truncate text-xs text-ink-muted">{sub}</div>}
        {chips && <div className="mt-1 flex flex-wrap gap-1">{chips}</div>}
      </div>
      {(figure !== undefined || caption) && (
        <div className="shrink-0 text-right">
          {figure !== undefined && <div className={`font-mono text-sm tabular-nums ${figureTone}`}>{figure}</div>}
          {caption && <div className="text-xs text-ink-muted">{caption}</div>}
        </div>
      )}
    </div>
  );
}

function Rows({ children }: { children: ReactNode }) {
  return <div className="divide-y divide-border-soft">{children}</div>;
}

const householdHref = (id: string) => `/studio/tax?household=${id}`;

interface Lot {
  symbol: string; instrument_name: string; account_name: string; mandate_name: string;
  quantity: number; cost_per_unit: number; current_price: number; unrealised_gain?: number;
  unrealised_loss?: number; holding_days: number; wash_sale_risk?: boolean;
  household_id?: string; household_name?: string;
}

/** Lots with their gain or loss. At the pane's width: instrument, amount and days held; quantity,
 * cost and price join as the space allows. */
function Lots({ lots, ccy, gain = false, washSale = false }: { lots: Lot[]; ccy: string; gain?: boolean; washSale?: boolean }) {
  if (!lots.length) return <None>None identified</None>;
  return (
    <table className="w-full text-sm" data-testid="tax-lots">
      <thead>
        <tr className="text-left text-[10px] uppercase tracking-wide text-ink-muted">
          <th className="px-4 py-2 font-medium">Instrument</th>
          <th className="hidden px-2 py-2 text-right font-medium cq-sm:table-cell">Qty</th>
          <th className="hidden px-2 py-2 text-right font-medium cq-md:table-cell">Cost</th>
          <th className="hidden px-2 py-2 text-right font-medium cq-md:table-cell">Price</th>
          <th className="px-2 py-2 text-right font-medium">{gain ? "Unrealised gain" : "Unrealised loss"}</th>
          <th className="px-4 py-2 text-right font-medium">Held</th>
        </tr>
      </thead>
      <tbody className="divide-y divide-border-soft">
        {lots.map((o, i) => {
          const amount = gain ? (o.unrealised_gain ?? 0) : (o.unrealised_loss ?? o.unrealised_gain ?? 0);
          return (
            <tr key={i}>
              <td className="max-w-0 px-4 py-2" title={o.instrument_name}>
                <div className="flex flex-wrap items-center gap-1">
                  <span className="font-mono text-xs text-ink">{o.symbol}</span>
                  {washSale && o.wash_sale_risk && <Chip tone="text-warn">wash sale</Chip>}
                </div>
                <div className="truncate text-xs text-ink-muted">
                  {o.household_id && o.household_name
                    ? <><ViewLink href={householdHref(o.household_id)} className="hover:underline">{o.household_name}</ViewLink> · </>
                    : null}
                  {[o.mandate_name, o.account_name].filter(Boolean).join(" · ")}
                </div>
              </td>
              <td className="hidden px-2 py-2 text-right font-mono text-xs tabular-nums text-ink-muted cq-sm:table-cell">{qty(o.quantity)}</td>
              <td className="hidden px-2 py-2 text-right font-mono text-xs tabular-nums text-ink-muted cq-md:table-cell">{money(o.cost_per_unit, ccy, 2)}</td>
              <td className="hidden px-2 py-2 text-right font-mono text-xs tabular-nums text-ink-muted cq-md:table-cell">{money(o.current_price, ccy, 2)}</td>
              <td className={`whitespace-nowrap px-2 py-2 text-right font-mono text-xs tabular-nums ${amount < 0 ? "text-crit" : "text-ok"}`}>{money(amount, ccy)}</td>
              <td className="whitespace-nowrap px-4 py-2 text-right font-mono text-xs tabular-nums text-ink-muted">{o.holding_days}d</td>
            </tr>
          );
        })}
      </tbody>
    </table>
  );
}

function Withdrawals({ data, ccy }: { data: { sequences: any[]; count: number; guidance?: string }; ccy: string }) {
  return (
    <Section title="Withdrawal sequencing" count={data.count} note={data.guidance} testId="tax-withdrawal">
      {!data.count ? <None>No mandates to sequence</None> : (
        <Rows>
          {data.sequences.map((s: any, i: number) => (
            <Row key={i} hint={s.rationale} title={s.mandate_name}
              chips={<><Chip tone={s.withdrawal_priority === 1 ? "text-ink" : "text-ink-muted"}>{s.priority_label}</Chip><Chip>{words(s.account_type)}</Chip></>}
              figure={money(s.value, ccy)} />
          ))}
        </Rows>
      )}
    </Section>
  );
}

function Shell({ children }: { children: ReactNode }) {
  return <div className="view-container">{children}</div>;
}

// ── The firm's book ────────────────────────────────────────────────────────────────────────────

function TaxBook() {
  const { data, loading, error, refetch } = useApi<any>("/api/analytics/tax-book");
  const refresh = <button className="btn-outline text-xs" onClick={refetch} disabled={loading} data-testid="tax-refresh">{loading ? "Scanning…" : "Refresh"}</button>;
  if (error) return <Shell><ViewHeader title="Tax intelligence" actions={refresh} /><Card><ErrorState what="the tax book" message={error} onRetry={refetch} /></Card></Shell>;
  if (loading || !data) return <Shell><ViewHeader title="Tax intelligence" /><SkeletonList count={4} /></Shell>;

  const india = firmMarket() === "IN";
  const j = data.jurisdictions ?? {};
  const meta = (
    <>
      <Chip>{data.total_households_scanned} households</Chip>
      {india
        ? <Chip>IN {j.IN ?? (data.in_households ?? []).length}</Chip>
        : ["NZ", "US", "UK", "IN"].filter((k) => (j[k] ?? 0) > 0).map((k) => <Chip key={k}>{k} {j[k]}</Chip>)}
      {(j.not_supported ?? 0) > 0 && <Chip tone="text-warn" testId="tax-book-unsupported">{j.not_supported} with no tax rules</Chip>}
      <Chip tone="text-warn">estimates</Chip>
      <Chip>as of {data.generated_at}</Chip>
    </>
  );

  return (
    <Shell>
      <ViewHeader title="Tax intelligence" meta={meta} actions={refresh} />
      {india ? <IndiaBook data={data} /> : <Book data={data} />}
      <HouseholdPicker />
    </Shell>
  );
}

function Book({ data }: { data: any }) {
  const nzd = (n: number) => money(n, "NZD");
  const lh = data.loss_harvest;
  const pie = data.pie_optimisation;
  const ks = data.kiwisaver;
  const bl = data.bright_line;
  const us: any[] = data.us_households ?? [];
  const uk: any[] = data.uk_households ?? [];
  const nzFindings = lh.count > 0 || pie.count > 0 || ks.count > 0 || bl.count > 0;

  return (
    <>
      <Tiles testId="tax-book-summary">
        <Tile label="Households scanned" value={data.total_households_scanned} caption={`as of ${data.generated_at}`} />
        <Tile label="NZ harvestable losses" value={nzd(lh.total_harvestable_loss)} caption={`${lh.count} lot(s)`} tone={lh.count ? "text-crit" : "text-ink"} />
        <Tile label="NZ est. tax saving" value={nzd(lh.estimated_tax_saving + pie.total_annual_saving)} caption="loss harvest + PIE" tone="text-ok" />
        <Tile label="Bright-line alerts" value={bl.count} caption={bl.count > 0 ? "NZ properties" : "none"} tone={bl.count ? "text-warn" : "text-ink-muted"} />
      </Tiles>

      {us.length > 0 && <JurisdictionHouseholds title="US households" rows={us} kind="US" />}
      {uk.length > 0 && <JurisdictionHouseholds title="UK households" rows={uk} kind="UK" />}

      {nzFindings && (
        <>
          <Section title="NZ loss harvesting" count={lh.count} testId="tax-book-nz-harvest"
            chips={<>
              {lh.count > 0 && <Chip tone="text-ok">{nzd(lh.estimated_tax_saving)} at top PIR</Chip>}
              {ks.count > 0 && <Chip>KiwiSaver {ks.count}</Chip>}
            </>}>
            <Lots lots={lh.opportunities} ccy="NZD" />
          </Section>
          {pie.count > 0 && (
            <Section title="NZ PIE fund regime" count={pie.count} testId="tax-book-nz-pie"
              chips={<Chip tone="text-ok">{nzd(pie.total_annual_saving)} / yr</Chip>}>
              <Rows>
                {pie.flags.map((f: any, i: number) => (
                  <Row key={i} hint={f.action}
                    title={f.household_id ? <ViewLink href={householdHref(f.household_id)} className="hover:underline">{f.household_name}</ViewLink> : f.household_name}
                    sub={`${f.person_name} · ${f.mandate_name}`}
                    chips={<Chip>marginal {pctFig(f.marginal_rate_pct)} · PIR {pctFig(f.pir_pct)}</Chip>}
                    figure={nzd(f.estimated_annual_saving)} figureTone="text-ok" caption="est. / yr" />
                ))}
              </Rows>
            </Section>
          )}
        </>
      )}
    </>
  );
}

function JurisdictionHouseholds({ title, rows, kind }: { title: string; rows: any[]; kind: "US" | "UK" }) {
  return (
    <Section title={title} count={rows.length} testId={`tax-book-${kind.toLowerCase()}`}>
      <Rows>
        {rows.map((h: any) => {
          const s = h.summary ?? {};
          const ccy = h.currency;
          return (
            <Row key={h.household_id}
              title={<ViewLink href={householdHref(h.household_id)} className="hover:underline" data-testid="tax-book-household">{h.household_name}</ViewLink>}
              sub={`portfolio ${money(h.total_portfolio_value, ccy)}`}
              chips={<>
                {kind === "US" && s.rmd_required > 0 && <Chip tone="text-crit">RMD active {s.rmd_required}</Chip>}
                {kind === "US" && s.roth_opportunity > 0 && <Chip tone="text-ok">Roth opportunity {s.roth_opportunity}</Chip>}
                {kind === "UK" && s.cgt_flag && <Chip tone="text-warn">CGT exposure</Chip>}
                {kind === "UK" && s.dividend_flag && <Chip tone="text-warn">dividend allowance exceeded</Chip>}
                {s.harvestable_loss > 0 && <Chip>{money(s.harvestable_loss, ccy)} harvestable</Chip>}
              </>}
              figure={s.total_flags} caption="flags" figureTone={s.total_flags ? "text-warn" : "text-ink-muted"} />
          );
        })}
      </Rows>
    </Section>
  );
}

function IndiaBook({ data }: { data: any }) {
  const inr = (n: number) => money(n, "INR");
  const rows: any[] = data.in_households ?? [];
  const sum = (k: string) => rows.reduce((t, h) => t + (h.summary?.[k] || 0), 0);
  return (
    <>
      <Tiles testId="tax-india-book">
        <Tile label="Households" value={rows.length} caption={rows[0] ? `Tax Year ${rows[0].tax_year}` : undefined} />
        <Tile label="Estimated tax so far" value={inr(sum("estimated_tax"))} caption="estimate" />
        <Tile label="Harvestable loss" value={inr(sum("harvestable_loss"))} caption="after FIFO" tone="text-crit" />
        <Tile label="Exemption unused" value={inr(sum("exemption_unused"))} caption="across taxpayers" tone="text-ok" />
      </Tiles>
      <Section title="Households" count={rows.length} testId="tax-india-households">
        {rows.length === 0 ? <None>No Indian households</None> : (
          <table className="w-full text-sm">
            <thead>
              <tr className="text-left text-[10px] uppercase tracking-wide text-ink-muted">
                <th className="px-4 py-2 font-medium">Household</th>
                <th className="hidden px-2 py-2 text-right font-medium cq-sm:table-cell">Taxpayers</th>
                <th className="px-2 py-2 text-right font-medium">Est. tax</th>
                <th className="px-2 py-2 text-right font-medium">Harvestable</th>
                <th className="px-2 py-2 text-right font-medium">Could save</th>
                <th className="hidden px-4 py-2 text-right font-medium cq-md:table-cell">Exemption unused</th>
              </tr>
            </thead>
            <tbody className="divide-y divide-border-soft">
              {rows.map((h) => (
                <tr key={h.household_id}>
                  <td className="max-w-0 truncate px-4 py-2">
                    <ViewLink href={householdHref(h.household_id)} className="text-ink hover:underline" data-testid="tax-book-household">{h.household_name}</ViewLink>
                  </td>
                  <td className="hidden px-2 py-2 text-right font-mono text-xs tabular-nums text-ink-muted cq-sm:table-cell">{h.summary.taxpayers}</td>
                  <td className="whitespace-nowrap px-2 py-2 text-right font-mono text-xs tabular-nums text-ink-muted">{inr(h.summary.estimated_tax)}</td>
                  <td className="whitespace-nowrap px-2 py-2 text-right font-mono text-xs tabular-nums text-ink-muted">{inr(h.summary.harvestable_loss)}</td>
                  <td className="whitespace-nowrap px-2 py-2 text-right font-mono text-xs tabular-nums text-ok">{inr(h.summary.estimated_tax_saving)}</td>
                  <td className="hidden whitespace-nowrap px-4 py-2 text-right font-mono text-xs tabular-nums text-ink-muted cq-md:table-cell">{inr(h.summary.exemption_unused)}</td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
      </Section>
    </>
  );
}

/** Whom to open: every household, each a link that opens its report (in the pane, in the pane). */
function HouseholdPicker() {
  const { data, loading, error, refetch } = useApi<{ id: string; name: string }[]>("/api/core/households");
  const [q, setQ] = useState("");
  if (error) return <Card><ErrorState what="households" message={error} onRetry={refetch} /></Card>;
  if (loading || !data) return <SkeletonList count={2} />;
  const needle = q.trim().toLowerCase();
  const shown = needle ? data.filter((h) => h.name.toLowerCase().includes(needle)) : data;
  return (
    <Section title="Open a household" count={data.length} testId="tax-households"
      chips={data.length > 8 ? (
        <input className="input w-full py-1 text-xs cq-sm:w-48" placeholder="Filter" aria-label="Filter households"
          value={q} onChange={(e) => setQ(e.target.value)} data-testid="tax-household-filter" />
      ) : undefined}>
      {shown.length === 0 ? <None>No match</None> : (
        <div className="max-h-80 divide-y divide-border-soft overflow-y-auto">
          {shown.map((h) => (
            <ViewLink key={h.id} href={householdHref(h.id)} className="block truncate px-4 py-2 text-sm text-ink hover:bg-border-soft" data-testid="tax-household">
              {h.name}
            </ViewLink>
          ))}
        </div>
      )}
    </Section>
  );
}

// ── One household ──────────────────────────────────────────────────────────────────────────────

function HouseholdTax({ id }: { id: string }) {
  const { data, loading, error, refetch } = useApi<any>(`/api/core/households/${id}/tax-intel`, [id]);
  const jur: string | null = data?.jurisdiction ?? null;
  // The 30-day wash-sale rule binds a household taxed in the US and nobody else (CLAUDE.md).
  const washPath = jur === "US" && data?.supported !== false ? `/api/core/households/${id}/wash-sale-calendar` : null;
  const wash = useApi<any>(washPath, [washPath]);

  const back = <AllClients href="/studio/tax" />;
  const refresh = (
    <button className="btn-outline text-xs" disabled={loading} data-testid="tax-refresh"
      onClick={() => { refetch(); if (washPath) wash.refetch(); }}>{loading ? "Scanning…" : "Refresh"}</button>
  );
  if (error) return <Shell><ViewHeader title="Tax intelligence" actions={back} /><Card><ErrorState what="this household's tax report" message={error} onRetry={refetch} /></Card></Shell>;
  if (loading || !data) return <Shell><ViewHeader title="Tax intelligence" actions={back} /><SkeletonList count={4} /></Shell>;

  const ccy: string = data.currency || "USD";
  const meta = (
    <>
      {JURISDICTION[data.jurisdiction] ? <Chip>{JURISDICTION[data.jurisdiction]}</Chip> : <Chip>{data.jurisdiction}</Chip>}
      {data.supported !== false && <Chip tone="text-warn">estimates</Chip>}
      {data.generated_at && <Chip>as of {data.generated_at}</Chip>}
    </>
  );

  return (
    <Shell>
      <ViewHeader title={`Tax · ${data.household_name ?? "household"}`} meta={meta} actions={<>{refresh}{back}</>} />
      {data.supported === false ? (
        <Card><Empty><span data-testid="tax-not-supported">No tax rules on this platform for {data.jurisdiction}.</span></Empty></Card>
      ) : (
        <>
          {jur === "US" && <WashSaleCalendar state={wash} ccy={ccy} />}
          {jur === "IN" && <TaxPositionCard result={data} full />}
          {jur === "US" && <UnitedStates data={data} ccy={ccy} />}
          {jur === "UK" && <UnitedKingdom data={data} ccy={ccy} />}
          {jur === "NZ" && <NewZealand data={data} ccy={ccy} />}
        </>
      )}
    </Shell>
  );
}

function WashSaleCalendar({ state, ccy }: { state: { data: any; loading: boolean; error: string | null; refetch: () => void }; ccy: string }) {
  if (state.error) return <Card className="mb-4"><ErrorState what="the wash-sale calendar" message={state.error} onRetry={state.refetch} /></Card>;
  if (state.loading || !state.data) return <div className="mb-4"><SkeletonList count={1} /></div>;
  const d = state.data;
  const clean = d.violations.length === 0;
  return (
    <Section title="Wash-sale calendar" count={d.violations.length} testId="tax-wash-sale"
      note="Any account in the household rebuying the same instrument within 30 days"
      chips={<>
        <Chip>{d.lots_checked} lots checked</Chip>
        {clean ? <Chip tone="text-ok">clear</Chip> : <Chip tone="text-warn">{money(d.total_disallowed_loss, ccy)} at risk</Chip>}
      </>}>
      {clean ? <None>No conflicts</None> : (
        <Rows>
          {d.violations.map((v: any) => (
            <Row key={v.lot_id} title={<span title={v.instrument_name}><span className="font-mono">{v.symbol}</span> · {v.instrument_name}</span>}
              sub={`${v.mandate_name} · ${v.account_name}`}
              chips={v.wash_sale_conflicts.map((c: any, i: number) => (
                <Chip key={i} tone="text-warn" title={`acquired ${c.acquired_on}`}>{c.account_name} +{qty(c.quantity)} · {c.days_ago}d ago</Chip>
              ))}
              figure={money(v.wash_sale_disallowed_loss, ccy)} figureTone="text-warn" caption="disallowed" />
          ))}
        </Rows>
      )}
    </Section>
  );
}

// ── United States ──────────────────────────────────────────────────────────────────────────────

function UnitedStates({ data, ccy }: { data: any; ccy: string }) {
  const m = (n: number) => money(n, ccy);
  const cgt = data.cgt_analysis;
  const rmd = data.rmd_planning;
  const gift = data.gift_tracker;
  const roth = data.roth_conversion;
  const sf = gift["529_superfunding"];
  const attractive = roth.candidates.filter((c: any) => c.attractive).length;

  return (
    <>
      <Tiles testId="tax-us-summary">
        <Tile label="Short-term gains" value={m(cgt.total_short_term_gains)} caption="ordinary income rates" tone="text-warn" />
        <Tile label="Long-term gains" value={m(cgt.total_long_term_gains)} caption="0–20% LTCG rates" />
        <Tile label="RMD this year" value={rmd.total_rmd_this_year > 0 ? m(rmd.total_rmd_this_year) : "Not required"}
          caption={rmd.rmd_active > 0 ? `${rmd.rmd_active} aged 73+` : "none active"} tone={rmd.rmd_active > 0 ? "text-crit" : "text-ink-muted"} />
        <Tile label="Harvestable losses" value={m(cgt.total_harvestable_losses)} caption={`est. saving ${m(data.summary.estimated_tax_saving)}`} />
      </Tiles>

      <Section title="Capital gains and losses" testId="tax-us-cgt"
        chips={<>
          {cgt.st_conversion_action && <Chip tone="text-warn" title={cgt.st_conversion_action}>hold 12+ months · ~{m(cgt.potential_st_to_lt_saving)}</Chip>}
          {cgt.niit_exposed && <Chip tone="text-crit" title={cgt.niit_action}>NIIT exposed · income {m(cgt.estimated_income)} · threshold {m(cgt.niit_threshold)}</Chip>}
          {cgt.wash_sale_violations.length > 0 && <Chip tone="text-warn">wash-sale risk {cgt.wash_sale_violations.length}</Chip>}
        </>}>
        {cgt.short_term_gains.length > 0 && <><Sub>Short-term gains · held under 366 days</Sub><Lots lots={cgt.short_term_gains} ccy={ccy} gain washSale /></>}
        {cgt.long_term_gains.length > 0 && <><Sub>Long-term gains · held 366 days or more</Sub><Lots lots={cgt.long_term_gains} ccy={ccy} gain washSale /></>}
        {cgt.loss_lots.length > 0 && <><Sub>Harvestable losses</Sub><Lots lots={cgt.loss_lots} ccy={ccy} washSale /></>}
        {!cgt.short_term_gains.length && !cgt.long_term_gains.length && !cgt.loss_lots.length && <None>No lots over the thresholds</None>}
      </Section>

      <Section title="Required minimum distributions" count={rmd.count} note={rmd.guidance} testId="tax-us-rmd"
        chips={rmd.rmd_active > 0 ? <Chip tone="text-crit">{rmd.rmd_active} active</Chip> : undefined}>
        {rmd.count === 0 ? <None>No RMD alerts</None> : (
          <Rows>
            {rmd.alerts.map((a: any, i: number) => (
              <Row key={i} hint={a.action} title={a.person_name}
                sub={`age ${a.age} · IRA ${m(a.ira_balance)}${a.roth_balance > 0 ? ` · Roth ${m(a.roth_balance)}` : ""}`}
                chips={a.rmd_required ? <Chip tone="text-crit">required</Chip> : a.years_to_rmd != null ? <Chip tone="text-warn">RMD in {a.years_to_rmd}y</Chip> : undefined}
                figure={a.estimated_rmd > 0 ? m(a.estimated_rmd) : undefined} figureTone="text-crit"
                caption={a.estimated_rmd > 0 ? "by 31 Dec" : undefined} />
            ))}
          </Rows>
        )}
      </Section>

      <Section title="Annual gift exclusion" note={gift.action} testId="tax-us-gift"
        chips={<>
          {gift.estate_exposed && <Chip tone="text-warn">estate above post-sunset exemption</Chip>}
          <Chip title={gift.qualified_exclusions?.tuition}>tuition paid direct · unlimited</Chip>
          <Chip title={gift.qualified_exclusions?.medical}>medical paid direct · unlimited</Chip>
        </>}>
        <div className="grid grid-cols-2 gap-3 p-4 cq-md:grid-cols-3">
          <Tile label="Per donor, per recipient" value={m(gift.annual_exclusion_per_donor)}
            caption={gift.gift_splitting_available ? `${m(gift.effective_annual_per_recipient)} with gift splitting` : undefined} />
          <Tile label="Max exclusion gifts this year" value={m(gift.max_annual_exclusion_gifts)} caption={`${gift.donors} donor(s) · assumes 4 outside recipients`} />
          {sf && <Tile label="529 superfunding" value={m(sf.max_per_recipient)} caption="per beneficiary · 5-year front-load" />}
        </div>
      </Section>

      <Section title="Roth conversion" count={roth.candidates.length} note={roth.guidance} testId="tax-us-roth"
        chips={<>
          {roth.total_ira_balance > 0 && <Chip>IRA/401(k) {m(roth.total_ira_balance)}</Chip>}
          {attractive > 0 && <Chip tone="text-ok">opportunities {attractive}</Chip>}
        </>}>
        {roth.candidates.length === 0 ? <None>No IRA on file</None> : (
          <Rows>
            {roth.candidates.map((c: any, i: number) => (
              <Row key={i} hint={c.action} title={c.person_name}
                sub={`IRA ${m(c.ira_balance)} · Roth ${m(c.roth_balance)}`}
                chips={<Chip tone={c.attractive ? "text-ok" : "text-ink-muted"}>{pctFig(c.marginal_rate_pct)} marginal</Chip>}
                figure={c.recommended_conversion > 0 ? m(c.recommended_conversion) : undefined} figureTone="text-ok"
                caption={c.recommended_conversion > 0 ? "convert this year" : undefined} />
            ))}
          </Rows>
        )}
      </Section>

      <Withdrawals data={data.withdrawal_sequencing} ccy={ccy} />
    </>
  );
}

// ── United Kingdom ─────────────────────────────────────────────────────────────────────────────

function UnitedKingdom({ data, ccy }: { data: any; ccy: string }) {
  const m = (n: number) => money(n, ccy);
  const cgt = data.cgt_allowance;
  const isa = data.isa_allowance;
  const pension = data.pension_allowance;
  const pet = data.pet_clock;
  const div = data.dividend_allowance;
  const s = data.summary;
  const flagged = [s.cgt_flag && "CGT", s.dividend_flag && "dividend"].filter(Boolean).join(" · ");

  return (
    <>
      <Tiles testId="tax-uk-summary">
        <Tile label="CGT exposure" value={cgt.flag ? m(cgt.net_taxable_gains) : "Within exempt amount"}
          caption={cgt.flag ? `est. tax ${m(cgt.estimated_cgt_at_higher_rate)}` : `${m(cgt.annual_exempt_amount)} exempt / person`}
          tone={cgt.flag ? "text-warn" : "text-ok"} />
        <Tile label="Total ISA value" value={m(isa.total_isa_value)} caption={`${m(isa.annual_allowance)} / person allowance`} />
        <Tile label="Harvestable losses" value={m(cgt.total_harvestable_losses)} caption="offset gains before 5 April" />
        <Tile label="Issues flagged" value={s.total_flags} caption={flagged || undefined} tone={s.total_flags > 0 ? "text-warn" : "text-ink-muted"} />
      </Tiles>

      <Section title="CGT annual exempt amount" note={cgt.key_changes} testId="tax-uk-cgt"
        chips={cgt.flag ? <Chip tone="text-warn">allowance exceeded</Chip> : <Chip tone="text-ok">within allowance</Chip>}>
        <div className="grid grid-cols-2 gap-3 p-4 cq-md:grid-cols-4">
          <Tile label="Unrealised gains" value={m(cgt.total_unrealised_gains)} />
          <Tile label="Exempt available" value={m(cgt.total_exempt_available)} />
          <Tile label="Harvestable losses" value={m(cgt.total_harvestable_losses)} />
          <Tile label="Est. CGT (higher rate)" value={cgt.flag ? m(cgt.estimated_cgt_at_higher_rate) : "—"} tone={cgt.flag ? "text-warn" : "text-ink-muted"} />
        </div>
        {cgt.gain_lots.length > 0 && <><Sub>Unrealised gains</Sub><Lots lots={cgt.gain_lots} ccy={ccy} gain /></>}
        {cgt.loss_lots.length > 0 && <><Sub>Harvestable losses</Sub><Lots lots={cgt.loss_lots} ccy={ccy} /></>}
      </Section>

      <Section title="ISA allowance" count={isa.persons.length} note={isa.guidance} testId="tax-uk-isa"
        chips={isa.junior_isa?.available ? <Chip title={(isa.junior_isa.beneficiaries ?? []).join(", ")}>Junior ISA {m(isa.junior_isa.allowance)} / yr per child</Chip> : undefined}>
        {isa.persons.length === 0 ? <None>No adults on file</None> : (
          <Rows>
            {isa.persons.map((p: any, i: number) => (
              <Row key={i} hint={p.action} title={p.person_name} sub={`allowance ${m(p.annual_allowance)}`} figure={m(p.isa_value)} caption="in ISA" />
            ))}
          </Rows>
        )}
      </Section>

      <Section title="Pension annual allowance" count={pension.persons.length} note={pension.guidance} testId="tax-uk-pension">
        {pension.persons.length === 0 ? <None>No adults on file</None> : (
          <Rows>
            {pension.persons.map((p: any, i: number) => (
              <Row key={i} hint={p.action} title={p.person_name}
                sub={`pot ${m(p.pension_pot)}`}
                chips={p.tapered ? <Chip tone="text-crit">tapered · standard {m(p.standard_allowance)}</Chip> : undefined}
                figure={m(p.effective_allowance)} figureTone={p.tapered ? "text-crit" : "text-ink"} caption="allowance" />
            ))}
          </Rows>
        )}
      </Section>

      <Section title="PET 7-year clock" note={pet.planning_note} testId="tax-uk-pet">
        <Sub>IHT taper relief</Sub>
        <div className="flex gap-1 px-4 py-3">
          {pet.taper_schedule.map((t: any, i: number) => (
            <div key={i} className={`min-w-0 flex-1 rounded border border-border-soft py-2 text-center font-mono text-xs tabular-nums ${i === 0 ? "text-crit" : i === pet.taper_schedule.length - 1 ? "text-ok" : "text-warn"}`}>
              <div className="font-semibold">{t.years_survived}yr</div>
              <div>{t.iht_pct_payable}%</div>
            </div>
          ))}
        </div>
        <div className="grid grid-cols-2 gap-3 px-4 pb-4">
          <Tile label="Annual exemption" value={m(pet.annual_exemption?.amount)} caption={<span title={pet.annual_exemption?.note}>per donor</span>} />
          <Tile label="Small gift exemption" value={m(pet.small_gift_exemption?.amount)} caption={<span title={pet.small_gift_exemption?.note}>per recipient</span>} />
        </div>
      </Section>

      <Section title="Dividend allowance" note={div.key_changes} testId="tax-uk-dividend"
        chips={div.flag ? <Chip tone="text-warn">likely exceeded</Chip> : <Chip tone="text-ok">within allowance</Chip>}>
        <div className="grid grid-cols-2 gap-3 p-4 cq-md:grid-cols-3" title={div.action}>
          <Tile label="Annual allowance" value={m(div.annual_allowance)} caption="per person" />
          <Tile label="Est. annual dividends" value={m(div.estimated_annual_dividends)} caption="estimate · 60% equity at 2.5% yield" />
          {div.flag && <Tile label="Est. tax (higher rate)" value={m(div.estimated_tax)} caption="33.75% rate" tone="text-warn" />}
        </div>
      </Section>

      <Withdrawals data={data.withdrawal_sequencing} ccy={ccy} />
    </>
  );
}

// ── New Zealand ────────────────────────────────────────────────────────────────────────────────

function NewZealand({ data, ccy }: { data: any; ccy: string }) {
  const m = (n: number) => money(n, ccy);
  const lh = data.loss_harvest;
  const pie = data.pie_optimisation;
  const ks = data.kiwisaver;
  const bl = data.bright_line;

  return (
    <>
      <Tiles testId="tax-nz-summary">
        <Tile label="Harvestable losses" value={m(lh.total_harvestable_loss)} caption={`${lh.count} lot(s)`} tone={lh.count ? "text-crit" : "text-ink"} />
        <Tile label="Est. tax saving" value={m(data.summary.estimated_tax_saving)} caption="loss harvest + PIE" tone="text-ok" />
        <Tile label="Bright-line alerts" value={bl.count} caption={bl.count > 0 ? "review before any sale" : "none"} tone={bl.count ? "text-warn" : "text-ink-muted"} />
        <Tile label="Total portfolio" value={m(data.total_portfolio_value)} caption={`${ks.count} KiwiSaver flag(s)`} />
      </Tiles>

      <Section title="Loss harvesting" count={lh.count} testId="tax-nz-harvest"
        chips={lh.count > 0 ? <Chip tone="text-ok">{m(lh.estimated_tax_saving_at_top_pir)} at top PIR</Chip> : undefined}>
        {lh.count === 0 ? <None>No harvestable losses</None> : <Lots lots={lh.opportunities} ccy={ccy} />}
      </Section>

      <Section title="PIE fund regime" count={pie.count} testId="tax-nz-pie"
        chips={pie.count > 0 ? <Chip tone="text-ok">{m(pie.total_annual_saving)} / yr</Chip> : undefined}>
        {pie.count === 0 ? <None>No PIE mismatches</None> : (
          <Rows>
            {pie.flags.map((f: any, i: number) => (
              <Row key={i} hint={f.action} title={f.person_name} sub={`${f.mandate_name} · ${m(f.mandate_value)}`}
                chips={<Chip>marginal {pctFig(f.marginal_rate_pct)} · PIR {pctFig(f.pir_pct)}</Chip>}
                figure={m(f.estimated_annual_saving)} figureTone="text-ok" caption="est. / yr" />
            ))}
          </Rows>
        )}
      </Section>

      <Section title="KiwiSaver contributions" count={ks.count} testId="tax-nz-kiwisaver">
        {ks.count === 0 ? <None>No KiwiSaver findings</None> : (
          <Rows>
            {ks.recommendations.map((r: any, i: number) => (
              <Row key={i} hint={r.action} title={r.person_name} sub={`income ${m(r.annual_income)}`}
                chips={<>
                  {r.current_rate_pct !== r.recommended_rate_pct
                    ? <Chip tone="text-warn">{r.current_rate_pct}% → {r.recommended_rate_pct}%</Chip>
                    : <Chip>{r.current_rate_pct}% · optimal</Chip>}
                  <Chip>max govt top-up {m(r.max_govt_topup)} / yr</Chip>
                  <Chip>employer match {m(r.employer_match_annual)} / yr · 3% assumed</Chip>
                </>} />
            ))}
          </Rows>
        )}
      </Section>

      <Section title="Bright-line property" count={bl.count} testId="tax-nz-bright-line">
        {bl.count === 0 ? <None>No bright-line alerts</None> : (
          <Rows>
            {bl.flags.map((f: any, i: number) => {
              const within = f.status === "within_bright_line";
              return (
                <Row key={i} hint={f.action} title={f.person_name} sub={f.property_address}
                  chips={<>
                    <Chip tone={within ? "text-crit" : "text-warn"}>{within ? "within bright-line" : "approaching"}</Chip>
                    <Chip>acquired {f.acquired_on}</Chip>
                    <Chip>{f.days_held}d held</Chip>
                    <Chip>{f.test_years}-year test</Chip>
                    {f.months_until_safe > 0 && <Chip tone="text-warn">{Number(f.months_until_safe).toFixed(1)} months to safe</Chip>}
                  </>}
                  figure={f.property_value ? m(f.property_value) : undefined} />
              );
            })}
          </Rows>
        )}
      </Section>

      <Withdrawals data={data.withdrawal_sequencing} ccy={ccy} />
    </>
  );
}
