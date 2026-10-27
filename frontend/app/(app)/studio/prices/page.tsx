"use client";
import { useState } from "react";
import { RefreshCw } from "lucide-react";
import PageHeader from "@/components/PageHeader";
import { Card, Empty, Spinner } from "@/components/ui";
import { api } from "@/lib/api";
import { useApi } from "@/lib/hooks";
import { STATUS_TEXT } from "@/app/workspace/components/artifacts/PriceFreshnessCard";

function n(v: number, d = 0) {
  return v.toLocaleString(undefined, { minimumFractionDigits: d, maximumFractionDigits: d });
}

function when(iso: string | null | undefined) {
  if (!iso) return "—";
  return new Date(iso).toLocaleString(undefined, { weekday: "short", day: "numeric", month: "short", hour: "2-digit", minute: "2-digit" });
}

export default function Prices() {
  const { data, loading, refetch } = useApi<any>("/api/prices/freshness");
  const [confirming, setConfirming] = useState(false);
  const [busy, setBusy] = useState(false);
  const [msg, setMsg] = useState<string | null>(null);

  if (loading || !data) return <Spinner />;
  const s = data.summary;
  const feed = data.feed;
  const bad = s.missing + s.synthetic + s.stale;

  // The same action as the refresh_prices tool, and it waits for the same confirmation.
  async function refresh() {
    setBusy(true);
    setMsg(null);
    try {
      const r = await api<any>("/api/prices/refresh", { method: "POST" });
      setMsg(r.status === "error"
        ? `The fetch failed: ${r.error ?? "no prices could be fetched"}.`
        : `Fetched ${r.resolved} of ${r.instruments}: ${r.inserted} new, ${r.updated} updated, ${r.failed.length} failed. ${r.revalued ? `${r.revalued.holdings} holding(s) revalued.` : "Nothing needed revaluing."}`);
      setConfirming(false);
      await refetch();
    } catch (e: any) {
      setMsg(e?.message ?? "That did not work.");
      setConfirming(false);
    } finally {
      setBusy(false);
    }
  }

  return (
    <div>
      <PageHeader title="Prices" sub="Whether the number next to each holding can be trusted, and when it was struck." />
      {msg && <div className="mb-4 font-mono text-xs text-ink-muted" role="status" data-testid="prices-status">{msg}</div>}

      <div className="mb-4 grid grid-cols-2 gap-3 lg:grid-cols-5" data-testid="prices-summary">
        {[
          ["Current", s.current, "text-ok"],
          ["Ageing", s.ageing, s.ageing ? "text-warn" : "text-ink-muted"],
          ["Stale", s.stale, s.stale ? "text-crit" : "text-ink-muted"],
          ["Placeholder", s.synthetic, s.synthetic ? "text-crit" : "text-ink-muted"],
          ["No price", s.missing, s.missing ? "text-crit" : "text-ink-muted"],
        ].map(([label, v, tone]: any) => (
          <Card key={label}>
            <div className="tile-label">{label}</div>
            <div className={`font-mono text-2xl tabular-nums ${tone}`}>{v}</div>
          </Card>
        ))}
      </div>

      <Card className="mb-4">
        <div className="flex flex-wrap items-center justify-between gap-3">
          <div className="flex flex-wrap gap-1.5" data-testid="prices-feed">
            <span className={`chip ${feed.live ? "text-ink-muted" : "text-crit"}`}>{feed.live ? "feed on" : "feed off"}</span>
            {feed.last_run && (
              <span className={`chip ${feed.last_run.status === "success" ? "text-ink-muted" : "text-warn"}`}>
                last run {when(feed.last_run.started_at)} · {feed.last_run.status} · {feed.last_run.inserted} new · {feed.last_run.updated} updated
              </span>
            )}
            {feed.next_runs.map((r: any) => <span key={r.at} className="chip text-ink-muted">next {when(r.at)} · {r.why}</span>)}
            {bad > 0 && s.share_not_current != null && (
              <span className="chip text-crit">{(s.share_not_current * 100).toFixed(0)}% of value on {bad} untrusted price(s)</span>
            )}
            {feed.last_run?.failed.map((f: any) => <span key={f.symbol} className="chip text-crit" title={f.reason}>{f.symbol} failed · {f.reason.slice(0, 48)}</span>)}
          </div>
          {!confirming ? (
            <button className="btn-outline text-xs" data-testid="prices-refresh" disabled={!data.can_refresh || !feed.live}
              title={!data.can_refresh ? "Only operations, the portfolio team or an administrator may fetch prices"
                : !feed.live ? "The live feed is switched off" : undefined}
              onClick={() => setConfirming(true)}>
              <RefreshCw size={13} className="mr-1.5 inline" /> Fetch prices now
            </button>
          ) : (
            <div className="flex items-center gap-2" data-testid="prices-confirm">
              <span className="chip text-ink-muted">revalues holdings · changes balances and fees</span>
              <button className="btn-primary text-xs" disabled={busy} onClick={refresh}>{busy ? "Fetching…" : "Confirm"}</button>
              <button className="btn-outline text-xs" disabled={busy} onClick={() => setConfirming(false)}>Not now</button>
            </div>
          )}
        </div>
      </Card>

      {data.instruments.length === 0 ? <Empty>No holdings are priced yet.</Empty> : (
        <Card className="overflow-hidden p-0">
          <div className="overflow-x-auto">
            <table className="w-full text-sm" data-testid="prices-table">
              <thead>
                <tr className="text-left text-[10px] uppercase tracking-wide text-ink-muted">
                  <th className="px-4 py-2 font-medium">Holding</th>
                  <th className="px-4 py-2 font-medium">Price</th>
                  <th className="px-4 py-2 font-medium">As at</th>
                  <th className="px-4 py-2 text-right font-medium">Age · limit</th>
                  <th className="px-4 py-2 text-right font-medium">Held</th>
                </tr>
              </thead>
              <tbody className="divide-y divide-border-soft">
                {data.instruments.map((r: any) => {
                  const st = STATUS_TEXT[r.status];
                  return (
                    <tr key={r.instrument_id}>
                      <td className="px-4 py-2">
                        <div className="text-ink">{r.symbol} <span className="text-xs text-ink-muted">{r.market_type === "private" ? "private" : "listed"}</span></div>
                        <div className="text-xs text-ink-muted">{r.name}</div>
                      </td>
                      <td className="px-4 py-2">
                        <span className={`chip ${st.tone}`}>{st.text}</span>
                        {r.basis && r.basis !== "market" && <span className="chip ml-1 text-ink-muted">{r.basis === "declared" ? "stated valuation" : "not a valuation"}</span>}
                      </td>
                      <td className="px-4 py-2 font-mono tabular-nums text-ink-muted">{r.as_of ?? "—"}</td>
                      <td className={`px-4 py-2 text-right font-mono tabular-nums ${r.status === "current" ? "text-ink-muted" : st.tone}`}>
                        {r.age_days != null ? `${r.age_days}d / ${r.limit_days}d` : "—"}
                      </td>
                      <td className="px-4 py-2 text-right font-mono tabular-nums text-ink-muted">{r.currency} {n(r.value_held)} · {r.holdings}</td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
          </div>
        </Card>
      )}
    </div>
  );
}
