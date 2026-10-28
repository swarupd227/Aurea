interface Period {
  start: string;
  end: string;
  return: number;
}

interface Attribution {
  asset_class: string;
  contribution: number;
  class_return: number | null;
  weight: number;
}

interface Disclosures {
  synthetic_price_symbols: string[];
  declared_price_symbols?: string[];
  unpriced_flat_symbols: string[];
  ledger_gap_lots: number;
  periods_skipped: number;
  as_of: string | null;
  stale_days: number | null;
}

interface PerformancePayload {
  household_name?: string;
  twr: number | null;
  mwr: { period: number; annualised: number } | null;
  start: string | null;
  end: string | null;
  months: number;
  periods: Period[];
  attribution: Attribution[];
  benchmark_symbol: string | null;
  /** "70% SPY / 30% AGG" for a blend; older results carry only the symbol. */
  benchmark_label?: string | null;
  /** The benchmark the household is held to, when it has no price over this window. */
  benchmark_unmeasured?: string | null;
  benchmark_return: number | null;
  alpha: number | null;
  disclosures: Disclosures;
}

function pct(n: number | null | undefined, signed = false) {
  if (n === null || n === undefined) return "—";
  const v = (n * 100).toFixed(2) + "%";
  return signed && n > 0 ? `+${v}` : v;
}

function tone(n: number | null | undefined) {
  if (n === null || n === undefined) return "text-ink-muted";
  return n >= 0 ? "text-ok" : "text-crit";
}

function label(s: string) {
  return s.replace(/_/g, " ").replace(/^./, (c) => c.toUpperCase());
}

function Stat({ k, v, cls }: { k: string; v: string; cls?: string }) {
  return (
    <div className="min-w-[88px]">
      <div className="text-[10px] uppercase tracking-wide text-ink-muted">{k}</div>
      <div className={`font-mono text-base tabular-nums ${cls ?? "text-ink"}`}>{v}</div>
    </div>
  );
}

export function PerformanceCard({ result }: { result: PerformancePayload }) {
  const d = result.disclosures;
  const max = Math.max(0.0001, ...result.periods.map((p) => Math.abs(p.return)));
  const chips: { text: string; warn?: boolean }[] = [
    { text: "Securities only" },
    { text: "Gross of fees" },
    { text: "No deposits or withdrawals recorded" },
  ];
  if (d.synthetic_price_symbols.length) chips.push({ text: `Synthetic price: ${d.synthetic_price_symbols.join(", ")}`, warn: true });
  if (d.declared_price_symbols?.length) chips.push({ text: `Stated valuation: ${d.declared_price_symbols.join(", ")}` });
  if (d.unpriced_flat_symbols.length) chips.push({ text: `Held flat, no price history: ${d.unpriced_flat_symbols.join(", ")}`, warn: true });
  if (d.ledger_gap_lots > 0) chips.push({ text: `${d.ledger_gap_lots} purchase(s) not in the ledger`, warn: true });
  if (d.periods_skipped > 0) chips.push({ text: `${d.periods_skipped} month(s) skipped, no capital` });
  if (d.stale_days !== null && d.stale_days > 45) chips.push({ text: `Prices as of ${d.as_of}`, warn: true });
  if (result.benchmark_unmeasured) chips.push({ text: `Benchmark ${result.benchmark_unmeasured}: no price at ${result.start}`, warn: true });

  return (
    <div className="overflow-hidden rounded-lg border border-border bg-surface" data-testid="performance-card">
      <div className="flex items-center justify-between border-b border-border-soft px-3 py-2">
        <div className="text-sm font-medium text-ink">
          Performance{result.household_name ? ` · ${result.household_name}` : ""}
        </div>
        <div className="font-mono text-xs text-ink-muted">
          {result.start && result.end ? `${result.start} → ${result.end} · ${result.months} mo` : "not measurable"}
        </div>
      </div>

      {result.twr === null ? (
        <div className="px-3 py-3 font-mono text-xs text-warn">No return — not enough price history</div>
      ) : (
        <>
          <div className="flex flex-wrap gap-x-6 gap-y-2 px-3 py-3">
            <Stat k="Time-weighted" v={pct(result.twr, true)} cls={tone(result.twr)} />
            <Stat k="Money-weighted" v={pct(result.mwr?.period, true)} cls={tone(result.mwr?.period)} />
            {result.benchmark_symbol && (
              <Stat k={`Benchmark · ${result.benchmark_label ?? result.benchmark_symbol}`} v={pct(result.benchmark_return, true)} cls={tone(result.benchmark_return)} />
            )}
            {result.alpha !== null && <Stat k="Alpha" v={pct(result.alpha, true)} cls={tone(result.alpha)} />}
          </div>

          <div className="border-t border-border-soft px-3 py-2">
            <div className="flex h-12 items-center gap-[3px]" role="img" aria-label="Monthly returns">
              {result.periods.map((p) => (
                <div key={p.end} className="flex h-full flex-1 flex-col justify-center" title={`${p.end}: ${pct(p.return, true)}`}>
                  <div className="flex h-1/2 items-end">
                    {p.return >= 0 && <div className="w-full rounded-sm bg-ok" style={{ height: `${(p.return / max) * 100}%`, minHeight: 1 }} />}
                  </div>
                  <div className="flex h-1/2 items-start">
                    {p.return < 0 && <div className="w-full rounded-sm bg-crit" style={{ height: `${(-p.return / max) * 100}%`, minHeight: 1 }} />}
                  </div>
                </div>
              ))}
            </div>
          </div>

          {result.attribution.length > 0 && (
            <table className="w-full border-t border-border-soft text-sm">
              <thead>
                <tr className="text-left text-[10px] uppercase tracking-wide text-ink-muted">
                  <th className="px-3 py-1.5 font-medium">Asset class</th>
                  <th className="px-3 py-1.5 text-right font-medium">Weight</th>
                  <th className="px-3 py-1.5 text-right font-medium">Return</th>
                  <th className="px-3 py-1.5 text-right font-medium">Contribution</th>
                </tr>
              </thead>
              <tbody className="divide-y divide-border-soft">
                {result.attribution.map((a) => (
                  <tr key={a.asset_class}>
                    <td className="px-3 py-1.5 text-ink">{label(a.asset_class)}</td>
                    <td className="px-3 py-1.5 text-right font-mono tabular-nums text-ink-muted">{pct(a.weight)}</td>
                    <td className={`px-3 py-1.5 text-right font-mono tabular-nums ${tone(a.class_return)}`}>{pct(a.class_return, true)}</td>
                    <td className={`px-3 py-1.5 text-right font-mono tabular-nums ${tone(a.contribution)}`}>{pct(a.contribution, true)}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          )}
        </>
      )}

      <div className="flex flex-wrap gap-1.5 border-t border-border-soft px-3 py-2">
        {chips.map((c) => (
          <span key={c.text} className={`chip ${c.warn ? "text-warn" : "text-ink-muted"}`}>{c.text}</span>
        ))}
      </div>
    </div>
  );
}
