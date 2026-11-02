"use client";

import { AlertTriangle, Check, BarChart3, FileCheck } from "lucide-react";
import { Card, Empty, Spinner } from "@/components/ui";
import { useApi } from "@/lib/hooks";
import { money } from "@/lib/format";
import { BenchmarkDisclosureView } from "@/app/workspace/components/artifacts/CompositeReportCard";
import { ViewHeader } from "./ViewHeader";

function pct(n: number | null) {
  return n === null || n === undefined ? "—" : `${(n * 100).toFixed(2)}%`;
}

const th = "text-left py-1.5 px-2 text-[10px] uppercase tracking-wide font-medium text-ink-muted whitespace-nowrap";
const td = "py-1.5 px-2 tabular-nums font-mono text-xs text-ink whitespace-nowrap";

function CompositePresentation({ compositeId }: { compositeId: string }) {
  const { data, loading } = useApi<any>(`/api/composites/${compositeId}/report`, [compositeId]);
  if (loading || !data) return <Spinner />;

  const std = data.ex_post_std_dev;

  return (
    <Card data-testid="composite-presentation">
      <div className="flex flex-wrap items-center justify-between gap-2 mb-2">
        <div className="font-semibold text-ink flex items-center gap-2"><BarChart3 size={15} className="text-ink-muted" /> {data.name}</div>
        <span className="chip text-ink-muted">{data.strategy}</span>
      </div>
      {data.inclusion_criteria && <div className="mb-3 flex flex-wrap gap-1.5"><span className="chip text-ink-muted">{data.inclusion_criteria}</span></div>}

      <div className="overflow-x-auto">
        <table className="w-full text-sm">
          <thead>
            <tr className="border-b border-border-soft">
              {["Accounts", "Composite assets", "Gross return", "Dispersion", "Ex-post std dev", "Benchmark"].map((h) => (
                <th key={h} className={th}>{h}</th>
              ))}
            </tr>
          </thead>
          <tbody>
            <tr>
              <td className={td}>{data.accounts_included}</td>
              <td className={td}>{money(data.composite_assets)}</td>
              <td className={`${td} font-medium`}>{pct(data.gross_return)}</td>
              <td className={td}>{pct(data.internal_dispersion)}</td>
              <td className={td}>
                {pct(std.value)}
                <span className={`ml-1.5 text-[11px] ${std.meets_gips_minimum ? "text-ok" : "text-warn"}`}>
                  ({std.months_used}/{std.months_required_for_gips} mo{std.meets_gips_minimum ? "" : " · below GIPS minimum"})
                </span>
              </td>
              <td className={td}>
                {data.benchmark
                  ? `${data.benchmark_disclosure?.label ?? data.benchmark.symbol} ${pct(data.benchmark.return)}`
                  : data.benchmark_disclosure?.label ? `${data.benchmark_disclosure.label} · not measurable` : "not configured"}
              </td>
            </tr>
          </tbody>
        </table>
      </div>

      {data.benchmark_disclosure && <div className="mt-3 -mx-3"><BenchmarkDisclosureView disclosure={data.benchmark_disclosure} /></div>}

      {(data.accounts_excluded_unseasoned.length > 0 || data.accounts_excluded_below_minimum.length > 0) && (
        <div className="mt-3 pt-3 border-t border-border-soft flex flex-wrap gap-1.5">
          {data.accounts_excluded_unseasoned.map((a: string) => (
            <span key={`u-${a}`} className="chip text-ink-muted">excluded, not seasoned · {a}</span>
          ))}
          {data.accounts_excluded_below_minimum.map((a: string) => (
            <span key={`m-${a}`} className="chip text-ink-muted">excluded, below minimum · {a}</span>
          ))}
        </div>
      )}
    </Card>
  );
}

function FirmDefinitionCheck() {
  const { data, loading } = useApi<any>("/api/composites/firm-definition-check");
  if (loading || !data) return null;

  return (
    <Card data-testid="composites-firm-definition">
      <div className="font-semibold text-ink mb-3 flex items-center gap-2"><FileCheck size={15} className="text-ink-muted" /> Firm definition check</div>
      <div className="flex flex-wrap items-center gap-1.5">
        {data.clean
          ? <span className="chip bg-ok-bg text-ok"><Check size={12} /> Fully captured</span>
          : <span className="chip bg-warn-bg text-warn"><AlertTriangle size={12} /> {money(data.uncaptured_aum)} uncaptured</span>}
        <span className="chip text-ink-muted tabular-nums">
          {money(data.composite_eligible_aum)} of {money(data.discretionary_aum)} discretionary captured
        </span>
      </div>
    </Card>
  );
}

/** GIPS composites by strategy: whether the firm definition captures every discretionary account,
 * and each composite's construction, dispersion and ex-post standard deviation, with the months of
 * history it used. Reads only. */
export function CompositesView() {
  const { data, loading } = useApi<any>("/api/composites");

  return (
    <div className="view-container">
      <ViewHeader
        title="Composites"
        meta={data?.items ? <span className="chip text-ink-muted tabular-nums">{data.items.length} composites · GIPS</span> : undefined}
      />

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
