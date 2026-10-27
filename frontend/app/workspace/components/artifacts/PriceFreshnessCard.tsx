interface Row {
  symbol: string;
  name: string;
  market_type: string;
  currency: string;
  as_of: string | null;
  age_days: number | null;
  limit_days: number;
  basis: "market" | "declared" | "synthetic" | null;
  status: "missing" | "synthetic" | "stale" | "ageing" | "current";
  holdings: number;
  value_held: number;
}

interface Feed {
  live: boolean;
  last_run: { status: string; started_at: string | null; inserted: number; updated: number; failed: { symbol: string; reason: string }[] } | null;
  next_runs: { at: string; why: string }[];
}

interface Freshness {
  instruments: Row[];
  summary: { total: number; missing: number; synthetic: number; stale: number; ageing: number; current: number; value_not_current: number; share_not_current: number | null };
  feed: Feed;
}

interface Refreshed {
  status: string;
  inserted: number;
  updated: number;
  resolved: number;
  instruments: number;
  failed: { symbol: string; reason: string }[];
  newest_price_date: string | null;
  revalued: { holdings: number; real_priced: number } | null;
}

export const STATUS_TEXT: Record<string, { text: string; tone: string }> = {
  missing: { text: "no price", tone: "text-crit" },
  synthetic: { text: "placeholder", tone: "text-crit" },
  stale: { text: "stale", tone: "text-crit" },
  ageing: { text: "ageing", tone: "text-warn" },
  current: { text: "current", tone: "text-ok" },
};

function n(v: number, d = 0) {
  return v.toLocaleString(undefined, { minimumFractionDigits: d, maximumFractionDigits: d });
}

function when(iso: string | null | undefined) {
  if (!iso) return "—";
  return new Date(iso).toLocaleString(undefined, { weekday: "short", day: "numeric", month: "short", hour: "2-digit", minute: "2-digit" });
}

export function PriceFreshnessCard({ result }: { result: Freshness | Refreshed }) {
  if (!("instruments" in result) || !Array.isArray(result.instruments)) {
    const r = result as Refreshed;
    return (
      <div className="overflow-hidden rounded-lg border border-border bg-surface" data-testid="price-refresh-card">
        <div className="border-b border-border-soft px-3 py-2 text-sm font-medium text-ink">Prices fetched</div>
        <div className="flex flex-wrap gap-x-8 gap-y-2 px-3 py-3">
          <div>
            <div className="text-[10px] uppercase tracking-wide text-ink-muted">Instruments</div>
            <div className="font-mono text-xl tabular-nums text-ink">{r.resolved} / {r.instruments}</div>
          </div>
          <div>
            <div className="text-[10px] uppercase tracking-wide text-ink-muted">New · updated</div>
            <div className="font-mono text-xl tabular-nums text-ink">{r.inserted} · {r.updated}</div>
          </div>
          {r.newest_price_date && (
            <div>
              <div className="text-[10px] uppercase tracking-wide text-ink-muted">Newest price</div>
              <div className="font-mono text-xl tabular-nums text-ink">{r.newest_price_date}</div>
            </div>
          )}
        </div>
        <div className="flex flex-wrap gap-1.5 border-t border-border-soft px-3 py-2">
          <span className="chip text-ink-muted">{r.revalued ? `${r.revalued.holdings} holding(s) revalued` : "nothing needed revaluing"}</span>
          {r.failed.map((f) => (
            <span key={f.symbol} className="chip text-crit" title={f.reason}>{f.symbol} failed</span>
          ))}
        </div>
      </div>
    );
  }

  const f = result as Freshness;
  const s = f.summary;
  const bad = s.missing + s.synthetic + s.stale;
  const feed = f.feed;
  return (
    <div className="overflow-hidden rounded-lg border border-border bg-surface" data-testid="price-freshness-card">
      <div className="flex flex-wrap items-center justify-between gap-2 border-b border-border-soft px-3 py-2">
        <div className="text-sm font-medium text-ink">Price freshness</div>
        <div className="flex flex-wrap gap-1.5">
          <span className="chip text-ok">{s.current} current</span>
          {s.ageing > 0 && <span className="chip text-warn">{s.ageing} ageing</span>}
          {bad > 0 && <span className="chip text-crit">{bad} not trustworthy</span>}
          {s.share_not_current != null && s.share_not_current > 0 && (
            <span className="chip text-crit">{(s.share_not_current * 100).toFixed(0)}% of value</span>
          )}
        </div>
      </div>
      <table className="w-full text-sm">
        <thead>
          <tr className="text-left text-[10px] uppercase tracking-wide text-ink-muted">
            <th className="px-3 py-1.5 font-medium">Holding</th>
            <th className="px-3 py-1.5 font-medium">Price</th>
            <th className="px-3 py-1.5 text-right font-medium">Age</th>
            <th className="px-3 py-1.5 text-right font-medium">Value held</th>
          </tr>
        </thead>
        <tbody className="divide-y divide-border-soft">
          {f.instruments.map((r) => {
            const st = STATUS_TEXT[r.status];
            return (
              <tr key={r.symbol}>
                <td className="px-3 py-1.5">
                  <div className="text-ink">{r.symbol}</div>
                  <div className="text-xs text-ink-muted">{r.name}</div>
                </td>
                <td className="px-3 py-1.5">
                  <span className={`chip ${st.tone}`}>{st.text}</span>
                  {r.basis && r.basis !== "market" && <span className="chip ml-1 text-ink-muted">{r.basis === "declared" ? "stated valuation" : "not a valuation"}</span>}
                </td>
                <td className={`px-3 py-1.5 text-right font-mono tabular-nums ${r.status === "current" ? "text-ink-muted" : st.tone}`}>
                  {r.age_days != null ? `${r.age_days}d / ${r.limit_days}d` : "—"}
                </td>
                <td className="px-3 py-1.5 text-right font-mono tabular-nums text-ink-muted">{r.currency} {n(r.value_held)} · {r.holdings}</td>
              </tr>
            );
          })}
        </tbody>
      </table>
      <div className="flex flex-wrap gap-1.5 border-t border-border-soft px-3 py-2">
        <span className={`chip ${feed.live ? "text-ink-muted" : "text-crit"}`}>{feed.live ? "feed on" : "feed off"}</span>
        {feed.last_run && (
          <span className={`chip ${feed.last_run.status === "success" ? "text-ink-muted" : "text-warn"}`}>
            last run {when(feed.last_run.started_at)} · {feed.last_run.status}
          </span>
        )}
        {feed.last_run?.failed.map((f) => (
          <span key={f.symbol} className="chip text-crit" title={f.reason}>{f.symbol} failed</span>
        ))}
        {feed.next_runs[0] && <span className="chip text-ink-muted">next {when(feed.next_runs[0].at)}</span>}
      </div>
    </div>
  );
}
