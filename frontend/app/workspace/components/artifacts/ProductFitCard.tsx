import { money } from "@/lib/format";

/** A new product, who it is for, and who it was offered to (docs/03_Architecture/product-distribution.md).
 * Figures and chips only: the reasons are in the agent's message, and each chip carries its fact as a title. */

export interface TargetMarket {
  version: number;
  objectives: string[];
  risk_profiles: string[];
  min_capacity: string | null;
  min_horizon_years: number | null;
  min_investment: number | null;
  complexity: string;
  liquidity: string;
  lock_years: number | null;
  markets: string[];
  negative_market: string[];
  conflict: string;
  document: string;
  needs_signoff: boolean;
}

interface Line { criterion: string; result: "pass" | "gate" | "missing"; said: string }
interface Signal { kind: string; said: string; points: number }
interface FitRow { household_id: string; household: string; adviser: string | null; verdict: string; lines: Line[]; signals: Signal[]; points: number }

const words = (s: string) => s.replace(/_/g, " ");
const SIGNAL: Record<string, string> = {
  idle_cash: "idle cash", goal_gap: "goal off track", dearer_class: "dearer class held", concentration: "concentration", review_due: "review due",
};
const SHOWN = 8;

function Header({ title, children, testId }: { title: string; children?: React.ReactNode; testId: string }) {
  return (
    <div className="flex flex-wrap items-center justify-between gap-2 border-b border-border-soft px-3 py-2" data-testid={testId}>
      <div className="text-sm font-medium text-ink">{title}</div>
      <div className="flex flex-wrap gap-1.5">{children}</div>
    </div>
  );
}

export function TargetMarketChips({ tm }: { tm: TargetMarket }) {
  return (
    <div className="flex flex-wrap gap-1.5" data-testid="target-market-chips">
      {tm.objectives.map((o) => <span key={o} className="chip text-ink">{words(o)}</span>)}
      {tm.risk_profiles.map((p) => <span key={p} className="chip text-ink-muted">{p}</span>)}
      {tm.min_capacity && <span className="chip text-ink-muted">capacity {tm.min_capacity}+</span>}
      {tm.min_horizon_years != null && <span className="chip text-ink-muted">{tm.min_horizon_years}y+</span>}
      {tm.min_investment != null && <span className="chip text-ink-muted tabular-nums">min {money(tm.min_investment)}</span>}
      <span className={`chip ${tm.complexity === "complex" ? "text-warn" : "text-ink-muted"}`}>{tm.complexity}</span>
      <span className="chip text-ink-muted">{tm.liquidity === "locked" ? `locked ${tm.lock_years}y` : tm.liquidity}</span>
      {tm.conflict !== "none" && <span className="chip text-warn">{words(tm.conflict)}</span>}
      {tm.negative_market.map((n) => <span key={n} className="chip text-crit" title="Not for">not: {words(n)}</span>)}
    </div>
  );
}

export function TargetMarketCard({ result }: { result: any }) {
  const tm: TargetMarket | null = result.target_market;
  return (
    <div className="overflow-hidden rounded-lg border border-border bg-surface" data-testid="target-market-card">
      <Header title={`${result.symbol} · ${result.product}`} testId="target-market-header">
        {result.shelf_status && <span className="chip text-ink-muted">shelf: {result.shelf_status}</span>}
        <span className={`chip ${result.offerable ? "text-ok" : "text-warn"}`} title={result.why_not ?? undefined}>
          {result.offerable ? "offerable" : "not offered"}
        </span>
        {tm?.needs_signoff && <span className={`chip ${result.signoff ? "text-ok" : "text-warn"}`}>{result.signoff ? "signed off" : "sign-off needed"}</span>}
      </Header>
      <div className="space-y-2 px-3 py-2">
        {tm ? (
          <>
            <TargetMarketChips tm={tm} />
            <div className="flex flex-wrap gap-1.5">
              <span className="chip text-ink-muted">v{tm.version}</span>
              <span className="chip text-ink-muted" title={tm.document}>{tm.document}</span>
            </div>
          </>
        ) : (
          <span className="chip text-ink-muted">no target market on file</span>
        )}
      </div>
    </div>
  );
}

function Gate({ row }: { row: FitRow }) {
  const shown = row.lines.filter((l) => l.result !== "pass");
  return (
    <div className="flex flex-wrap gap-1.5">
      {shown.slice(0, 3).map((l, i) => (
        <span key={i} className={`chip ${l.result === "gate" ? "text-crit" : "text-warn"}`} title={l.said}>{words(l.criterion)}</span>
      ))}
    </div>
  );
}

export function ProductFitCard({ result }: { result: any }) {
  const fits: FitRow[] = result.fits || [];
  const needs: FitRow[] = result.needs_fact || [];
  const notFor: FitRow[] = result.not_for || [];
  const c = result.counts || {};
  return (
    <div className="overflow-hidden rounded-lg border border-border bg-surface" data-testid="product-fit-card">
      <Header title={`${result.symbol} · ${result.scope ?? "who it fits"}`} testId="product-fit-header">
        <span className="chip text-ok tabular-nums">{c.fits ?? fits.length} fit</span>
        <span className="chip text-warn tabular-nums">{c.needs_fact ?? needs.length} need a fact</span>
        <span className="chip text-ink-muted tabular-nums">{c.not_for ?? notFor.length} not for them</span>
        {c.withheld != null && c.withheld > 0 && <span className="chip text-ink-muted tabular-nums">{c.withheld} not shown</span>}
        {!result.offerable && <span className="chip text-warn" title={result.why_not ?? undefined}>not offered</span>}
      </Header>
      <div className="overflow-x-auto">
        <table className="w-full text-sm">
          <tbody className="divide-y divide-border-soft">
            {fits.slice(0, SHOWN).map((r) => (
              <tr key={r.household_id} data-testid="fit-row">
                <td className="px-3 py-1.5 text-ink">{r.household}</td>
                <td className="px-3 py-1.5">
                  <div className="flex flex-wrap gap-1.5">
                    <span className="chip text-ok">fits</span>
                    {r.signals.map((s) => <span key={s.kind} className="chip text-ink-muted" title={s.said}>{SIGNAL[s.kind] ?? words(s.kind)}</span>)}
                  </div>
                </td>
                <td className="px-3 py-1.5 text-right font-mono text-xs tabular-nums text-ink-muted">{r.points}</td>
              </tr>
            ))}
            {[...needs, ...notFor].slice(0, Math.max(0, SHOWN - fits.length)).map((r) => (
              <tr key={r.household_id} data-testid="fit-row">
                <td className="px-3 py-1.5 text-ink-muted">{r.household}</td>
                <td className="px-3 py-1.5"><Gate row={r} /></td>
                <td />
              </tr>
            ))}
          </tbody>
        </table>
      </div>
      {fits.length + needs.length + notFor.length > SHOWN && (
        <div className="border-t border-border-soft px-3 py-2"><span className="chip text-ink-muted">+{fits.length + needs.length + notFor.length - SHOWN} more</span></div>
      )}
    </div>
  );
}

const STATUS_TONE: Record<string, string> = {
  to_discuss: "text-ink-muted", discussed: "text-ink-muted", interested: "text-ok", bought: "text-ok",
  declined: "text-warn", not_suitable: "text-warn", withdrawn: "text-ink-muted",
};

export function DistributionCard({ result }: { result: any }) {
  const pitches: any[] = result.pitches || [];
  const title = result.symbol ? `${result.symbol} · distribution` : `${result.household} · products offered`;
  return (
    <div className="overflow-hidden rounded-lg border border-border bg-surface" data-testid="distribution-card">
      <Header title={title} testId="distribution-header">
        {result.symbol && (
          <>
            <span className="chip text-ink-muted tabular-nums">{result.offered} offered</span>
            <span className="chip text-ok tabular-nums">{result.bought} bought</span>
            <span className={`chip tabular-nums ${result.bought_outside_target_market?.length ? "text-crit" : "text-ink-muted"}`}>
              {result.bought_outside_target_market?.length ?? 0} outside target market
            </span>
          </>
        )}
      </Header>
      <div className="overflow-x-auto">
        <table className="w-full text-sm">
          <tbody className="divide-y divide-border-soft">
            {pitches.slice(0, SHOWN).map((p) => (
              <tr key={p.id} data-testid="pitch-row">
                <td className="px-3 py-1.5 text-ink">{result.symbol ? p.household : p.symbol}</td>
                <td className="px-3 py-1.5"><span className={`chip ${STATUS_TONE[p.status] ?? "text-ink-muted"}`} title={p.reason ?? undefined}>{words(p.status)}</span></td>
                <td className="px-3 py-1.5 text-xs text-ink-muted">{p.adviser ?? "—"}</td>
                <td className="px-3 py-1.5 text-right font-mono text-xs tabular-nums text-ink-muted">{p.as_of}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
      {pitches.length === 0 && <div className="px-3 py-2"><span className="chip text-ink-muted">nothing offered yet</span></div>}
    </div>
  );
}
