"use client";
import { useEffect, useRef, useState } from "react";
import Link from "next/link";
import {
  Radar, X, Loader2, AlertTriangle, TrendingDown, Coins, Target, Users2, Sparkles, ArrowRight, CheckCircle2,
} from "lucide-react";
import { streamSSE } from "@/lib/stream";
import { money } from "@/lib/format";

const SIGNAL: Record<string, { label: string; icon: any; cls: string }> = {
  concentration: { label: "Concentration risk", icon: AlertTriangle, cls: "text-critical bg-critical/10" },
  loss_harvest: { label: "Tax-loss harvest", icon: TrendingDown, cls: "text-positive bg-positive/10" },
  idle_cash: { label: "Cash drag", icon: Coins, cls: "text-caution bg-caution/10" },
  goal_gap: { label: "Goal off-track", icon: Target, cls: "text-caution bg-caution/10" },
  intergenerational: { label: "Next-gen engagement", icon: Users2, cls: "text-navy-700 bg-navy-100" },
  insight: { label: "Insight", icon: Sparkles, cls: "text-navy-700 bg-navy-100" },
};
const meta = (k: string) => SIGNAL[k] || SIGNAL.insight;

export default function BookScan({
  agent = "next_best_action",
  onClose,
  onDone,
}: {
  agent?: string;
  onClose: () => void;
  onDone?: () => void;
}) {
  const started = useRef(false);
  const ref = useRef<HTMLDivElement>(null);
  const [total, setTotal] = useState(0);
  const [progress, setProgress] = useState(0);
  const [current, setCurrent] = useState("");
  const [detected, setDetected] = useState(0);
  const [tally, setTally] = useState<Record<string, number>>({});
  const [recent, setRecent] = useState<{ name: string; found: number }[]>([]);
  const [result, setResult] = useState<any>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    ref.current?.scrollIntoView({ behavior: "smooth", block: "start" });
    if (started.current) return;
    started.current = true;
    streamSSE(`/api/atlas/scan-stream?agent=${agent}`, (ev) => {
      switch (ev.phase) {
        case "start": setTotal(ev.households); break;
        case "progress":
          setProgress(ev.index); setCurrent(ev.household); setDetected(ev.detected_total);
          setTally(ev.tally || {});
          setRecent((r) => [{ name: ev.household, found: ev.found }, ...r].slice(0, 5));
          break;
        case "done": setResult(ev); onDone?.(); break;
        case "error": setError(ev.message || "Scan failed"); break;
      }
    }).catch((e) => setError(e.message));
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  const pct = total ? Math.round((progress / total) * 100) : 0;
  const agg = result?.aggregates || {};

  return (
    <div ref={ref} className="card overflow-hidden mb-5 border-gold/40 scroll-mt-4">
      {/* Header */}
      <div className="px-4 py-3 bg-navy-900 text-white flex items-center gap-2.5">
        <Radar size={17} className={result || error ? "text-gold" : "text-gold animate-spin"} />
        <div className="min-w-0">
          <div className="font-semibold text-sm">Book scan · Next-Best-Action</div>
          <div className="text-[11px] text-navy-200/80">
            {result ? "Sweep complete" : error ? "Scan error" : "Sweeping every household for opportunities, risks & anomalies"}
          </div>
        </div>
        <button onClick={onClose} className="ml-auto p-1 hover:bg-white/10 rounded" title="Close"><X size={16} /></button>
      </div>

      <div className="p-4">
        {error ? (
          <div className="text-sm text-critical">{error}</div>
        ) : !result ? (
          /* ── Live sweep ── */
          <div className="grid md:grid-cols-3 gap-4">
            <div className="md:col-span-2">
              <div className="flex items-center justify-between text-sm mb-1.5">
                <span className="text-ink-soft flex items-center gap-2">
                  <Loader2 size={14} className="animate-spin text-navy-500" />
                  {current ? <>Scanning <span className="font-medium text-ink">{current}</span></> : "Reading the book…"}
                </span>
                <span className="text-ink-muted tabular-nums">{progress}/{total || "…"}</span>
              </div>
              <div className="h-2 rounded-full bg-navy-100 overflow-hidden">
                <div className="h-full bg-gold transition-all duration-300" style={{ width: `${pct}%` }} />
              </div>
              <div className="mt-3 space-y-1">
                {recent.map((r, i) => (
                  <div key={i} className="flex items-center justify-between text-xs text-ink-muted">
                    <span className="flex items-center gap-1.5"><CheckCircle2 size={12} className="text-positive" /> {r.name}</span>
                    <span>{r.found} signal{r.found === 1 ? "" : "s"}</span>
                  </div>
                ))}
              </div>
            </div>
            <div className="rounded-lg bg-navy-50/60 border border-navy-100 p-3">
              <div className="text-2xl font-bold text-ink tabular-nums">{detected}</div>
              <div className="text-xs text-ink-muted mb-2">signals detected so far</div>
              <div className="flex flex-wrap gap-1">
                {Object.entries(tally).map(([k, n]: any) => {
                  const m = meta(k); const Icon = m.icon;
                  return <span key={k} className={`chip ${m.cls}`}><Icon size={11} /> {n}</span>;
                })}
              </div>
            </div>
          </div>
        ) : (
          /* ── Result ── */
          <div className="space-y-4">
            {/* Stat tiles */}
            <div className="grid grid-cols-2 lg:grid-cols-4 gap-3">
              <Tile label="Households swept" value={result.households_scanned} />
              <Tile label="Signals detected" value={result.detected} />
              <Tile label="Actions surfaced" value={result.items_surfaced} accent="gold" />
              <Tile label="At stake" value={money((agg.harvestable_losses || 0) + (agg.idle_cash || 0))}
                sub={`${money(agg.harvestable_losses || 0)} losses · ${money(agg.idle_cash || 0)} cash`} />
            </div>

            {/* Breakdown */}
            <div className="flex flex-wrap gap-1.5">
              {Object.entries(result.by_signal || {}).map(([k, n]: any) => {
                const m = meta(k); const Icon = m.icon;
                return <span key={k} className={`chip ${m.cls}`}><Icon size={12} /> {n} {m.label}</span>;
              })}
            </div>

            {/* Items */}
            <div className="rounded-xl border border-navy-100 divide-y divide-navy-50 max-h-[360px] overflow-y-auto">
              {(result.items || []).map((it: any) => {
                const m = meta(it.signal); const Icon = m.icon;
                return (
                  <Link key={it.id} href="/studio/review"
                    className="flex items-start gap-3 p-3 hover:bg-navy-50/50 transition-colors">
                    <span className={`h-7 w-7 rounded-lg flex items-center justify-center shrink-0 ${m.cls}`}><Icon size={14} /></span>
                    <div className="min-w-0 flex-1">
                      <div className="text-sm font-medium text-ink">{it.title}</div>
                      <div className="text-xs text-ink-muted">{it.subject_label} · {m.label} · {Math.round(it.confidence * 100)}% confidence</div>
                    </div>
                    <span className={`chip shrink-0 ${it.priority <= 1 ? "bg-critical/10 text-critical" : it.priority === 2 ? "bg-caution/10 text-caution" : "bg-navy-50 text-ink-muted"}`}>
                      P{it.priority}
                    </span>
                  </Link>
                );
              })}
              {(!result.items || result.items.length === 0) && (
                <div className="p-4 text-sm text-ink-muted">No new actions — the book is within tolerance.</div>
              )}
            </div>

            <div className="flex items-center justify-between">
              <span className="text-xs text-ink-muted">Surfaced items are now in your feed & the decision ledger.</span>
              <Link href="/studio/review" className="btn-primary text-sm">View in Recommendations <ArrowRight size={15} /></Link>
            </div>
          </div>
        )}
      </div>
    </div>
  );
}

function Tile({ label, value, sub, accent }: any) {
  return (
    <div className="rounded-lg border border-navy-100 bg-surface p-3">
      <div className="text-[11px] uppercase tracking-wide text-ink-muted">{label}</div>
      <div className={`text-xl font-semibold tabular-nums ${accent === "gold" ? "text-gold-dark" : "text-ink"}`}>{value}</div>
      {sub && <div className="text-[11px] text-ink-muted mt-0.5">{sub}</div>}
    </div>
  );
}
