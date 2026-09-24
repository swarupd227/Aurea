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

interface CompositeReportPayload {
  name: string;
  strategy: string | null;
  accounts_included: number;
  composite_assets: number;
  gross_return: number | null;
  internal_dispersion: number | null;
  ex_post_std_dev: StdDev;
  benchmark: Benchmark | null;
}

function pct(n: number | null) {
  return n === null ? "—" : `${(n * 100).toFixed(2)}%`;
}
function money(n: number) {
  return n.toLocaleString(undefined, { style: "currency", currency: "USD", maximumFractionDigits: 0 });
}

export function CompositeReportCard({ result }: { result: CompositeReportPayload }) {
  const std = result.ex_post_std_dev;
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
          {result.benchmark && (
            <tr>
              <td className="px-3 py-1.5 text-ink-muted">Benchmark ({result.benchmark.symbol})</td>
              <td className="px-3 py-1.5 text-right font-mono tabular-nums text-ink-muted">{pct(result.benchmark.return)}</td>
            </tr>
          )}
        </tbody>
      </table>
    </div>
  );
}
