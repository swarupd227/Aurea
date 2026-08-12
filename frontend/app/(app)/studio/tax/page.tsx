"use client";
import { useEffect, useState, useCallback } from "react";
import { api } from "@/lib/api";
import {
  Receipt, AlertTriangle, TrendingDown, Leaf, Landmark, ArrowDownUp,
  ChevronRight, RefreshCw, CheckCircle2, Info, ArrowRight, Home,
} from "lucide-react";

// ── Types ─────────────────────────────────────────────────────────────────────

interface LossOpportunity {
  account_name: string; mandate_name: string; symbol: string; instrument_name: string;
  quantity: number; cost_per_unit: number; current_price: number;
  unrealised_loss: number; unrealised_loss_pct: number;
  acquired_on: string; holding_days: number; wash_sale_risk: boolean;
  household_id?: string; household_name?: string;
}

interface PieFlag {
  person_name: string; mandate_name: string;
  marginal_rate_pct: number; pir_pct: number;
  mandate_value: number; estimated_annual_saving: number; action: string;
  household_id?: string; household_name?: string;
}

interface KsRecommendation {
  person_name: string; annual_income: number;
  current_rate_pct: number; recommended_rate_pct: number;
  current_govt_topup: number; max_govt_topup: number; employer_match_annual: number;
  action: string;
  household_id?: string; household_name?: string;
}

interface BrightLineFlag {
  person_name: string; property_address: string; property_value: number | null;
  acquired_on: string; days_held: number; test_years: number;
  days_until_safe: number; months_until_safe: number;
  status: string; action: string;
  household_id?: string; household_name?: string;
}

interface WithdrawalSeq {
  mandate_name: string; mandate_type: string; account_type: string;
  value: number; withdrawal_priority: number; priority_label: string; rationale: string;
}

interface HouseholdTaxResult {
  household_id: string; household_name: string; total_portfolio_value: number;
  summary: { total_flags: number; harvestable_loss: number; estimated_tax_saving: number; bright_line_flags: number };
  loss_harvest: { opportunities: LossOpportunity[]; count: number; total_harvestable_loss: number; estimated_tax_saving_at_top_pir: number };
  pie_optimisation: { flags: PieFlag[]; count: number; total_annual_saving: number };
  kiwisaver: { recommendations: KsRecommendation[]; count: number };
  bright_line: { flags: BrightLineFlag[]; count: number };
  withdrawal_sequencing: { sequences: WithdrawalSeq[]; count: number; guidance: string };
  generated_at: string;
}

interface BookResult {
  total_households_scanned: number; generated_at: string;
  loss_harvest: { opportunities: LossOpportunity[]; count: number; total_harvestable_loss: number; estimated_tax_saving: number };
  pie_optimisation: { flags: PieFlag[]; count: number; total_annual_saving: number };
  kiwisaver: { recommendations: KsRecommendation[]; count: number };
  bright_line: { flags: BrightLineFlag[]; count: number };
}

interface Household { id: string; name: string; total_value: number }

// ── Utilities ─────────────────────────────────────────────────────────────────

const fmt = (n: number) =>
  new Intl.NumberFormat("en-NZ", { style: "currency", currency: "NZD", maximumFractionDigits: 0 }).format(n);

const fmtPct = (n: number) => `${n.toFixed(1)}%`;

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

function SectionHeader({ icon: Icon, title, count, saving }: { icon: any; title: string; count: number; saving?: string }) {
  return (
    <div className="flex items-center justify-between mb-3">
      <div className="flex items-center gap-2">
        <Icon className="w-4 h-4 text-neutral-400" />
        <h3 className="font-semibold text-neutral-900 dark:text-neutral-100">{title}</h3>
        <span className="text-xs bg-neutral-100 dark:bg-neutral-700 text-neutral-600 dark:text-neutral-300 px-2 py-0.5 rounded-full">{count}</span>
      </div>
      {saving && <span className="text-xs text-emerald-600 dark:text-emerald-400 font-medium">{saving} potential saving</span>}
    </div>
  );
}

function LossHarvestPanel({ data }: { data: HouseholdTaxResult["loss_harvest"] | BookResult["loss_harvest"] }) {
  if (data.count === 0)
    return <div className="text-sm text-neutral-400 py-4 text-center">No harvestable losses identified.</div>;

  const opportunities = (data as any).opportunities as LossOpportunity[];
  return (
    <div className="overflow-x-auto">
      <table className="w-full text-sm">
        <thead>
          <tr className="border-b border-neutral-200 dark:border-neutral-700">
            {["household" in opportunities[0] ? "Household" : null, "Mandate / Account", "Instrument", "Qty", "Cost", "Price", "Unrealised Loss", "Held", ""].filter(Boolean)
              .map((h) => (
                <th key={h!} className="text-left py-2 px-2 text-xs font-medium text-neutral-500 dark:text-neutral-400 whitespace-nowrap">{h}</th>
              ))}
          </tr>
        </thead>
        <tbody>
          {opportunities.map((o, i) => (
            <tr key={i} className="border-b border-neutral-100 dark:border-neutral-800 hover:bg-neutral-50 dark:hover:bg-neutral-750">
              {o.household_name && (
                <td className="py-2 px-2 text-neutral-700 dark:text-neutral-300 text-xs">{o.household_name}</td>
              )}
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
                <span className="text-red-600 dark:text-red-400 font-semibold font-mono text-xs">{fmt(o.unrealised_loss)}</span>
                <div className="text-red-400 text-xs">{fmtPct(o.unrealised_loss_pct)}</div>
              </td>
              <td className="py-2 px-2 text-right text-neutral-500 text-xs">{o.holding_days}d</td>
              <td className="py-2 px-2">
                {o.wash_sale_risk && (
                  <span className="text-xs bg-amber-100 dark:bg-amber-900/40 text-amber-700 dark:text-amber-300 px-1.5 py-0.5 rounded font-medium">Wash-sale risk</span>
                )}
              </td>
            </tr>
          ))}
        </tbody>
      </table>
      <div className="mt-3 flex gap-6 text-sm text-neutral-600 dark:text-neutral-400 border-t border-neutral-100 dark:border-neutral-800 pt-3">
        <span>Total harvestable loss: <strong className="text-red-600 dark:text-red-400">{fmt(data.total_harvestable_loss)}</strong></span>
        <span>Est. tax saving at top PIR: <strong className="text-emerald-600 dark:text-emerald-400">
          {fmt("estimated_tax_saving_at_top_pir" in data ? data.estimated_tax_saving_at_top_pir : (data as any).estimated_tax_saving)}
        </strong></span>
      </div>
    </div>
  );
}

function PiePanel({ data }: { data: HouseholdTaxResult["pie_optimisation"] | BookResult["pie_optimisation"] }) {
  if (data.count === 0)
    return <div className="text-sm text-neutral-400 py-4 text-center">No PIE fund mismatches identified.</div>;
  return (
    <div className="space-y-3">
      {data.flags.map((f, i) => (
        <div key={i} className="rounded-lg border border-emerald-200 dark:border-emerald-800 bg-emerald-50 dark:bg-emerald-900/20 p-3">
          <div className="flex items-start justify-between gap-2">
            <div>
              {f.household_name && <div className="text-xs text-neutral-500 dark:text-neutral-400 mb-0.5">{f.household_name}</div>}
              <div className="font-medium text-neutral-800 dark:text-neutral-200 text-sm">{f.person_name} — {f.mandate_name}</div>
              <div className="text-xs text-neutral-500 dark:text-neutral-400 mt-1">
                Marginal rate {fmtPct(f.marginal_rate_pct)} vs PIR {fmtPct(f.pir_pct)} · Mandate value {fmt(f.mandate_value)}
              </div>
              <p className="text-xs text-emerald-700 dark:text-emerald-300 mt-2">{f.action}</p>
            </div>
            <div className="text-right flex-shrink-0">
              <div className="text-lg font-bold text-emerald-700 dark:text-emerald-300">{fmt(f.estimated_annual_saving)}</div>
              <div className="text-xs text-neutral-400">per year</div>
            </div>
          </div>
        </div>
      ))}
      {data.count > 0 && (
        <div className="text-sm text-neutral-600 dark:text-neutral-400 pt-1">
          Total annual saving potential: <strong className="text-emerald-600 dark:text-emerald-400">{fmt(data.total_annual_saving)}</strong>
        </div>
      )}
    </div>
  );
}

function KiwiSaverPanel({ data }: { data: HouseholdTaxResult["kiwisaver"] | BookResult["kiwisaver"] }) {
  if (data.count === 0)
    return <div className="text-sm text-neutral-400 py-4 text-center">No KiwiSaver optimisation identified.</div>;
  return (
    <div className="space-y-3">
      {data.recommendations.map((r, i) => (
        <div key={i} className="rounded-lg border border-blue-200 dark:border-blue-800 bg-blue-50 dark:bg-blue-900/20 p-3">
          {r.household_name && <div className="text-xs text-neutral-500 dark:text-neutral-400 mb-0.5">{r.household_name}</div>}
          <div className="flex items-start justify-between gap-4">
            <div className="flex-1">
              <div className="font-medium text-neutral-800 dark:text-neutral-200 text-sm">{r.person_name}</div>
              <div className="text-xs text-neutral-500 dark:text-neutral-400 mt-1">
                Income: {fmt(r.annual_income)} · Current rate: {r.current_rate_pct}%
                {r.current_rate_pct !== r.recommended_rate_pct &&
                  <> → <span className="text-blue-600 dark:text-blue-300 font-medium">{r.recommended_rate_pct}% recommended</span></>}
              </div>
              <p className="text-xs text-blue-700 dark:text-blue-300 mt-2">{r.action}</p>
            </div>
            <div className="text-right flex-shrink-0 space-y-1">
              <div>
                <div className="text-xs text-neutral-400">Govt top-up</div>
                <div className="font-semibold text-blue-700 dark:text-blue-300">{fmt(r.max_govt_topup)}/yr</div>
              </div>
              <div>
                <div className="text-xs text-neutral-400">Employer match</div>
                <div className="font-semibold text-blue-700 dark:text-blue-300">{fmt(r.employer_match_annual)}/yr</div>
              </div>
            </div>
          </div>
        </div>
      ))}
    </div>
  );
}

function BrightLinePanel({ data }: { data: HouseholdTaxResult["bright_line"] | BookResult["bright_line"] }) {
  if (data.count === 0)
    return <div className="text-sm text-neutral-400 py-4 text-center">No bright-line alerts identified.</div>;
  return (
    <div className="space-y-3">
      {data.flags.map((f, i) => (
        <div key={i} className={`rounded-lg border p-3 ${
          f.status === "within_bright_line"
            ? "border-red-300 dark:border-red-700 bg-red-50 dark:bg-red-900/20"
            : "border-amber-200 dark:border-amber-700 bg-amber-50 dark:bg-amber-900/20"
        }`}>
          {f.household_name && <div className="text-xs text-neutral-500 dark:text-neutral-400 mb-0.5">{f.household_name}</div>}
          <div className="flex items-start gap-3">
            <Home className={`w-4 h-4 mt-0.5 flex-shrink-0 ${
              f.status === "within_bright_line" ? "text-red-500" : "text-amber-500"
            }`} />
            <div className="flex-1">
              <div className="flex items-center gap-2 flex-wrap">
                <span className="font-medium text-neutral-800 dark:text-neutral-200 text-sm">{f.person_name}</span>
                <span className="text-xs text-neutral-500 dark:text-neutral-400">{f.property_address}</span>
                {f.property_value && (
                  <span className="text-xs text-neutral-500 dark:text-neutral-400">{fmt(f.property_value)}</span>
                )}
              </div>
              <div className="flex gap-4 mt-1 text-xs text-neutral-500 dark:text-neutral-400">
                <span>Acquired {f.acquired_on}</span>
                <span>{f.days_held} days held</span>
                <span>{f.test_years}-year bright-line</span>
                {f.months_until_safe > 0 && (
                  <span className="font-medium text-amber-600 dark:text-amber-400">
                    {f.months_until_safe.toFixed(1)} months until safe
                  </span>
                )}
              </div>
              <p className={`text-xs mt-2 ${
                f.status === "within_bright_line"
                  ? "text-red-700 dark:text-red-300"
                  : "text-amber-700 dark:text-amber-300"
              }`}>{f.action}</p>
            </div>
          </div>
        </div>
      ))}
    </div>
  );
}

function WithdrawalPanel({ data }: { data: HouseholdTaxResult["withdrawal_sequencing"] }) {
  if (data.count === 0)
    return <div className="text-sm text-neutral-400 py-4 text-center">No mandates to sequence.</div>;

  const colors: Record<number, string> = {
    1: "bg-red-100 dark:bg-red-900/30 text-red-700 dark:text-red-300 border-red-200 dark:border-red-800",
    2: "bg-orange-100 dark:bg-orange-900/30 text-orange-700 dark:text-orange-300 border-orange-200 dark:border-orange-800",
    3: "bg-blue-100 dark:bg-blue-900/30 text-blue-700 dark:text-blue-300 border-blue-200 dark:border-blue-800",
    4: "bg-emerald-100 dark:bg-emerald-900/30 text-emerald-700 dark:text-emerald-300 border-emerald-200 dark:border-emerald-800",
  };

  return (
    <div className="space-y-2">
      <p className="text-xs text-neutral-500 dark:text-neutral-400 mb-3">{data.guidance}</p>
      {data.sequences.map((s, i) => (
        <div key={i} className={`rounded-lg border p-3 ${colors[s.withdrawal_priority]}`}>
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

// ── Main page ─────────────────────────────────────────────────────────────────

type Mode = "book" | "household";

export default function TaxIntelligencePage() {
  const [mode, setMode] = useState<Mode>("book");
  const [households, setHouseholds] = useState<Household[]>([]);
  const [selectedHH, setSelectedHH] = useState<string>("");
  const [bookData, setBookData] = useState<BookResult | null>(null);
  const [hhData, setHhData] = useState<HouseholdTaxResult | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    api("/api/core/households").then(setHouseholds).catch(() => {});
  }, []);

  const loadBook = useCallback(async () => {
    setLoading(true); setError(null);
    try {
      setBookData(await api("/api/analytics/tax-book"));
    } catch (e: any) {
      setError(e.message || "Failed to load book data");
    } finally {
      setLoading(false);
    }
  }, []);

  const loadHousehold = useCallback(async (id: string) => {
    if (!id) return;
    setLoading(true); setError(null);
    try {
      setHhData(await api(`/api/core/households/${id}/tax-intel`));
    } catch (e: any) {
      setError(e.message || "Failed to load household tax data");
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    if (mode === "book") loadBook();
    else if (mode === "household" && selectedHH) loadHousehold(selectedHH);
  }, [mode, selectedHH, loadBook, loadHousehold]);

  const activeData = mode === "book" ? bookData : hhData;

  return (
    <div className="p-6 space-y-6 max-w-7xl mx-auto">
      {/* Header */}
      <div className="flex items-start justify-between flex-wrap gap-3">
        <div>
          <div className="flex items-center gap-2 mb-1">
            <Receipt className="w-5 h-5 text-neutral-400" />
            <h1 className="text-xl font-semibold text-neutral-900 dark:text-neutral-100">Tax Intelligence</h1>
          </div>
          <p className="text-sm text-neutral-500 dark:text-neutral-400">
            Cross-book NZ tax optimisation: loss-harvest, PIE regime, KiwiSaver, bright-line property, and withdrawal sequencing.
          </p>
        </div>
        <button
          onClick={() => mode === "book" ? loadBook() : loadHousehold(selectedHH)}
          disabled={loading}
          className="flex items-center gap-1.5 text-sm px-3 py-1.5 rounded-lg border border-neutral-200 dark:border-neutral-700 hover:bg-neutral-50 dark:hover:bg-neutral-800 disabled:opacity-50"
        >
          <RefreshCw className={`w-3.5 h-3.5 ${loading ? "animate-spin" : ""}`} />
          Refresh
        </button>
      </div>

      {/* Mode toggle + household selector */}
      <div className="flex items-center gap-3 flex-wrap">
        <div className="flex rounded-lg border border-neutral-200 dark:border-neutral-700 overflow-hidden text-sm">
          <button
            className={`px-4 py-1.5 font-medium transition-colors ${mode === "book" ? "bg-[#163a52] text-white" : "text-neutral-600 dark:text-neutral-400 hover:bg-neutral-50 dark:hover:bg-neutral-800"}`}
            onClick={() => setMode("book")}
          >Full book</button>
          <button
            className={`px-4 py-1.5 font-medium transition-colors ${mode === "household" ? "bg-[#163a52] text-white" : "text-neutral-600 dark:text-neutral-400 hover:bg-neutral-50 dark:hover:bg-neutral-800"}`}
            onClick={() => setMode("household")}
          >Single household</button>
        </div>
        {mode === "household" && (
          <select
            value={selectedHH}
            onChange={(e) => setSelectedHH(e.target.value)}
            className="text-sm border border-neutral-200 dark:border-neutral-700 rounded-lg px-3 py-1.5 bg-white dark:bg-neutral-800 text-neutral-900 dark:text-neutral-100"
          >
            <option value="">Select household…</option>
            {households.map((h) => (
              <option key={h.id} value={h.id}>{h.name}</option>
            ))}
          </select>
        )}
      </div>

      {error && (
        <div className="text-sm text-red-600 dark:text-red-400 bg-red-50 dark:bg-red-900/20 border border-red-200 dark:border-red-800 rounded-lg px-4 py-2">
          {error}
        </div>
      )}

      {loading && (
        <div className="text-sm text-neutral-500 dark:text-neutral-400 py-8 text-center animate-pulse">
          Scanning {mode === "book" ? "all mandates" : "household"} for tax opportunities…
        </div>
      )}

      {!loading && activeData && (() => {
        const isBook = mode === "book";
        const bookResult = isBook ? (activeData as BookResult) : null;
        const hhResult = !isBook ? (activeData as HouseholdTaxResult) : null;

        const lh = activeData.loss_harvest;
        const pie = activeData.pie_optimisation;
        const ks = activeData.kiwisaver;
        const bl = activeData.bright_line;

        const totalEstSaving = isBook
          ? (lh as BookResult["loss_harvest"]).estimated_tax_saving + pie.total_annual_saving
          : (hhResult!.summary.estimated_tax_saving);

        return (
          <>
            {/* Summary cards */}
            <div className="grid grid-cols-2 sm:grid-cols-4 gap-3">
              <SummaryCard
                icon={TrendingDown}
                label="Harvestable losses"
                value={fmt(lh.total_harvestable_loss)}
                sub={`${lh.count} lot${lh.count !== 1 ? "s" : ""}`}
                color="bg-red-100 dark:bg-red-900/40 text-red-600 dark:text-red-400"
              />
              <SummaryCard
                icon={Leaf}
                label="Est. tax saving"
                value={fmt(totalEstSaving)}
                sub="loss-harvest + PIE"
                color="bg-emerald-100 dark:bg-emerald-900/40 text-emerald-600 dark:text-emerald-400"
              />
              <SummaryCard
                icon={AlertTriangle}
                label="Bright-line alerts"
                value={String(bl.count)}
                sub={bl.count > 0 ? "Review before any sale" : "No alerts"}
                color={bl.count > 0 ? "bg-amber-100 dark:bg-amber-900/40 text-amber-600 dark:text-amber-400" : "bg-neutral-100 dark:bg-neutral-700 text-neutral-400"}
              />
              <SummaryCard
                icon={Receipt}
                label={isBook ? "Households scanned" : "Total portfolio"}
                value={isBook ? String((bookResult!).total_households_scanned) : fmt(hhResult!.total_portfolio_value)}
                sub={isBook ? `as of ${bookResult!.generated_at}` : `${ks.count} KiwiSaver flag${ks.count !== 1 ? "s" : ""}`}
                color="bg-blue-100 dark:bg-blue-900/40 text-blue-600 dark:text-blue-400"
              />
            </div>

            {/* Panels */}
            <div className="space-y-4">

              {/* Loss harvest */}
              <div className="bg-white dark:bg-neutral-800 border border-neutral-200 dark:border-neutral-700 rounded-xl p-4">
                <SectionHeader
                  icon={TrendingDown}
                  title="Loss-harvesting opportunities"
                  count={lh.count}
                  saving={lh.count > 0 ? fmt(
                    "estimated_tax_saving_at_top_pir" in lh
                      ? (lh as any).estimated_tax_saving_at_top_pir
                      : (lh as any).estimated_tax_saving
                  ) : undefined}
                />
                <LossHarvestPanel data={lh} />
              </div>

              {/* PIE */}
              <div className="bg-white dark:bg-neutral-800 border border-neutral-200 dark:border-neutral-700 rounded-xl p-4">
                <SectionHeader
                  icon={Leaf}
                  title="PIE fund regime optimisation"
                  count={pie.count}
                  saving={pie.count > 0 ? `${fmt(pie.total_annual_saving)}/yr` : undefined}
                />
                <PiePanel data={pie} />
              </div>

              {/* KiwiSaver */}
              <div className="bg-white dark:bg-neutral-800 border border-neutral-200 dark:border-neutral-700 rounded-xl p-4">
                <SectionHeader icon={ArrowDownUp} title="KiwiSaver contribution optimisation" count={ks.count} />
                <KiwiSaverPanel data={ks} />
              </div>

              {/* Bright-line */}
              <div className="bg-white dark:bg-neutral-800 border border-neutral-200 dark:border-neutral-700 rounded-xl p-4">
                <SectionHeader icon={Home} title="Bright-line property tracker" count={bl.count} />
                <BrightLinePanel data={bl} />
              </div>

              {/* Withdrawal sequencing — household only */}
              {!isBook && hhResult && (
                <div className="bg-white dark:bg-neutral-800 border border-neutral-200 dark:border-neutral-700 rounded-xl p-4">
                  <SectionHeader
                    icon={ArrowDownUp}
                    title="Tax-efficient withdrawal sequencing"
                    count={hhResult.withdrawal_sequencing.count}
                  />
                  <WithdrawalPanel data={hhResult.withdrawal_sequencing} />
                </div>
              )}
            </div>
          </>
        );
      })()}

      {!loading && !activeData && mode === "household" && !selectedHH && (
        <div className="text-center py-16 text-neutral-400 dark:text-neutral-500">
          <Receipt className="w-8 h-8 mx-auto mb-2 opacity-40" />
          <p>Select a household to view tax intelligence.</p>
        </div>
      )}
    </div>
  );
}
