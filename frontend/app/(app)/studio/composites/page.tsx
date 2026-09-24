"use client";
import { AlertTriangle, Check, BarChart3, FileCheck } from "lucide-react";
import PageHeader from "@/components/PageHeader";
import { Card, Empty, Spinner } from "@/components/ui";
import { useApi } from "@/lib/hooks";

function pct(n: number | null) {
  return n === null || n === undefined ? "—" : `${(n * 100).toFixed(2)}%`;
}
function money(n: number) {
  return n.toLocaleString(undefined, { style: "currency", currency: "USD", maximumFractionDigits: 0 });
}

function CompositePresentation({ compositeId }: { compositeId: string }) {
  const { data, loading } = useApi<any>(`/api/composites/${compositeId}/report`, [compositeId]);
  if (loading || !data) return <Spinner />;

  const std = data.ex_post_std_dev;

  return (
    <Card>
      <div className="flex items-center justify-between mb-1">
        <div className="font-semibold text-ink flex items-center gap-2"><BarChart3 size={17} /> {data.name}</div>
        <span className="text-xs text-ink-muted">{data.strategy}</span>
      </div>
      <p className="text-xs text-ink-muted mb-3">{data.inclusion_criteria}</p>

      <div className="overflow-x-auto">
        <table className="w-full text-sm">
          <thead>
            <tr className="border-b border-navy-100">
              {["Accounts", "Composite assets", "Gross return", "Dispersion", "Ex-post std dev", "Benchmark"].map((h) => (
                <th key={h} className="text-left py-1.5 px-2 text-xs font-medium text-ink-muted whitespace-nowrap">{h}</th>
              ))}
            </tr>
          </thead>
          <tbody>
            <tr>
              <td className="py-1.5 px-2 tabular-nums">{data.accounts_included}</td>
              <td className="py-1.5 px-2 tabular-nums">{money(data.composite_assets)}</td>
              <td className="py-1.5 px-2 tabular-nums font-medium">{pct(data.gross_return)}</td>
              <td className="py-1.5 px-2 tabular-nums">{pct(data.internal_dispersion)}</td>
              <td className="py-1.5 px-2 tabular-nums">
                {pct(std.value)}
                <span className={`ml-1.5 text-[11px] ${std.meets_gips_minimum ? "text-positive" : "text-caution"}`}>
                  ({std.months_used}/{std.months_required_for_gips} mo{std.meets_gips_minimum ? "" : " — below GIPS minimum"})
                </span>
              </td>
              <td className="py-1.5 px-2 tabular-nums">
                {data.benchmark ? `${data.benchmark.symbol} ${pct(data.benchmark.return)}` : "not configured"}
              </td>
            </tr>
          </tbody>
        </table>
      </div>

      {(data.accounts_excluded_unseasoned.length > 0 || data.accounts_excluded_below_minimum.length > 0) && (
        <div className="mt-3 pt-3 border-t border-navy-50 text-xs text-ink-muted space-y-1">
          {data.accounts_excluded_unseasoned.length > 0 && (
            <div>Excluded, not yet seasoned: {data.accounts_excluded_unseasoned.join(", ")}</div>
          )}
          {data.accounts_excluded_below_minimum.length > 0 && (
            <div>Excluded, below minimum account size: {data.accounts_excluded_below_minimum.join(", ")}</div>
          )}
        </div>
      )}
    </Card>
  );
}

function FirmDefinitionCheck() {
  const { data, loading } = useApi<any>("/api/composites/firm-definition-check");
  if (loading || !data) return null;

  return (
    <Card>
      <div className="font-semibold text-ink mb-1 flex items-center gap-2"><FileCheck size={17} /> Firm definition check</div>
      <p className="text-xs text-ink-muted mb-3">
        GIPS's "foundational sin" per L200-5 §2.5: a firm definition narrowed to exclude bad history. Every
        discretionary account should be captured in a composite.
      </p>
      <div className="flex items-center gap-3">
        {data.clean
          ? <span className="chip bg-positive/10 text-positive"><Check size={12} /> Fully captured</span>
          : <span className="chip bg-caution/10 text-caution"><AlertTriangle size={12} /> {money(data.uncaptured_aum)} uncaptured</span>}
        <span className="text-xs text-ink-muted">
          {money(data.composite_eligible_aum)} of {money(data.discretionary_aum)} discretionary AUM captured
        </span>
      </div>
    </Card>
  );
}

export default function Composites() {
  const { data, loading } = useApi<any>("/api/composites");

  return (
    <div>
      <PageHeader title="Composites"
        sub="GIPS composite reporting by strategy (L200-5 §2.5) — firm definition, construction, dispersion, and an ex-post standard deviation honestly labelled by how many months of history it actually used." />

      <div className="space-y-5">
        <FirmDefinitionCheck />
        {loading && <Spinner />}
        {!loading && !data?.items?.length && <Empty>No composites defined yet.</Empty>}
        {!loading && data?.items?.map((c: any) => (
          <CompositePresentation key={c.id} compositeId={c.id} />
        ))}
      </div>
    </div>
  );
}
