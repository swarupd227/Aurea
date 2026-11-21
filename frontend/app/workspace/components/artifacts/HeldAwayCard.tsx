import { money } from "@/lib/format";
import { reviewDay } from "./ReviewsCard";

// Held away, line by line, and how it is doing (`read_held_away`, B-198): each holding at what its statement states,
// its year on real prices beside its class reference, the shelf's alternative and any overlap with the managed book.
// Figures and chips only. For staff: no client reads it.

export interface HeldAwayLine {
  id: string;
  institution: string;
  account_label: string | null;
  owner: string;
  symbol: string | null;
  name: string;
  asset_class: string | null;
  quantity: number | null;
  value: number;
  currency: string;
  value_on: string;
  source_document: string;
  source: "statement" | "feed";
  feed: string | null;
  simulated: boolean;
  counted: boolean;
  performance: { start: string; end: string; return: number; stale: boolean } | null;
  not_measured: string | null;
  reference: { symbol: string; label: string; return: number | null } | null;
  gap: number | null;
  underperforming: boolean;
  alternative: { symbol: string; name: string; return: number; shelf: "approved" | "not_reviewed" } | null;
  overlap: { managed_value: number } | null;
}

export const signedPct = (v: number | null | undefined) => (v == null ? "—" : `${v >= 0 ? "+" : ""}${(v * 100).toFixed(1)}%`);

export function SimulatedChip() {
  return <span className="chip text-warn" data-testid="held-away-simulated" title="From a simulated feed, for demonstration">simulated</span>;
}

/** The lines, one row each. */
export function HeldAwayRows({ rows, act }: { rows: HeldAwayLine[]; act?: (l: HeldAwayLine) => React.ReactNode }) {
  return (
    <table className="w-full text-xs" data-testid="held-away-rows">
      <thead>
        <tr className="text-left text-[10px] uppercase tracking-wide text-ink-muted">
          <th className="px-3 py-1.5 font-medium">Holding</th>
          <th className="px-3 py-1.5 font-medium">Where</th>
          <th className="px-3 py-1.5 text-right font-medium">Stated</th>
          <th className="px-3 py-1.5 text-right font-medium">Year</th>
          <th className="px-3 py-1.5 text-right font-medium">Reference</th>
          <th className="px-3 py-1.5 font-medium">Shelf</th>
          <th className="px-3 py-1.5 font-medium" />
          {act && <th className="px-3 py-1.5" />}
        </tr>
      </thead>
      <tbody className="divide-y divide-border-soft">
        {rows.map((l) => (
          <tr key={l.id} title={l.source_document}>
            <td className="px-3 py-1.5">
              <div className="text-ink">{l.symbol || l.name}</div>
              <div className="text-ink-muted">{l.symbol ? l.name : l.asset_class?.replace(/_/g, " ") ?? ""}</div>
            </td>
            <td className="px-3 py-1.5 text-ink-muted">{l.institution}<div>{l.owner}</div></td>
            <td className="px-3 py-1.5 text-right font-mono tabular-nums text-ink-muted">
              {money(l.value, l.currency)}<div>{reviewDay(l.value_on)}</div>
            </td>
            <td className={`px-3 py-1.5 text-right font-mono tabular-nums ${l.underperforming ? "text-crit" : "text-ink"}`}>
              {l.performance ? signedPct(l.performance.return) : "—"}
            </td>
            <td className="px-3 py-1.5 text-right font-mono tabular-nums text-ink-muted">
              {l.reference?.return != null ? <>{l.reference.symbol} {signedPct(l.reference.return)}</> : "—"}
            </td>
            <td className="px-3 py-1.5">
              {l.alternative
                ? <span className="chip tabular-nums text-ok" title={l.alternative.name}>{l.alternative.symbol} {signedPct(l.alternative.return)}{l.alternative.shelf === "not_reviewed" ? " · not reviewed" : ""}</span>
                : <span className="text-ink-muted">—</span>}
            </td>
            <td className="px-3 py-1.5">
              <div className="flex flex-wrap gap-1">
                {l.underperforming && <span className="chip text-crit" data-testid="held-away-under">trails reference</span>}
                {l.overlap && <span className="chip text-warn" data-testid="held-away-overlap">also managed</span>}
                {l.not_measured && <span className="chip text-ink-muted" title={l.not_measured}>no performance</span>}
                {l.performance?.stale && <span className="chip text-warn">prices stale</span>}
                {!l.counted && <span className="chip text-warn">not converted</span>}
                {l.source === "feed" && <span className="chip text-ink-muted">{l.feed}</span>}
                {l.simulated && <SimulatedChip />}
              </div>
            </td>
            {act && <td className="px-3 py-1.5 text-right">{act(l)}</td>}
          </tr>
        ))}
      </tbody>
    </table>
  );
}

export function HeldAwayChips({ summary, currency }: { summary: any; currency?: string | null }) {
  return (
    <div className="flex flex-wrap gap-1.5">
      {typeof summary.value === "number" && <span className="chip tabular-nums text-ink-muted">{money(summary.value, currency ?? undefined)} stated</span>}
      <span className={`chip tabular-nums ${summary.underperforming ? "text-crit" : "text-ink-muted"}`}>{summary.underperforming} trailing</span>
      <span className={`chip tabular-nums ${summary.overlaps ? "text-warn" : "text-ink-muted"}`}>{summary.overlaps} also managed</span>
      <span className="chip text-ink-muted">estimate · price return</span>
      {summary.simulated > 0 && <SimulatedChip />}
    </div>
  );
}

const SHOWN = 8;

export function HeldAwayCard({ result }: { result: any }) {
  if (Array.isArray(result?.households)) {
    const rows: any[] = result.households;
    return (
      <div className="overflow-hidden rounded-lg border border-border bg-surface" data-testid="held-away-card">
        <div className="flex flex-wrap items-center justify-between gap-2 border-b border-border-soft px-3 py-2">
          <div className="text-sm font-medium text-ink">Held away</div>
          <HeldAwayChips summary={result.summary} />
        </div>
        <div className="overflow-x-auto">
          <table className="w-full text-sm">
            <thead>
              <tr className="text-left text-[10px] uppercase tracking-wide text-ink-muted">
                <th className="px-3 py-1.5 font-medium">Client</th>
                <th className="px-3 py-1.5 text-right font-medium">Holdings</th>
                <th className="px-3 py-1.5 text-right font-medium">Stated</th>
                <th className="px-3 py-1.5 font-medium" />
              </tr>
            </thead>
            <tbody className="divide-y divide-border-soft">
              {rows.slice(0, SHOWN).map((h) => (
                <tr key={h.household_id}>
                  <td className="px-3 py-1.5 text-ink">{h.household}</td>
                  <td className="px-3 py-1.5 text-right font-mono text-xs tabular-nums text-ink-muted">{h.holdings}</td>
                  <td className="px-3 py-1.5 text-right font-mono text-xs tabular-nums text-ink-muted">{money(h.value, result.currency ?? undefined)}</td>
                  <td className="px-3 py-1.5">
                    <div className="flex flex-wrap gap-1">
                      {h.underperforming > 0 && <span className="chip text-crit">{h.underperforming} trailing</span>}
                      {h.overlaps > 0 && <span className="chip text-warn">{h.overlaps} also managed</span>}
                      {h.simulated > 0 && <SimulatedChip />}
                    </div>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
        {rows.length > SHOWN && <div className="border-t border-border-soft px-3 py-2"><span className="chip text-ink-muted">+{rows.length - SHOWN} more</span></div>}
      </div>
    );
  }
  const lines: HeldAwayLine[] = result.holdings || [];
  return (
    <div className="overflow-hidden rounded-lg border border-border bg-surface" data-testid="held-away-card">
      <div className="flex flex-wrap items-center justify-between gap-2 border-b border-border-soft px-3 py-2">
        <div className="text-sm font-medium text-ink">Held away · {result.household}</div>
        <HeldAwayChips summary={result.summary} currency={result.currency} />
      </div>
      <div className="overflow-x-auto"><HeldAwayRows rows={lines.slice(0, SHOWN)} /></div>
      {lines.length > SHOWN && <div className="border-t border-border-soft px-3 py-2"><span className="chip text-ink-muted">+{lines.length - SHOWN} more</span></div>}
    </div>
  );
}
