/** India's onboarding measures (B-392): each with its definition, count and figures, "not assessed" where nothing is recorded. */

const th = "px-3 py-1.5 font-medium";
const NONE = "not assessed";

const tone = (cell: string) => (cell === NONE ? "text-ink-muted" : "text-ink");

export function MeasureCount({ m }: { m: any }) {
  return <span className={`chip tabular-nums ${m.assessed ? "" : "text-ink-muted"}`} data-testid="india-measure-count">{m.count}</span>;
}

/** One measure in full: what it counts, its table and the chips that qualify it. */
export function MeasureBlock({ m, link }: { m: any; link?: React.ReactNode }) {
  return (
    <div className="overflow-hidden rounded-lg border border-border bg-surface" data-testid={`india-measure-${m.key.replace(/_/g, "-")}`}>
      <div className="flex flex-wrap items-center gap-1.5 border-b border-border-soft px-3 py-2">
        <span className="mr-auto text-sm font-medium text-ink">{m.label}</span>
        {m.simulated && <span className="chip text-warn" data-testid="india-measure-simulated">simulated</span>}
        <MeasureCount m={m} />
        {link}
      </div>
      <div className="px-3 pt-1.5 text-[11px] text-ink-muted" data-testid="india-measure-definition">{m.definition}</div>
      <div className="overflow-x-auto">
        <table className="w-full text-xs">
          <thead>
            <tr className="text-left text-[10px] uppercase tracking-wide text-ink-muted">
              {m.columns.map((c: string, i: number) => <th key={c} className={`${th} ${i ? "text-right" : ""}`}>{c}</th>)}
            </tr>
          </thead>
          <tbody className="divide-y divide-border-soft">
            {m.rows.map((r: string[], n: number) => (
              <tr key={n}>
                {r.map((cell, i) => <td key={i} className={`px-3 py-1.5 ${i ? "text-right font-mono tabular-nums" : ""} ${tone(cell)}`}>{cell}</td>)}
              </tr>
            ))}
          </tbody>
        </table>
      </div>
      {m.chips.length > 0 && (
        <div className="flex flex-wrap gap-1.5 border-t border-border-soft px-3 py-2">
          {m.chips.map((c: string) => <span key={c} className="chip text-ink-muted">{c}</span>)}
        </div>
      )}
    </div>
  );
}

/** The two officers the firm has named, or that it has not. */
export function OfficerChips({ officers }: { officers: any[] }) {
  return (
    <>
      {officers.map((o) => (
        <span key={o.key} className={`chip ${o.name ? "text-ink-muted" : "text-warn"}`} data-testid="india-officer">{o.label}: {o.name ?? "not recorded"}</span>
      ))}
    </>
  );
}

/** The conversation's compact reading: each measure and its count. */
export function MeasureSummary({ india }: { india: any }) {
  return (
    <table className="w-full text-xs" data-testid="india-measures-summary">
      <tbody className="divide-y divide-border-soft">
        {india.metrics.map((m: any) => (
          <tr key={m.key}>
            <td className="px-3 py-1.5 text-ink" title={m.definition}>{m.label}{m.simulated && <span className="chip ml-1.5 text-warn">simulated</span>}</td>
            <td className={`px-3 py-1.5 text-right font-mono tabular-nums ${m.assessed ? "text-ink" : "text-ink-muted"}`}>{m.count}</td>
          </tr>
        ))}
      </tbody>
    </table>
  );
}
