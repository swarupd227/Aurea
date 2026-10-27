"use client";

import { useEffect, useMemo, useState } from "react";
import { ArrowRight, Radar, Send, Zap } from "lucide-react";
import RecommendationCard from "@/components/RecommendationCard";
import { useAgentRunner } from "@/components/AgentConsole";
import BookScan from "@/components/BookScan";
import ActivityRail from "@/components/ActivityRail";
import { StatTile, SkeletonList, Empty } from "@/components/ui";
import { useApi } from "@/lib/hooks";
import { api, getUser } from "@/lib/api";
import { formatDateFull, timeAgo } from "@/lib/format";
import { useViewNav, ViewLink } from "./ViewNav";
import { ViewHeader, chip } from "./ViewHeader";

const SCAN_SENTENCE = "Have the next-best-action agent scan the book for opportunities.";

/** The day's proposals, the workforce's activity, and a way to hand it work. On a page the Delegate
 * bar and Scan book run through their own streaming routes; in the work pane they are sentences to
 * the conversation beside it, so a run goes through `run_agent` and its Confirm card. */
export function CockpitView() {
  const nav = useViewNav();
  const { data: feed, loading, refetch } = useApi<any[]>("/api/studio/feed");
  const { data: cap, refetch: refetchCap } = useApi<any>("/api/studio/capacity");
  const { data: activity } = useApi<any[]>("/api/atlas/activity?limit=60");
  const { data: households } = useApi<any[]>("/api/core/households");
  const [user, setUser] = useState<any>(null);
  const runner = useAgentRunner();
  const [showScan, setShowScan] = useState(false);
  const [delegateText, setDelegateText] = useState("");
  const [delegating, setDelegating] = useState(false);
  const [delegateError, setDelegateError] = useState<string | null>(null);

  useEffect(() => setUser(getUser()), []);

  const top = (feed || []).slice(0, 6);
  const autonomous = (activity || []).filter((a) => a.autonomous).slice(0, 5);

  // Built from the firm's own client names, so each one is a sentence an agent can act on.
  const suggestions = useMemo(() => {
    if (!households?.length) return [
      "Find growth opportunities across the book",
      "Run a cash drag sweep across all clients",
      "Prepare for my next meeting",
    ];
    const [first, second] = households;
    return [
      first ? `Rebalance ${first.name} if it has drifted` : "Rebalance portfolios that have drifted",
      second ? `Prepare a meeting brief for ${second.name}` : "Prepare for my next client meeting",
      "Find growth opportunities across the book",
    ];
  }, [households]);

  useEffect(() => {
    const h = () => { refetch(); refetchCap(); };
    window.addEventListener("aurea:decided", h);
    return () => window.removeEventListener("aurea:decided", h);
  }, [refetch, refetchCap]);

  async function delegate(text: string) {
    if (!text.trim()) return;
    if (nav.say) {
      nav.say(text.trim());
      setDelegateText("");
      return;
    }
    setDelegating(true);
    setDelegateError(null);
    try {
      const r = await api("/api/atlas/delegate", { body: { text } });
      runner.run({ agentKey: r.agent_key, subjectType: r.subject_type, subjectId: r.subject_id,
                   label: `Delegated · ${r.interpreted}` });
      setDelegateText("");
    } catch (e: any) {
      setDelegateError(e?.message || "Couldn't route that request. Try again.");
    } finally {
      setDelegating(false);
    }
  }

  const scan = () => (nav.say ? nav.say(SCAN_SENTENCE) : setShowScan(true));
  const greeting = `Good day, ${user?.full_name?.split(" ")[0] || "there"}`;

  return (
    <div className="view-container">
      <ViewHeader
        title={nav.inPane ? "Cockpit" : greeting}
        actions={
          <button className="btn-outline" data-testid="book-scan" onClick={scan}>
            <Radar size={16} /> Scan book
          </button>
        }
      />

      {showScan && (
        <BookScan agent="next_best_action" onClose={() => setShowScan(false)}
          onDone={() => { refetch(); refetchCap(); }} />
      )}

      {/* Delegate. In the pane the conversation's composer is beside it: the chips are sentences for it. */}
      <div className="mb-5 rounded-lg border border-border bg-surface p-3">
        {!nav.inPane && (
          <form onSubmit={(e) => { e.preventDefault(); delegate(delegateText); }} className="flex gap-2">
            <input className="input" aria-label="Delegate a task to your workforce" data-testid="delegate-input"
              placeholder="Delegate: rebalance any client that has drifted…"
              value={delegateText} onChange={(e) => setDelegateText(e.target.value)} />
            <button className="btn-primary shrink-0" data-testid="delegate-submit" disabled={delegating}>
              <Send size={16} /> {delegating ? "Routing…" : "Delegate"}
            </button>
          </form>
        )}
        {delegateError && (
          <p className="mt-2 rounded-lg bg-crit-bg px-3 py-2 text-sm text-crit" role="alert">{delegateError}</p>
        )}
        <div className={`flex flex-wrap items-center gap-2 ${nav.inPane ? "" : "mt-2"}`}>
          {nav.inPane && <span className="font-mono text-[11px] uppercase tracking-wider text-ink-muted">Delegate</span>}
          {suggestions.map((s) => (
            <button
              key={s}
              onClick={() => delegate(s)}
              disabled={delegating}
              data-testid="cockpit-suggestion"
              className="rounded-full border border-border px-3 py-1 text-xs text-ink-soft transition-colors hover:border-accent/60 hover:text-ink disabled:opacity-50"
            >
              {s}
            </button>
          ))}
        </div>
      </div>

      <div className="grid gap-5 cq-lg:grid-cols-3">
        <div className="min-w-0 space-y-5 cq-lg:col-span-2">
          <div className="grid grid-cols-2 gap-3 cq-md:grid-cols-4">
            <StatTile label="Open actions" value={cap?.open_items ?? "—"} accent="warn" />
            <StatTile label="Decisions" value={cap?.decisions_made ?? "—"} />
            <StatTile label="Agent runs" value={cap?.total_agent_runs ?? "—"} />
            <StatTile label="Hours reclaimed · est." value={cap ? `${cap.estimated_hours_reclaimed}h` : "—"} accent="ok" />
          </div>

          {autonomous.length > 0 && (
            <div className="rounded-lg border border-border bg-surface p-4">
              <div className="mb-2 flex items-center gap-2 font-semibold text-ink"><Zap size={16} className="text-ink-muted" /> Acted autonomously</div>
              <ul className="space-y-1.5">
                {autonomous.map((a) => (
                  <li key={a.id} className="flex items-start gap-2 text-sm text-ink-soft">
                    <span className={`${chip} shrink-0`}>Autonomous</span>
                    <span className="min-w-0 flex-1">{a.summary}</span>
                    <span className="shrink-0 text-xs text-ink-muted" title={formatDateFull(a.created_at)}>{timeAgo(a.created_at)}</span>
                  </li>
                ))}
              </ul>
            </div>
          )}

          <div className="flex items-center justify-between gap-2">
            <h2 className="flex items-center gap-2 text-lg font-semibold text-ink">
              Next-best-action feed
              {feed && <span className={chip}>{feed.length}</span>}
            </h2>
            <ViewLink href="/studio/review" className="btn-ghost text-sm">All <ArrowRight size={15} /></ViewLink>
          </div>

          {loading ? (
            <SkeletonList count={3} />
          ) : top.length === 0 ? (
            <div className="rounded-lg border border-border bg-surface p-8">
              <Empty>
                <div className="space-y-3">
                  <p>No open recommendations</p>
                  <button className="btn-outline text-sm" data-testid="book-scan-empty" onClick={scan}>
                    <Radar size={15} /> Scan book
                  </button>
                </div>
              </Empty>
            </div>
          ) : (
            <div className="space-y-3">
              {top.map((r) => (
                <RecommendationCard
                  key={r.id}
                  rec={r}
                  onDecided={() => { refetch(); refetchCap(); nav.changed(); }}
                />
              ))}
            </div>
          )}
        </div>

        <div className="min-w-0">
          <ActivityRail />
        </div>
      </div>
    </div>
  );
}
