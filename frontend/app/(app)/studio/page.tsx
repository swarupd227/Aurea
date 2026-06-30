"use client";
import { useEffect, useMemo, useState } from "react";
import Link from "next/link";
import { ArrowRight, ListChecks, Sparkles, Radar, Send, Zap, Wand2 } from "lucide-react";
import PageHeader from "@/components/PageHeader";
import RecommendationCard from "@/components/RecommendationCard";
import { useAgentRunner } from "@/components/AgentConsole";
import BookScan from "@/components/BookScan";
import ActivityRail from "@/components/ActivityRail";
import { StatTile, SkeletonList, Empty } from "@/components/ui";
import { useApi } from "@/lib/hooks";
import { api, getUser } from "@/lib/api";
import { formatDateFull, timeAgo } from "@/lib/format";

export default function Cockpit() {
  const { data: feed, loading, refetch } = useApi<any[]>("/api/studio/feed");
  const { data: cap, refetch: refetchCap } = useApi<any>("/api/studio/capacity");
  const { data: activity } = useApi<any[]>("/api/atlas/activity?limit=60");
  const { data: households } = useApi<any[]>("/api/core/households");
  const user = typeof window !== "undefined" ? getUser() : null;
  const runner = useAgentRunner();
  const [showScan, setShowScan] = useState(false);
  const [delegateText, setDelegateText] = useState("");
  const [delegating, setDelegating] = useState(false);

  const top = (feed || []).slice(0, 6);
  const autonomous = (activity || []).filter((a) => a.autonomous).slice(0, 5);

  // Build dynamic suggestions from real client names
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
    setDelegating(true);
    try {
      const r = await api("/api/atlas/delegate", { body: { text } });
      runner.run({ agentKey: r.agent_key, subjectType: r.subject_type, subjectId: r.subject_id,
                   label: `Delegated · ${r.interpreted}` });
      setDelegateText("");
    } finally {
      setDelegating(false);
    }
  }

  return (
    <div>
      <PageHeader
        title={`Good day, ${user?.full_name?.split(" ")[0] || "there"}`}
        sub="Delegate to your agents, watch them work, and approve what they propose."
        actions={
          <button className="btn-gold" onClick={() => setShowScan(true)}>
            <Radar size={16} /> Scan book
          </button>
        }
      />

      {showScan && (
        <BookScan agent="next_best_action" onClose={() => setShowScan(false)}
          onDone={() => { refetch(); refetchCap(); }} />
      )}

      {/* Delegate command bar */}
      <div className="card p-4 mb-5 border-gold/30">
        <div className="flex items-center gap-2 mb-2 text-sm font-medium text-ink">
          <Wand2 size={16} className="text-gold" /> Delegate to your workforce
        </div>
        <form onSubmit={(e) => { e.preventDefault(); delegate(delegateText); }} className="flex gap-2">
          <input className="input" placeholder="e.g. Rebalance any client that has drifted…"
            value={delegateText} onChange={(e) => setDelegateText(e.target.value)} />
          <button className="btn-primary" disabled={delegating}><Send size={16} /> {delegating ? "Routing…" : "Delegate"}</button>
        </form>
        <div className="flex flex-wrap gap-2 mt-2">
          {suggestions.map((s) => (
            <button key={s} onClick={() => delegate(s)} className="chip bg-navy-50 text-navy-700 hover:bg-navy-100 border border-navy-100">{s}</button>
          ))}
        </div>
      </div>

      <div className="grid lg:grid-cols-3 gap-5">
        {/* Left — KPIs, live runs, feed */}
        <div className="lg:col-span-2 space-y-5">
          <div className="grid grid-cols-2 md:grid-cols-4 gap-3">
            <StatTile label="Open actions" value={cap?.open_items ?? "—"} hint="Awaiting your decision" accent="gold" />
            <StatTile label="Decisions" value={cap?.decisions_made ?? "—"} hint="Approve / modify / dismiss" />
            <StatTile label="Agent runs" value={cap?.total_agent_runs ?? "—"} hint="Across the workforce" />
            <StatTile label="Capacity" value={cap ? `${cap.estimated_hours_reclaimed}h` : "—"} hint="Reclaimed this period" accent="positive" />
          </div>

          {/* Acted autonomously */}
          {autonomous.length > 0 && (
            <div className="card p-4">
              <div className="font-semibold text-ink mb-2 flex items-center gap-2"><Zap size={16} className="text-[#6b4b78]" /> Acted autonomously</div>
              <div className="space-y-1.5">
                {autonomous.map((a) => (
                  <div key={a.id} className="text-sm text-ink-soft flex items-center gap-2">
                    <span className="chip bg-[#efe2f0] text-[#6b4b78]">Tier 3</span>
                    {a.summary}
                    <span className="text-xs text-ink-muted ml-auto" title={formatDateFull(a.created_at)}>{timeAgo(a.created_at)}</span>
                  </div>
                ))}
              </div>
            </div>
          )}

          <div className="flex items-center justify-between">
            <h2 className="text-lg font-semibold text-ink flex items-center gap-2">
              <Sparkles size={18} className="text-gold" /> Next-best-action feed
            </h2>
            <Link href="/studio/review" className="btn-ghost text-sm">View all <ArrowRight size={15} /></Link>
          </div>

          {loading ? (
            <SkeletonList count={3} />
          ) : top.length === 0 ? (
            <div className="card p-8">
              <Empty>
                <div className="space-y-3">
                  <p>No open recommendations yet.</p>
                  <div className="flex justify-center gap-2">
                    <button className="btn-gold text-sm" onClick={() => setShowScan(true)}>
                      <Radar size={15} /> Scan book
                    </button>
                    <span className="text-ink-muted self-center text-sm">or delegate a task above.</span>
                  </div>
                </div>
              </Empty>
            </div>
          ) : (
            <div className="space-y-3">
              {top.map((r) => <RecommendationCard key={r.id} rec={r} onDecided={() => { refetch(); refetchCap(); }} />)}
            </div>
          )}
        </div>

        {/* Right — live activity */}
        <div>
          <ActivityRail />
        </div>
      </div>
    </div>
  );
}
