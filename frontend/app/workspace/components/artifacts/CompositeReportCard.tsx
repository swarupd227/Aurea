interface StdDev {
  value: number | null;
  months_used: number;
  months_required_for_gips: number;
  meets_gips_minimum: boolean;
}

interface Benchmark {
  symbol: string;
  return: number;
}

interface BenchmarkChange {
  date: string | null;
  from_label: string;
  to_label: string;
  reason: string | null;
}

/** What the benchmark is and every change to it - a GIPS disclosure, shown measured or not. */
export interface BenchmarkDisclosure {
  label: string | null;
  basis: string | null;
  measured: boolean;
  changes: BenchmarkChange[];
}

/** The benchmark disclosure block: how a blend is computed, and every change with its date and reason. */
export function BenchmarkDisclosureView({ disclosure: d }: { disclosure: BenchmarkDisclosure }) {
  if (!d.basis && d.changes.length === 0) return null;
  return (
    <div className="space-y-1.5 border-t border-border-soft px-3 py-2" data-testid="benchmark-disclosure">
      <div className="font-mono text-[10px] uppercase tracking-wider text-ink-muted">Benchmark disclosure</div>
      {d.basis && <span className="inline-block rounded border border-border px-1.5 py-0.5 text-[11px] text-ink-soft">{d.basis}</span>}
      {d.changes.length > 0 && (
        <table className="w-full text-xs">
          <thead>
            <tr className="text-left text-ink-muted">
              <th className="py-0.5 pr-2 font-normal">Changed</th>
              <th className="py-0.5 pr-2 font-normal">From</th>
              <th className="py-0.5 pr-2 font-normal">To</th>
              <th className="py-0.5 font-normal">Reason</th>
            </tr>
          </thead>
          <tbody className="divide-y divide-border-soft">
            {d.changes.map((c, i) => (
              <tr key={i} className="align-top">
                <td className="py-1 pr-2 font-mono tabular-nums text-ink-soft">{c.date ?? "—"}</td>
                <td className="py-1 pr-2 text-ink-soft">{c.from_label}</td>
                <td className="py-1 pr-2 text-ink">{c.to_label}</td>
                <td className="py-1 text-ink-soft">{c.reason ?? "—"}</td>
              </tr>
            ))}
          </tbody>
        </table>
      )}
    </div>
  );
}

interface CompositeReportPayload {
  name: string;
  strategy: string | null;
  accounts_included: number;
  composite_assets: number;
  gross_return: number | null;
  internal_dispersion: number | null;
  ex_post_std_dev: StdDev;
  benchmark: Benchmark | null;
  benchmark_disclosure?: BenchmarkDisclosure;
}

function pct(n: number | null) {
  return n === null ? "—" : `${(n * 100).toFixed(2)}%`;
}
function money(n: number) {
  return n.toLocaleString(undefined, { style: "currency", currency: "USD", maximumFractionDigits: 0 });
}

export function CompositeReportCard({ result }: { result: CompositeReportPayload }) {
  const std = result.ex_post_std_dev;
  const bd = result.benchmark_disclosure;
  const label = bd?.label ?? result.benchmark?.symbol;
  return (
    <div className="overflow-hidden rounded-lg border border-border bg-surface">
      <div className="flex items-center justify-between border-b border-border-soft px-3 py-2">
        <div className="text-sm font-medium text-ink">{result.name}</div>
        <div className="font-mono text-xs tabular-nums text-ink-muted">{result.accounts_included} account(s)</div>
      </div>
      <table className="w-full text-sm">
        <tbody className="divide-y divide-border-soft">
          <tr>
            <td className="px-3 py-1.5 text-ink-muted">Composite assets</td>
            <td className="px-3 py-1.5 text-right font-mono tabular-nums text-ink">{money(result.composite_assets)}</td>
          </tr>
          <tr>
            <td className="px-3 py-1.5 text-ink-muted">Gross return</td>
            <td className="px-3 py-1.5 text-right font-mono tabular-nums text-ink">{pct(result.gross_return)}</td>
          </tr>
          <tr>
            <td className="px-3 py-1.5 text-ink-muted">Internal dispersion</td>
            <td className="px-3 py-1.5 text-right font-mono tabular-nums text-ink">{pct(result.internal_dispersion)}</td>
          </tr>
          <tr>
            <td className="px-3 py-1.5 text-ink-muted">Ex-post std dev</td>
            <td className="px-3 py-1.5 text-right font-mono tabular-nums text-ink">
              {pct(std.value)}
              <span className={`ml-1.5 text-[11px] ${std.meets_gips_minimum ? "text-ok" : "text-warn"}`}>
                ({std.months_used}/{std.months_required_for_gips}mo)
              </span>
            </td>
          </tr>
          {label && (
            <tr>
              <td className="px-3 py-1.5 text-ink-muted">Benchmark ({label})</td>
              <td className={`px-3 py-1.5 text-right font-mono tabular-nums ${result.benchmark ? "text-ink-muted" : "text-warn"}`}>
                {result.benchmark ? pct(result.benchmark.return) : "not measurable"}
              </td>
            </tr>
          )}
        </tbody>
      </table>
      {bd && <BenchmarkDisclosureView disclosure={bd} />}
    </div>
  );
}
