"use client";
import { useEffect, useState, useCallback } from "react";
import { api } from "@/lib/api";
import {
  Receipt, AlertTriangle, TrendingDown, Leaf, Landmark, ArrowDownUp,
  RefreshCw, Home, DollarSign, Clock, Gift, TrendingUp, PiggyBank,
  ChevronDown, ChevronRight, Info,
} from "lucide-react";

// ── Types ─────────────────────────────────────────────────────────────────────

interface Household { id: string; name: string; total_value: number }

// Shared
interface LotEntry {
  symbol: string; instrument_name: string; account_name: string; mandate_name: string;
  quantity: number; cost_per_unit: number; current_price: number; unrealised_gain?: number;
  unrealised_loss?: number; holding_days: number; wash_sale_risk: boolean;
}

// NZ-specific
interface NZResult {
  jurisdiction: "NZ"; currency: "NZD";
  loss_harvest: { opportunities: LotEntry[]; count: number; total_harvestable_loss: number; estimated_tax_saving_at_top_pir: number };
  pie_optimisation: { flags: any[]; count: number; total_annual_saving: number };
  kiwisaver: { recommendations: any[]; count: number };
  bright_line: { flags: any[]; count: number };
  withdrawal_sequencing: { sequences: any[]; count: number; guidance: string };
  summary: { total_flags: number; harvestable_loss: number; estimated_tax_saving: number; bright_line_flags: number };
}

// US-specific
interface USResult {
  jurisdiction: "US"; currency: "USD";
  cgt_analysis: {
    short_term_gains: LotEntry[]; long_term_gains: LotEntry[]; loss_lots: LotEntry[];
    total_short_term_gains: number; total_long_term_gains: number; total_harvestable_losses: number;
    niit_exposed: boolean; niit_threshold: number; estimated_income: number;
    potential_st_to_lt_saving: number; niit_action?: string; st_conversion_action?: string;
    wash_sale_violations: LotEntry[];
  };
  rmd_planning: {
    alerts: any[]; count: number; rmd_active: number; total_rmd_this_year: number; guidance: string;
  };
  gift_tracker: {
    annual_exclusion_per_donor: number; donors: number; married: boolean;
    max_annual_exclusion_gifts: number; estate_exposed: boolean;
    action: string; qualified_exclusions: any; gift_splitting_available: boolean;
    effective_annual_per_recipient: number;
    "529_superfunding"?: { available: boolean; max_per_recipient: number; max_total: number; action: string };
  };
  roth_conversion: {
    candidates: any[]; count: number; total_ira_balance: number; guidance: string;
  };
  withdrawal_sequencing: { sequences: any[]; count: number; guidance: string };
  summary: { total_flags: number; harvestable_loss: number; estimated_tax_saving: number; rmd_required: number; roth_opportunity: number };
}

// UK-specific
interface UKResult {
  jurisdiction: "UK"; currency: "GBP";
  cgt_allowance: {
    annual_exempt_amount: number; total_exempt_available: number;
    total_unrealised_gains: number; total_harvestable_losses: number;
    net_taxable_gains: number; estimated_cgt_at_higher_rate: number;
    gain_lots: LotEntry[]; loss_lots: LotEntry[];
    flag: boolean; action: string; key_changes: string;
  };
  isa_allowance: { annual_allowance: number; persons: any[]; total_isa_value: number; guidance: string; junior_isa: any };
  pension_allowance: { annual_allowance: number; persons: any[]; guidance: string };
  pet_clock: { survival_period_years: number; taper_schedule: any[]; planning_note: string; annual_exemption: any; small_gift_exemption: any; wedding_gifts: any };
  dividend_allowance: { annual_allowance: number; estimated_annual_dividends: number; flag: boolean; action: string; key_changes: string; estimated_tax: number };
  withdrawal_sequencing: { sequences: any[]; count: number; guidance: string };
  summary: { total_flags: number; harvestable_loss: number; estimated_tax_saving: number; cgt_flag: boolean; dividend_flag: boolean };
}

type HouseholdResult = (NZResult | USResult | UKResult) & {
  household_id: string; household_name: string; total_portfolio_value: number; generated_at: string;
};

interface BookResult {
  total_households_scanned: number; generated_at: string;
  jurisdictions?: { NZ: number; US: number; UK: number };
  loss_harvest: { opportunities: any[]; count: number; total_harvestable_loss: number; estimated_tax_saving: number };
  pie_optimisation: { flags: any[]; count: number; total_annual_saving: number };
  kiwisaver: { recommendations: any[]; count: number };
  bright_line: { flags: any[]; count: number };
  us_households?: { household_id: string; household_name: string; total_portfolio_value: number; summary: USResult["summary"]; currency: string }[];
  uk_households?: { household_id: string; household_name: string; total_portfolio_value: number; summary: UKResult["summary"]; currency: string }[];
}

// ── Utilities ─────────────────────────────────────────────────────────────────

function makeFmt(currency: string) {
  const locale = currency === "GBP" ? "en-GB" : currency === "USD" ? "en-US" : "en-NZ";
  return (n: number) =>
    new Intl.NumberFormat(locale, { style: "currency", currency, maximumFractionDigits: 0 }).format(n);
}

const fmtPct = (n: number) => `${n.toFixed(1)}%`;

// ── Shared sub-components ─────────────────────────────────────────────────────

function SummaryCard({ icon: Icon, label, value, sub, color }: { icon: any; label: string; value: string; sub?: string; color: string }) {
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

function SectionHeader({ icon: Icon, title, count, saving, badge }: { icon: any; title: string; count?: number; saving?: string; badge?: { text: string; color: string } }) {
  return (
    <div className="flex items-center justify-between mb-3">
      <div className="flex items-center gap-2">
        <Icon className="w-4 h-4 text-neutral-400" />
        <h3 className="font-semibold text-neutral-900 dark:text-neutral-100">{title}</h3>
        {count !== undefined && (
          <span className="text-xs bg-neutral-100 dark:bg-neutral-700 text-neutral-600 dark:text-neutral-300 px-2 py-0.5 rounded-full">{count}</span>
        )}
        {badge && (
          <span className={`text-xs px-2 py-0.5 rounded-full font-medium ${badge.color}`}>{badge.text}</span>
        )}
      </div>
      {saving && <span className="text-xs text-emerald-600 dark:text-emerald-400 font-medium">{saving} potential saving</span>}
    </div>
  );
}

function LotTable({ lots, currency, gainMode }: { lots: LotEntry[]; currency: string; gainMode?: boolean }) {
  const fmt = makeFmt(currency);
  if (!lots.length)
    return <div className="text-sm text-neutral-400 py-4 text-center">None identified.</div>;
  return (
    <div className="overflow-x-auto">
      <table className="w-full text-sm">
        <thead>
          <tr className="border-b border-neutral-200 dark:border-neutral-700">
            {["Mandate / Account", "Instrument", "Qty", "Cost", "Price", gainMode ? "Unrealised Gain" : "Unrealised Loss", "Held", ""].map((h, i) => (
              <th key={i} className="text-left py-2 px-2 text-xs font-medium text-neutral-500 dark:text-neutral-400 whitespace-nowrap">{h}</th>
            ))}
          </tr>
        </thead>
        <tbody>
          {lots.map((o, i) => {
            const amount = gainMode ? (o.unrealised_gain ?? 0) : (o.unrealised_loss ?? o.unrealised_gain ?? 0);
            const isNeg = amount < 0;
            return (
              <tr key={i} className="border-b border-neutral-100 dark:border-neutral-800 hover:bg-neutral-50 dark:hover:bg-neutral-750">
                <td className="py-2 px-2">
                  <div className="font-medium text-neutral-800 dark:text-neutral-200 text-xs">{o.mandate_name}</div>
                  <div className="text-neutral-400 text-xs">{o.account_name}</div>
                </td>
                <td className="py-2 px-2">
                  <div className="font-mono text-xs font-medium text-neutral-800 dark:text-neutral-200">{o.symbol}</div>
                  <div className="text-neutral-400 text-xs truncate max-w-[140px]">{o.instrument_name}</div>
                </td>
                <td className="py-2 px-2 text-right text-neutral-700 dark:text-neutral-300 font-mono text-xs">{o.quantity.toLocaleString()}</td>
                <td className="py-2 px-2 text-right text-neutral-700 dark:text-neutral-300 font-mono text-xs">{fmt(o.cost_per_unit)}</td>
                <td className="py-2 px-2 text-right text-neutral-700 dark:text-neutral-300 font-mono text-xs">{fmt(o.current_price)}</td>
                <td className="py-2 px-2 text-right">
                  <span className={`font-semibold font-mono text-xs ${isNeg ? "text-red-600 dark:text-red-400" : "text-emerald-600 dark:text-emerald-400"}`}>
                    {fmt(amount)}
                  </span>
                </td>
                <td className="py-2 px-2 text-right text-neutral-500 text-xs">{o.holding_days}d</td>
                <td className="py-2 px-2">
                  {o.wash_sale_risk && <span className="text-xs bg-amber-100 dark:bg-amber-900/40 text-amber-700 dark:text-amber-300 px-1.5 py-0.5 rounded font-medium">Wash-sale</span>}
                </td>
              </tr>
            );
          })}
        </tbody>
      </table>
    </div>
  );
}

function WithdrawalPanel({ data, currency }: { data: { sequences: any[]; count: number; guidance: string }; currency: string }) {
  const fmt = makeFmt(currency);
  const colors: Record<number, string> = {
    1: "bg-red-100 dark:bg-red-900/30 text-red-700 dark:text-red-300 border-red-200 dark:border-red-800",
    2: "bg-orange-100 dark:bg-orange-900/30 text-orange-700 dark:text-orange-300 border-orange-200 dark:border-orange-800",
    3: "bg-blue-100 dark:bg-blue-900/30 text-blue-700 dark:text-blue-300 border-blue-200 dark:border-blue-800",
    4: "bg-emerald-100 dark:bg-emerald-900/30 text-emerald-700 dark:text-emerald-300 border-emerald-200 dark:border-emerald-800",
  };
  if (!data.count) return <div className="text-sm text-neutral-400 py-4 text-center">No mandates to sequence.</div>;
  return (
    <div className="space-y-2">
      <p className="text-xs text-neutral-500 dark:text-neutral-400 mb-3">{data.guidance}</p>
      {data.sequences.map((s, i) => (
        <div key={i} className={`rounded-lg border p-3 ${colors[s.withdrawal_priority] || colors[1]}`}>
          <div className="flex items-start justify-between gap-2">
            <div className="flex-1">
              <div className="flex items-center gap-2">
                <span className="text-xs font-bold">{s.priority_label}</span>
                <span className="text-sm font-medium text-neutral-800 dark:text-neutral-200">{s.mandate_name}</span>
              </div>
              <p className="text-xs mt-1 opacity-80">{s.rationale}</p>
            </div>
            <div className="text-right flex-shrink-0">
              <div className="font-semibold text-sm">{fmt(s.value)}</div>
              <div className="text-xs opacity-70">{s.account_type}</div>
            </div>
          </div>
        </div>
      ))}
    </div>
  );
}

function InfoNote({ text }: { text: string }) {
  return (
    <div className="flex gap-2 text-xs text-blue-700 dark:text-blue-300 bg-blue-50 dark:bg-blue-900/20 border border-blue-200 dark:border-blue-800 rounded-lg px-3 py-2 mt-2">
      <Info className="w-3.5 h-3.5 mt-0.5 flex-shrink-0" />
      <span>{text}</span>
    </div>
  );
}

// ════════════════════════════════════════════════════════════════════════════════
// NZ PANELS (existing)
// ════════════════════════════════════════════════════════════════════════════════

function NZResultView({ data }: { data: NZResult & { total_portfolio_value: number; generated_at: string } }) {
  const fmt = makeFmt("NZD");
  const lh = data.loss_harvest;
  const pie = data.pie_optimisation;
  const ks = data.kiwisaver;
  const bl = data.bright_line;

  return (
    <>
      <div className="grid grid-cols-2 sm:grid-cols-4 gap-3">
        <SummaryCard icon={TrendingDown} label="Harvestable losses" value={fmt(lh.total_harvestable_loss)} sub={`${lh.count} lot(s)`} color="bg-red-100 dark:bg-red-900/40 text-red-600 dark:text-red-400" />
        <SummaryCard icon={Leaf} label="Est. tax saving" value={fmt(data.summary.estimated_tax_saving)} sub="loss-harvest + PIE" color="bg-emerald-100 dark:bg-emerald-900/40 text-emerald-600 dark:text-emerald-400" />
        <SummaryCard icon={AlertTriangle} label="Bright-line alerts" value={String(bl.count)} sub={bl.count > 0 ? "Review before any sale" : "No alerts"} color={bl.count > 0 ? "bg-amber-100 dark:bg-amber-900/40 text-amber-600 dark:text-amber-400" : "bg-neutral-100 dark:bg-neutral-700 text-neutral-400"} />
        <SummaryCard icon={Receipt} label="Total portfolio" value={fmt(data.total_portfolio_value)} sub={`${ks.count} KiwiSaver flag(s)`} color="bg-blue-100 dark:bg-blue-900/40 text-blue-600 dark:text-blue-400" />
      </div>
      <div className="space-y-4">
        <div className="bg-white dark:bg-neutral-800 border border-neutral-200 dark:border-neutral-700 rounded-xl p-4">
          <SectionHeader icon={TrendingDown} title="Loss-harvesting opportunities" count={lh.count} saving={lh.count > 0 ? `${fmt(lh.estimated_tax_saving_at_top_pir)} (top PIR)` : undefined} />
          {lh.count === 0 ? <div className="text-sm text-neutral-400 py-4 text-center">No harvestable losses identified.</div> : <LotTable lots={lh.opportunities as any} currency="NZD" />}
        </div>
        <div className="bg-white dark:bg-neutral-800 border border-neutral-200 dark:border-neutral-700 rounded-xl p-4">
          <SectionHeader icon={Leaf} title="PIE fund regime optimisation" count={pie.count} saving={pie.count > 0 ? `${fmt(pie.total_annual_saving)}/yr` : undefined} />
          {pie.count === 0 ? <div className="text-sm text-neutral-400 py-4 text-center">No PIE fund mismatches identified.</div> : (
            <div className="space-y-3">
              {pie.flags.map((f: any, i: number) => (
                <div key={i} className="rounded-lg border border-emerald-200 dark:border-emerald-800 bg-emerald-50 dark:bg-emerald-900/20 p-3">
                  <div className="flex items-start justify-between gap-2">
                    <div>
                      <div className="font-medium text-sm">{f.person_name} — {f.mandate_name}</div>
                      <div className="text-xs text-neutral-500 mt-1">Marginal {fmtPct(f.marginal_rate_pct)} vs PIR {fmtPct(f.pir_pct)} · Mandate {fmt(f.mandate_value)}</div>
                      <p className="text-xs text-emerald-700 dark:text-emerald-300 mt-2">{f.action}</p>
                    </div>
                    <div className="text-right flex-shrink-0">
                      <div className="text-lg font-bold text-emerald-700 dark:text-emerald-300">{fmt(f.estimated_annual_saving)}</div>
                      <div className="text-xs text-neutral-400">per year</div>
                    </div>
                  </div>
                </div>
              ))}
            </div>
          )}
        </div>
        <div className="bg-white dark:bg-neutral-800 border border-neutral-200 dark:border-neutral-700 rounded-xl p-4">
          <SectionHeader icon={ArrowDownUp} title="KiwiSaver contribution optimisation" count={ks.count} />
          {ks.count === 0 ? <div className="text-sm text-neutral-400 py-4 text-center">No KiwiSaver optimisation identified.</div> : (
            <div className="space-y-3">
              {ks.recommendations.map((r: any, i: number) => (
                <div key={i} className="rounded-lg border border-blue-200 dark:border-blue-800 bg-blue-50 dark:bg-blue-900/20 p-3">
                  <div className="flex items-start justify-between gap-4">
                    <div className="flex-1">
                      <div className="font-medium text-sm">{r.person_name}</div>
                      <div className="text-xs text-neutral-500 mt-1">Income: {fmt(r.annual_income)} · Rate: {r.current_rate_pct}%{r.current_rate_pct !== r.recommended_rate_pct && <> → <span className="text-blue-600 font-medium">{r.recommended_rate_pct}% recommended</span></>}</div>
                      <p className="text-xs text-blue-700 dark:text-blue-300 mt-2">{r.action}</p>
                    </div>
                    <div className="text-right space-y-1">
                      <div><div className="text-xs text-neutral-400">Govt top-up</div><div className="font-semibold text-blue-700 dark:text-blue-300">{fmt(r.max_govt_topup)}/yr</div></div>
                      <div><div className="text-xs text-neutral-400">Employer match</div><div className="font-semibold text-blue-700 dark:text-blue-300">{fmt(r.employer_match_annual)}/yr</div></div>
                    </div>
                  </div>
                </div>
              ))}
            </div>
          )}
        </div>
        <div className="bg-white dark:bg-neutral-800 border border-neutral-200 dark:border-neutral-700 rounded-xl p-4">
          <SectionHeader icon={Home} title="Bright-line property tracker" count={bl.count} />
          {bl.count === 0 ? <div className="text-sm text-neutral-400 py-4 text-center">No bright-line alerts.</div> : (
            <div className="space-y-3">
              {bl.flags.map((f: any, i: number) => (
                <div key={i} className={`rounded-lg border p-3 ${f.status === "within_bright_line" ? "border-red-300 bg-red-50 dark:bg-red-900/20" : "border-amber-200 bg-amber-50 dark:bg-amber-900/20"}`}>
                  <div className="flex items-start gap-3">
                    <Home className={`w-4 h-4 mt-0.5 flex-shrink-0 ${f.status === "within_bright_line" ? "text-red-500" : "text-amber-500"}`} />
                    <div>
                      <div className="flex items-center gap-2 flex-wrap">
                        <span className="font-medium text-sm">{f.person_name}</span>
                        <span className="text-xs text-neutral-500">{f.property_address}</span>
                        {f.property_value && <span className="text-xs text-neutral-500">{fmt(f.property_value)}</span>}
                      </div>
                      <div className="flex gap-4 mt-1 text-xs text-neutral-500">
                        <span>Acquired {f.acquired_on}</span><span>{f.days_held}d held</span><span>{f.test_years}-year test</span>
                        {f.months_until_safe > 0 && <span className="font-medium text-amber-600">{f.months_until_safe.toFixed(1)} months until safe</span>}
                      </div>
                      <p className={`text-xs mt-2 ${f.status === "within_bright_line" ? "text-red-700 dark:text-red-300" : "text-amber-700 dark:text-amber-300"}`}>{f.action}</p>
                    </div>
                  </div>
                </div>
              ))}
            </div>
          )}
        </div>
        <div className="bg-white dark:bg-neutral-800 border border-neutral-200 dark:border-neutral-700 rounded-xl p-4">
          <SectionHeader icon={ArrowDownUp} title="Tax-efficient withdrawal sequencing" count={data.withdrawal_sequencing.count} />
          <WithdrawalPanel data={data.withdrawal_sequencing} currency="NZD" />
        </div>
      </div>
    </>
  );
}

// ════════════════════════════════════════════════════════════════════════════════
// US PANELS
// ════════════════════════════════════════════════════════════════════════════════

function USResultView({ data }: { data: USResult & { total_portfolio_value: number; generated_at: string } }) {
  const fmt = makeFmt("USD");
  const cgt = data.cgt_analysis;
  const rmd = data.rmd_planning;
  const gift = data.gift_tracker;
  const roth = data.roth_conversion;

  return (
    <>
      {/* Summary cards */}
      <div className="grid grid-cols-2 sm:grid-cols-4 gap-3">
        <SummaryCard icon={TrendingDown} label="Short-term gains" value={fmt(cgt.total_short_term_gains)} sub="Ordinary income rates" color="bg-amber-100 dark:bg-amber-900/40 text-amber-600 dark:text-amber-400" />
        <SummaryCard icon={TrendingUp} label="Long-term gains" value={fmt(cgt.total_long_term_gains)} sub="0–20% LTCG rates" color="bg-emerald-100 dark:bg-emerald-900/40 text-emerald-600 dark:text-emerald-400" />
        <SummaryCard icon={Clock} label="RMD this year" value={rmd.total_rmd_this_year > 0 ? fmt(rmd.total_rmd_this_year) : "Not required"} sub={rmd.rmd_active > 0 ? `${rmd.rmd_active} person(s) aged 73+` : "All under 73"} color={rmd.rmd_active > 0 ? "bg-red-100 dark:bg-red-900/40 text-red-600 dark:text-red-400" : "bg-neutral-100 dark:bg-neutral-700 text-neutral-400"} />
        <SummaryCard icon={TrendingDown} label="Harvestable losses" value={fmt(cgt.total_harvestable_losses)} sub={`Est. saving: ${fmt(data.summary.estimated_tax_saving)}`} color="bg-blue-100 dark:bg-blue-900/40 text-blue-600 dark:text-blue-400" />
      </div>

      <div className="space-y-4">
        {/* CGT Analysis */}
        <div className="bg-white dark:bg-neutral-800 border border-neutral-200 dark:border-neutral-700 rounded-xl p-4">
          <SectionHeader icon={TrendingDown} title="Capital gains & loss analysis" badge={cgt.niit_exposed ? { text: "NIIT exposed", color: "bg-red-100 dark:bg-red-900/30 text-red-700 dark:text-red-300" } : undefined} />
          {cgt.st_conversion_action && <div className="text-xs text-amber-700 dark:text-amber-300 bg-amber-50 dark:bg-amber-900/20 border border-amber-200 dark:border-amber-800 rounded-lg px-3 py-2 mb-3">{cgt.st_conversion_action}</div>}
          {cgt.niit_action && <div className="text-xs text-red-700 dark:text-red-300 bg-red-50 dark:bg-red-900/20 border border-red-200 dark:border-red-800 rounded-lg px-3 py-2 mb-3">{cgt.niit_action}</div>}

          {cgt.short_term_gains.length > 0 && (
            <div className="mb-4">
              <p className="text-xs font-semibold text-neutral-500 mb-2 uppercase tracking-wide">Short-term gains (held &lt;366 days — ordinary income rates)</p>
              <LotTable lots={cgt.short_term_gains} currency="USD" gainMode />
            </div>
          )}
          {cgt.long_term_gains.length > 0 && (
            <div className="mb-4">
              <p className="text-xs font-semibold text-neutral-500 mb-2 uppercase tracking-wide">Long-term gains (held ≥366 days — 15–20% LTCG rates)</p>
              <LotTable lots={cgt.long_term_gains} currency="USD" gainMode />
            </div>
          )}
          {cgt.loss_lots.length > 0 && (
            <div>
              <p className="text-xs font-semibold text-neutral-500 mb-2 uppercase tracking-wide">Harvestable losses</p>
              <LotTable lots={cgt.loss_lots} currency="USD" />
            </div>
          )}
          {cgt.wash_sale_violations.length > 0 && (
            <div className="mt-3 text-xs text-amber-700 dark:text-amber-300 bg-amber-50 dark:bg-amber-900/20 border border-amber-200 rounded-lg px-3 py-2">
              ⚠ {cgt.wash_sale_violations.length} wash-sale risk(s) identified — selling at a loss and rebuying the same security within 30 days disallows the loss under IRS §1091.
            </div>
          )}
        </div>

        {/* RMD Planning */}
        <div className="bg-white dark:bg-neutral-800 border border-neutral-200 dark:border-neutral-700 rounded-xl p-4">
          <SectionHeader icon={Clock} title="Required Minimum Distributions (RMD)" count={rmd.count} badge={rmd.rmd_active > 0 ? { text: `${rmd.rmd_active} active`, color: "bg-red-100 dark:bg-red-900/30 text-red-700 dark:text-red-300" } : undefined} />
          {rmd.count === 0 ? <div className="text-sm text-neutral-400 py-4 text-center">No RMD alerts — all persons under age 73.</div> : (
            <div className="space-y-3">
              {rmd.alerts.map((a: any, i: number) => (
                <div key={i} className={`rounded-lg border p-3 ${a.rmd_required ? "border-red-300 dark:border-red-700 bg-red-50 dark:bg-red-900/20" : "border-amber-200 dark:border-amber-700 bg-amber-50 dark:bg-amber-900/20"}`}>
                  <div className="flex items-start justify-between gap-2">
                    <div>
                      <div className="font-medium text-sm">{a.person_name} <span className="text-xs text-neutral-500 font-normal">age {a.age}</span></div>
                      <div className="text-xs text-neutral-500 mt-1">IRA: {fmt(a.ira_balance)}{a.roth_balance > 0 && ` · Roth: ${fmt(a.roth_balance)}`}</div>
                      <p className={`text-xs mt-2 ${a.rmd_required ? "text-red-700 dark:text-red-300" : "text-amber-700 dark:text-amber-300"}`}>{a.action}</p>
                    </div>
                    {a.estimated_rmd > 0 && (
                      <div className="text-right flex-shrink-0">
                        <div className="text-lg font-bold text-red-700 dark:text-red-300">{fmt(a.estimated_rmd)}</div>
                        <div className="text-xs text-neutral-400">required by Dec 31</div>
                      </div>
                    )}
                  </div>
                </div>
              ))}
            </div>
          )}
          <InfoNote text={rmd.guidance} />
        </div>

        {/* Gift Tracker */}
        <div className="bg-white dark:bg-neutral-800 border border-neutral-200 dark:border-neutral-700 rounded-xl p-4">
          <SectionHeader icon={Gift} title="Annual gift exclusion tracker" />
          <div className="text-xs text-neutral-700 dark:text-neutral-300 bg-neutral-50 dark:bg-neutral-900 border border-neutral-200 dark:border-neutral-700 rounded-lg p-3 mb-3">
            <p>{gift.action}</p>
          </div>
          <div className="grid grid-cols-2 sm:grid-cols-3 gap-3 text-xs">
            <div className="bg-emerald-50 dark:bg-emerald-900/20 border border-emerald-200 dark:border-emerald-800 rounded-lg p-3">
              <div className="text-neutral-500 mb-1">Per donor / per recipient</div>
              <div className="text-lg font-bold text-emerald-700 dark:text-emerald-300">{fmt(gift.annual_exclusion_per_donor)}</div>
              {gift.gift_splitting_available && <div className="text-emerald-600 dark:text-emerald-400 mt-0.5">Gift-splitting: {fmt(gift.effective_annual_per_recipient)}/recipient</div>}
            </div>
            <div className="bg-blue-50 dark:bg-blue-900/20 border border-blue-200 dark:border-blue-800 rounded-lg p-3">
              <div className="text-neutral-500 mb-1">Max exclusion gifts this year</div>
              <div className="text-lg font-bold text-blue-700 dark:text-blue-300">{fmt(gift.max_annual_exclusion_gifts)}</div>
              <div className="text-blue-600 dark:text-blue-400 mt-0.5">{gift.donors} donor(s)</div>
            </div>
            {gift["529_superfunding"] && (
              <div className="bg-purple-50 dark:bg-purple-900/20 border border-purple-200 dark:border-purple-800 rounded-lg p-3">
                <div className="text-neutral-500 mb-1">529 superfunding</div>
                <div className="text-lg font-bold text-purple-700 dark:text-purple-300">{fmt(gift["529_superfunding"].max_per_recipient)}</div>
                <div className="text-purple-600 dark:text-purple-400 mt-0.5">per beneficiary (5-yr front-load)</div>
              </div>
            )}
          </div>
          {gift["529_superfunding"] && <InfoNote text={gift["529_superfunding"].action} />}
          <div className="mt-3 grid grid-cols-2 gap-2 text-xs">
            <div className="bg-neutral-50 dark:bg-neutral-900 border border-neutral-200 dark:border-neutral-700 rounded p-2">
              <div className="font-medium text-neutral-700 dark:text-neutral-300">Qualified tuition payments</div>
              <div className="text-neutral-500">{gift.qualified_exclusions?.tuition}</div>
            </div>
            <div className="bg-neutral-50 dark:bg-neutral-900 border border-neutral-200 dark:border-neutral-700 rounded p-2">
              <div className="font-medium text-neutral-700 dark:text-neutral-300">Qualified medical payments</div>
              <div className="text-neutral-500">{gift.qualified_exclusions?.medical}</div>
            </div>
          </div>
        </div>

        {/* Roth Conversion */}
        <div className="bg-white dark:bg-neutral-800 border border-neutral-200 dark:border-neutral-700 rounded-xl p-4">
          <SectionHeader icon={PiggyBank} title="Roth conversion opportunity" count={roth.candidates.length} badge={roth.candidates.filter((c: any) => c.attractive).length > 0 ? { text: "Opportunities identified", color: "bg-emerald-100 dark:bg-emerald-900/30 text-emerald-700 dark:text-emerald-300" } : undefined} />
          {roth.total_ira_balance > 0 && <div className="text-xs text-neutral-500 mb-3">Total IRA/401(k) balance: <strong>{fmt(roth.total_ira_balance)}</strong></div>}
          {roth.candidates.length === 0 ? <div className="text-sm text-neutral-400 py-4 text-center">No retirement account holders identified.</div> : (
            <div className="space-y-3">
              {roth.candidates.map((c: any, i: number) => (
                <div key={i} className={`rounded-lg border p-3 ${c.attractive ? "border-emerald-200 dark:border-emerald-800 bg-emerald-50 dark:bg-emerald-900/20" : "border-neutral-200 dark:border-neutral-700 bg-neutral-50 dark:bg-neutral-900"}`}>
                  <div className="flex items-start justify-between gap-2">
                    <div className="flex-1">
                      <div className="font-medium text-sm">{c.person_name} <span className="text-xs text-neutral-500 font-normal">{c.marginal_rate_pct}% marginal rate</span></div>
                      <div className="text-xs text-neutral-500 mt-1">IRA: {fmt(c.ira_balance)} · Roth: {fmt(c.roth_balance)}</div>
                      <p className={`text-xs mt-2 ${c.attractive ? "text-emerald-700 dark:text-emerald-300" : "text-neutral-600 dark:text-neutral-400"}`}>{c.action}</p>
                    </div>
                    {c.recommended_conversion > 0 && (
                      <div className="text-right flex-shrink-0">
                        <div className="text-base font-bold text-emerald-700 dark:text-emerald-300">{fmt(c.recommended_conversion)}</div>
                        <div className="text-xs text-neutral-400">convert this year</div>
                      </div>
                    )}
                  </div>
                </div>
              ))}
            </div>
          )}
          <InfoNote text={roth.guidance} />
        </div>

        {/* Withdrawal Sequencing */}
        <div className="bg-white dark:bg-neutral-800 border border-neutral-200 dark:border-neutral-700 rounded-xl p-4">
          <SectionHeader icon={ArrowDownUp} title="Tax-efficient withdrawal sequencing" count={data.withdrawal_sequencing.count} />
          <WithdrawalPanel data={data.withdrawal_sequencing} currency="USD" />
        </div>
      </div>
    </>
  );
}

// ════════════════════════════════════════════════════════════════════════════════
// UK PANELS
// ════════════════════════════════════════════════════════════════════════════════

function UKResultView({ data }: { data: UKResult & { total_portfolio_value: number; generated_at: string } }) {
  const fmt = makeFmt("GBP");
  const cgt = data.cgt_allowance;
  const isa = data.isa_allowance;
  const pension = data.pension_allowance;
  const pet = data.pet_clock;
  const div = data.dividend_allowance;

  return (
    <>
      <div className="grid grid-cols-2 sm:grid-cols-4 gap-3">
        <SummaryCard icon={TrendingDown} label="CGT exposure" value={cgt.flag ? fmt(cgt.net_taxable_gains) : "Within exempt amount"} sub={cgt.flag ? `Est. tax: ${fmt(cgt.estimated_cgt_at_higher_rate)}` : `£${cgt.annual_exempt_amount.toLocaleString()} exempt/person`} color={cgt.flag ? "bg-amber-100 dark:bg-amber-900/40 text-amber-600" : "bg-emerald-100 dark:bg-emerald-900/40 text-emerald-600 dark:text-emerald-400"} />
        <SummaryCard icon={PiggyBank} label="Total ISA value" value={fmt(isa.total_isa_value)} sub={`£${isa.annual_allowance.toLocaleString()}/person allowance`} color="bg-blue-100 dark:bg-blue-900/40 text-blue-600 dark:text-blue-400" />
        <SummaryCard icon={TrendingDown} label="Harvestable losses" value={fmt(cgt.total_harvestable_losses)} sub={`Offset gains before 5 April`} color="bg-red-100 dark:bg-red-900/40 text-red-600 dark:text-red-400" />
        <SummaryCard icon={AlertTriangle} label="Issues flagged" value={String(data.summary.total_flags)} sub={(data.summary.cgt_flag ? "CGT " : "") + (data.summary.dividend_flag ? "Dividend" : "")} color={data.summary.total_flags > 0 ? "bg-amber-100 dark:bg-amber-900/40 text-amber-600 dark:text-amber-400" : "bg-neutral-100 dark:bg-neutral-700 text-neutral-400"} />
      </div>

      <div className="space-y-4">
        {/* CGT Allowance */}
        <div className="bg-white dark:bg-neutral-800 border border-neutral-200 dark:border-neutral-700 rounded-xl p-4">
          <SectionHeader icon={TrendingDown} title="CGT annual exempt amount" badge={cgt.flag ? { text: "Allowance exceeded", color: "bg-amber-100 dark:bg-amber-900/30 text-amber-700 dark:text-amber-300" } : { text: "Within allowance", color: "bg-emerald-100 dark:bg-emerald-900/30 text-emerald-700 dark:text-emerald-300" }} />
          <div className="text-xs text-neutral-700 dark:text-neutral-300 bg-neutral-50 dark:bg-neutral-900 border border-neutral-200 dark:border-neutral-700 rounded-lg p-3 mb-3">{cgt.action}</div>
          <div className="grid grid-cols-3 gap-3 text-xs mb-3">
            <div className="bg-emerald-50 dark:bg-emerald-900/20 border border-emerald-200 dark:border-emerald-800 rounded-lg p-3">
              <div className="text-neutral-500 mb-1">Unrealised gains</div>
              <div className="text-lg font-bold text-emerald-700 dark:text-emerald-300">{fmt(cgt.total_unrealised_gains)}</div>
            </div>
            <div className="bg-red-50 dark:bg-red-900/20 border border-red-200 dark:border-red-800 rounded-lg p-3">
              <div className="text-neutral-500 mb-1">Harvestable losses</div>
              <div className="text-lg font-bold text-red-700 dark:text-red-300">{fmt(cgt.total_harvestable_losses)}</div>
            </div>
            <div className={`border rounded-lg p-3 ${cgt.flag ? "bg-amber-50 dark:bg-amber-900/20 border-amber-200 dark:border-amber-800" : "bg-neutral-50 dark:bg-neutral-900 border-neutral-200 dark:border-neutral-700"}`}>
              <div className="text-neutral-500 mb-1">Est. CGT (higher rate)</div>
              <div className={`text-lg font-bold ${cgt.flag ? "text-amber-700 dark:text-amber-300" : "text-neutral-400"}`}>{cgt.flag ? fmt(cgt.estimated_cgt_at_higher_rate) : "—"}</div>
            </div>
          </div>
          {cgt.gain_lots.length > 0 && <div className="mb-4"><p className="text-xs font-semibold text-neutral-500 mb-2 uppercase tracking-wide">Unrealised gains</p><LotTable lots={cgt.gain_lots} currency="GBP" gainMode /></div>}
          {cgt.loss_lots.length > 0 && <div><p className="text-xs font-semibold text-neutral-500 mb-2 uppercase tracking-wide">Harvestable losses</p><LotTable lots={cgt.loss_lots} currency="GBP" /></div>}
          <InfoNote text={cgt.key_changes} />
        </div>

        {/* ISA Allowance */}
        <div className="bg-white dark:bg-neutral-800 border border-neutral-200 dark:border-neutral-700 rounded-xl p-4">
          <SectionHeader icon={PiggyBank} title="ISA allowance tracker" />
          <div className="space-y-3">
            {isa.persons.map((p: any, i: number) => (
              <div key={i} className="rounded-lg border border-blue-200 dark:border-blue-800 bg-blue-50 dark:bg-blue-900/20 p-3">
                <div className="flex items-start justify-between gap-2">
                  <div>
                    <div className="font-medium text-sm">{p.person_name}</div>
                    <div className="text-xs text-neutral-500 mt-0.5">ISA value: {fmt(p.isa_value)} · Annual allowance: {fmt(p.annual_allowance)}</div>
                    <p className="text-xs text-blue-700 dark:text-blue-300 mt-2">{p.action}</p>
                  </div>
                </div>
              </div>
            ))}
          </div>
          {isa.junior_isa?.available && (
            <div className="mt-3 rounded-lg border border-purple-200 dark:border-purple-800 bg-purple-50 dark:bg-purple-900/20 p-3 text-xs">
              <div className="font-medium text-purple-700 dark:text-purple-300 mb-1">Junior ISA — {fmt(isa.junior_isa.allowance)}/year per child</div>
              <div className="text-neutral-500">Beneficiaries: {isa.junior_isa.beneficiaries.join(", ")}</div>
            </div>
          )}
          <InfoNote text={isa.guidance} />
        </div>

        {/* Pension Allowance */}
        <div className="bg-white dark:bg-neutral-800 border border-neutral-200 dark:border-neutral-700 rounded-xl p-4">
          <SectionHeader icon={Landmark} title="Pension annual allowance" />
          <div className="space-y-3">
            {pension.persons.map((p: any, i: number) => (
              <div key={i} className={`rounded-lg border p-3 ${p.tapered ? "border-red-300 dark:border-red-700 bg-red-50 dark:bg-red-900/20" : "border-emerald-200 dark:border-emerald-800 bg-emerald-50 dark:bg-emerald-900/20"}`}>
                <div className="flex items-start justify-between gap-2">
                  <div className="flex-1">
                    <div className="font-medium text-sm">{p.person_name} {p.tapered && <span className="text-xs font-normal text-red-600 dark:text-red-400 ml-1">⚠ Tapered</span>}</div>
                    <div className="text-xs text-neutral-500 mt-0.5">Pension pot: {fmt(p.pension_pot)} · Allowance: {fmt(p.effective_allowance)}{p.tapered ? ` (standard: ${fmt(p.standard_allowance)})` : ""}</div>
                    <p className={`text-xs mt-2 ${p.tapered ? "text-red-700 dark:text-red-300" : "text-emerald-700 dark:text-emerald-300"}`}>{p.action}</p>
                  </div>
                </div>
              </div>
            ))}
          </div>
          <InfoNote text={pension.guidance} />
        </div>

        {/* PET Clock */}
        <div className="bg-white dark:bg-neutral-800 border border-neutral-200 dark:border-neutral-700 rounded-xl p-4">
          <SectionHeader icon={Clock} title="PET 7-year survival clock" />
          <p className="text-xs text-neutral-700 dark:text-neutral-300 mb-4">{pet.planning_note}</p>
          <div className="mb-4">
            <p className="text-xs font-semibold text-neutral-500 mb-2 uppercase tracking-wide">IHT taper relief schedule</p>
            <div className="flex gap-1">
              {pet.taper_schedule.map((t: any, i: number) => (
                <div key={i} className={`flex-1 rounded text-center text-xs py-2 ${i === 0 ? "bg-red-100 dark:bg-red-900/40 text-red-700 dark:text-red-300" : i === pet.taper_schedule.length - 1 ? "bg-emerald-100 dark:bg-emerald-900/40 text-emerald-700 dark:text-emerald-300" : "bg-amber-100 dark:bg-amber-900/40 text-amber-700 dark:text-amber-300"}`}>
                  <div className="font-bold">{t.years_survived}yr</div>
                  <div>{t.iht_pct_payable}% IHT</div>
                </div>
              ))}
            </div>
          </div>
          <div className="grid grid-cols-2 gap-3 text-xs">
            <div className="bg-neutral-50 dark:bg-neutral-900 border border-neutral-200 dark:border-neutral-700 rounded-lg p-3">
              <div className="font-medium text-neutral-700 dark:text-neutral-300 mb-1">Annual exemption</div>
              <div className="text-lg font-bold text-emerald-600">£{pet.annual_exemption?.amount?.toLocaleString()}</div>
              <div className="text-neutral-500">{pet.annual_exemption?.note}</div>
            </div>
            <div className="bg-neutral-50 dark:bg-neutral-900 border border-neutral-200 dark:border-neutral-700 rounded-lg p-3">
              <div className="font-medium text-neutral-700 dark:text-neutral-300 mb-1">Small gift exemption</div>
              <div className="text-lg font-bold text-emerald-600">£{pet.small_gift_exemption?.amount?.toLocaleString()}</div>
              <div className="text-neutral-500">{pet.small_gift_exemption?.note}</div>
            </div>
          </div>
        </div>

        {/* Dividend Allowance */}
        <div className="bg-white dark:bg-neutral-800 border border-neutral-200 dark:border-neutral-700 rounded-xl p-4">
          <SectionHeader icon={DollarSign} title="Dividend allowance" badge={div.flag ? { text: "Allowance likely exceeded", color: "bg-amber-100 dark:bg-amber-900/30 text-amber-700 dark:text-amber-300" } : { text: "Within allowance", color: "bg-emerald-100 dark:bg-emerald-900/30 text-emerald-700 dark:text-emerald-300" }} />
          <div className="grid grid-cols-3 gap-3 text-xs mb-3">
            <div className="bg-neutral-50 dark:bg-neutral-900 border border-neutral-200 dark:border-neutral-700 rounded-lg p-3">
              <div className="text-neutral-500 mb-1">Annual allowance</div>
              <div className="text-lg font-bold text-neutral-700 dark:text-neutral-300">£{div.annual_allowance.toLocaleString()}</div>
              <div className="text-neutral-400">per person</div>
            </div>
            <div className="bg-blue-50 dark:bg-blue-900/20 border border-blue-200 dark:border-blue-800 rounded-lg p-3">
              <div className="text-neutral-500 mb-1">Est. annual dividends</div>
              <div className="text-lg font-bold text-blue-700 dark:text-blue-300">{fmt(div.estimated_annual_dividends)}</div>
              <div className="text-neutral-400">2.5% yield estimate</div>
            </div>
            {div.flag && (
              <div className="bg-amber-50 dark:bg-amber-900/20 border border-amber-200 dark:border-amber-800 rounded-lg p-3">
                <div className="text-neutral-500 mb-1">Est. tax (higher rate)</div>
                <div className="text-lg font-bold text-amber-700 dark:text-amber-300">{fmt(div.estimated_tax)}</div>
                <div className="text-neutral-400">33.75% rate</div>
              </div>
            )}
          </div>
          <p className="text-xs text-neutral-700 dark:text-neutral-300">{div.action}</p>
          <InfoNote text={div.key_changes} />
        </div>

        {/* Withdrawal Sequencing */}
        <div className="bg-white dark:bg-neutral-800 border border-neutral-200 dark:border-neutral-700 rounded-xl p-4">
          <SectionHeader icon={ArrowDownUp} title="Tax-efficient withdrawal sequencing" count={data.withdrawal_sequencing.count} />
          <WithdrawalPanel data={data.withdrawal_sequencing} currency="GBP" />
        </div>
      </div>
    </>
  );
}

// ════════════════════════════════════════════════════════════════════════════════
// Book view (multi-jurisdiction summary)
// ════════════════════════════════════════════════════════════════════════════════

function BookView({ data }: { data: BookResult }) {
  const fmtNZD = makeFmt("NZD");
  const fmtUSD = makeFmt("USD");
  const fmtGBP = makeFmt("GBP");
  const lh = data.loss_harvest;
  const pie = data.pie_optimisation;
  const ks = data.kiwisaver;
  const bl = data.bright_line;
  const us = data.us_households || [];
  const uk = data.uk_households || [];
  const hasMulti = us.length > 0 || uk.length > 0;

  return (
    <>
      <div className="grid grid-cols-2 sm:grid-cols-4 gap-3">
        <SummaryCard icon={Receipt} label="Households scanned" value={String(data.total_households_scanned)} sub={`as of ${data.generated_at}`} color="bg-blue-100 dark:bg-blue-900/40 text-blue-600 dark:text-blue-400" />
        <SummaryCard icon={TrendingDown} label="NZ harvestable losses" value={fmtNZD(lh.total_harvestable_loss)} sub={`${lh.count} lot(s)`} color="bg-red-100 dark:bg-red-900/40 text-red-600 dark:text-red-400" />
        <SummaryCard icon={Leaf} label="NZ est. tax saving" value={fmtNZD(lh.estimated_tax_saving + pie.total_annual_saving)} sub="loss-harvest + PIE" color="bg-emerald-100 dark:bg-emerald-900/40 text-emerald-600 dark:text-emerald-400" />
        <SummaryCard icon={AlertTriangle} label="Bright-line alerts" value={String(bl.count)} sub={bl.count > 0 ? "NZ properties" : "None"} color={bl.count > 0 ? "bg-amber-100 dark:bg-amber-900/40 text-amber-600" : "bg-neutral-100 dark:bg-neutral-700 text-neutral-400"} />
      </div>

      {hasMulti && (
        <div className="space-y-4">
          {us.length > 0 && (
            <div className="bg-white dark:bg-neutral-800 border border-neutral-200 dark:border-neutral-700 rounded-xl p-4">
              <SectionHeader icon={DollarSign} title={`US households (${us.length})`} />
              <div className="space-y-2">
                {us.map((h, i) => (
                  <div key={i} className="rounded-lg border border-neutral-200 dark:border-neutral-700 p-3 flex items-start justify-between gap-3">
                    <div>
                      <div className="font-medium text-sm">{h.household_name}</div>
                      <div className="flex gap-4 mt-1 text-xs text-neutral-500">
                        <span>Portfolio: {fmtUSD(h.total_portfolio_value)}</span>
                        {h.summary.rmd_required > 0 && <span className="text-red-600 dark:text-red-400">{h.summary.rmd_required} RMD active</span>}
                        {h.summary.roth_opportunity > 0 && <span className="text-emerald-600 dark:text-emerald-400">{h.summary.roth_opportunity} Roth opportunity</span>}
                        {h.summary.harvestable_loss > 0 && <span>{fmtUSD(h.summary.harvestable_loss)} harvestable losses</span>}
                      </div>
                    </div>
                    <div className="text-right text-xs">
                      <div className="font-semibold text-neutral-700 dark:text-neutral-300">{h.summary.total_flags} flag(s)</div>
                    </div>
                  </div>
                ))}
              </div>
            </div>
          )}

          {uk.length > 0 && (
            <div className="bg-white dark:bg-neutral-800 border border-neutral-200 dark:border-neutral-700 rounded-xl p-4">
              <SectionHeader icon={Landmark} title={`UK households (${uk.length})`} />
              <div className="space-y-2">
                {uk.map((h, i) => (
                  <div key={i} className="rounded-lg border border-neutral-200 dark:border-neutral-700 p-3 flex items-start justify-between gap-3">
                    <div>
                      <div className="font-medium text-sm">{h.household_name}</div>
                      <div className="flex gap-4 mt-1 text-xs text-neutral-500">
                        <span>Portfolio: {fmtGBP(h.total_portfolio_value)}</span>
                        {h.summary.cgt_flag && <span className="text-amber-600 dark:text-amber-400">CGT exposure</span>}
                        {h.summary.dividend_flag && <span className="text-amber-600 dark:text-amber-400">Dividend allowance exceeded</span>}
                        {h.summary.harvestable_loss > 0 && <span>{fmtGBP(h.summary.harvestable_loss)} harvestable losses</span>}
                      </div>
                    </div>
                    <div className="text-right text-xs">
                      <div className="font-semibold text-neutral-700 dark:text-neutral-300">{h.summary.total_flags} flag(s)</div>
                    </div>
                  </div>
                ))}
              </div>
            </div>
          )}
        </div>
      )}

      {/* NZ detail panels */}
      {(lh.count > 0 || pie.count > 0 || ks.count > 0 || bl.count > 0) && (
        <div className="space-y-4">
          <div className="bg-white dark:bg-neutral-800 border border-neutral-200 dark:border-neutral-700 rounded-xl p-4">
            <SectionHeader icon={TrendingDown} title="NZ loss-harvesting opportunities" count={lh.count} saving={lh.count > 0 ? `${fmtNZD(lh.estimated_tax_saving)} (top PIR)` : undefined} />
            <LotTable lots={lh.opportunities as any} currency="NZD" />
          </div>
          {pie.count > 0 && (
            <div className="bg-white dark:bg-neutral-800 border border-neutral-200 dark:border-neutral-700 rounded-xl p-4">
              <SectionHeader icon={Leaf} title="NZ PIE fund regime" count={pie.count} saving={`${fmtNZD(pie.total_annual_saving)}/yr`} />
              <div className="space-y-2">{pie.flags.map((f: any, i: number) => <div key={i} className="rounded-lg border border-emerald-200 dark:border-emerald-800 bg-emerald-50 dark:bg-emerald-900/20 p-3 text-xs"><div className="font-medium">{f.household_name} — {f.person_name} · {f.mandate_name}</div><p className="text-emerald-700 dark:text-emerald-300 mt-1">{f.action}</p></div>)}</div>
            </div>
          )}
        </div>
      )}
    </>
  );
}

// ════════════════════════════════════════════════════════════════════════════════
// Main page
// ════════════════════════════════════════════════════════════════════════════════

type Mode = "book" | "household";

export default function TaxIntelligencePage() {
  const [mode, setMode] = useState<Mode>("book");
  const [households, setHouseholds] = useState<Household[]>([]);
  const [selectedHH, setSelectedHH] = useState<string>("");
  const [bookData, setBookData] = useState<BookResult | null>(null);
  const [hhData, setHhData] = useState<HouseholdResult | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    api("/api/core/households").then(setHouseholds).catch(() => {});
  }, []);

  const loadBook = useCallback(async () => {
    setLoading(true); setError(null);
    try { setBookData(await api("/api/analytics/tax-book")); }
    catch (e: any) { setError(e.message || "Failed to load book data"); }
    finally { setLoading(false); }
  }, []);

  const loadHousehold = useCallback(async (id: string) => {
    if (!id) return;
    setLoading(true); setError(null);
    try { setHhData(await api(`/api/core/households/${id}/tax-intel`)); }
    catch (e: any) { setError(e.message || "Failed to load household tax data"); }
    finally { setLoading(false); }
  }, []);

  useEffect(() => {
    if (mode === "book") loadBook();
    else if (mode === "household" && selectedHH) loadHousehold(selectedHH);
  }, [mode, selectedHH, loadBook, loadHousehold]);

  const jurisdictionBadge = hhData ? {
    US: "🇺🇸 US — SEC/FINRA",
    UK: "🇬🇧 UK — FCA",
    NZ: "🇳🇿 NZ — FMA",
  }[(hhData as any).jurisdiction] : null;

  return (
    <div className="p-6 space-y-6 max-w-7xl mx-auto">
      <div className="flex items-start justify-between flex-wrap gap-3">
        <div>
          <div className="flex items-center gap-2 mb-1">
            <Receipt className="w-5 h-5 text-neutral-400" />
            <h1 className="text-xl font-semibold text-neutral-900 dark:text-neutral-100">Tax Intelligence</h1>
            {jurisdictionBadge && <span className="text-xs bg-neutral-100 dark:bg-neutral-700 text-neutral-600 dark:text-neutral-300 px-2 py-0.5 rounded-full">{jurisdictionBadge}</span>}
          </div>
          <p className="text-sm text-neutral-500 dark:text-neutral-400">
            Jurisdiction-dispatched tax optimisation — NZ (PIE / KiwiSaver / bright-line), US (CGT / RMD / Roth), UK (CGT allowance / ISA / pension).
          </p>
        </div>
        <button onClick={() => mode === "book" ? loadBook() : loadHousehold(selectedHH)} disabled={loading}
          className="flex items-center gap-1.5 text-sm px-3 py-1.5 rounded-lg border border-neutral-200 dark:border-neutral-700 hover:bg-neutral-50 dark:hover:bg-neutral-800 disabled:opacity-50">
          <RefreshCw className={`w-3.5 h-3.5 ${loading ? "animate-spin" : ""}`} />
          Refresh
        </button>
      </div>

      <div className="flex items-center gap-3 flex-wrap">
        <div className="flex rounded-lg border border-neutral-200 dark:border-neutral-700 overflow-hidden text-sm">
          <button className={`px-4 py-1.5 font-medium transition-colors ${mode === "book" ? "bg-[#163a52] text-white" : "text-neutral-600 dark:text-neutral-400 hover:bg-neutral-50 dark:hover:bg-neutral-800"}`} onClick={() => setMode("book")}>Full book</button>
          <button className={`px-4 py-1.5 font-medium transition-colors ${mode === "household" ? "bg-[#163a52] text-white" : "text-neutral-600 dark:text-neutral-400 hover:bg-neutral-50 dark:hover:bg-neutral-800"}`} onClick={() => setMode("household")}>Single household</button>
        </div>
        {mode === "household" && (
          <select value={selectedHH} onChange={(e) => setSelectedHH(e.target.value)}
            className="text-sm border border-neutral-200 dark:border-neutral-700 rounded-lg px-3 py-1.5 bg-white dark:bg-neutral-800 text-neutral-900 dark:text-neutral-100">
            <option value="">Select household…</option>
            {households.map((h) => <option key={h.id} value={h.id}>{h.name}</option>)}
          </select>
        )}
      </div>

      {error && (
        <div className="text-sm text-red-600 dark:text-red-400 bg-red-50 dark:bg-red-900/20 border border-red-200 dark:border-red-800 rounded-lg px-4 py-2">{error}</div>
      )}
      {loading && (
        <div className="text-sm text-neutral-500 dark:text-neutral-400 py-8 text-center animate-pulse">
          Scanning {mode === "book" ? "all mandates" : "household"} for tax opportunities…
        </div>
      )}

      {!loading && mode === "book" && bookData && <BookView data={bookData} />}

      {!loading && mode === "household" && hhData && (() => {
        const jur = (hhData as any).jurisdiction;
        if (jur === "US") return <USResultView data={hhData as USResult & any} />;
        if (jur === "UK") return <UKResultView data={hhData as UKResult & any} />;
        return <NZResultView data={hhData as NZResult & any} />;
      })()}

      {!loading && !bookData && !hhData && mode === "household" && !selectedHH && (
        <div className="text-center py-16 text-neutral-400 dark:text-neutral-500">
          <Receipt className="w-8 h-8 mx-auto mb-2 opacity-40" />
          <p>Select a household to view tax intelligence.</p>
        </div>
      )}
    </div>
  );
}
