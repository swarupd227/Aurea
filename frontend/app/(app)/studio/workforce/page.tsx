"use client";
import { useState } from "react";
import Link from "next/link";
import { Bot, Eye, Activity, ChevronDown, ChevronUp, Play, RefreshCw } from "lucide-react";
import PageHeader from "@/components/PageHeader";
import { Card, SkeletonCard, TierBadge } from "@/components/ui";
import ActivityRail from "@/components/ActivityRail";
import { useAgentRunner } from "@/components/AgentConsole";
import { useApi } from "@/lib/hooks";
import { formatDateFull, timeAgo } from "@/lib/format";

const STATUS: Record<string, { dot: string; label: string }> = {
  working:  { dot: "bg-positive animate-pulse", label: "Working" },
  "on-duty": { dot: "bg-navy-400", label: "On duty" },
  paused:   { dot: "bg-critical", label: "Paused" },
};

const LADDER = [
  { tier: "tier_1", d: "Drafts; the human authors and decides." },
  { tier: "tier_2", d: "Acts only on explicit approval." },
  { tier: "tier_3", d: "Pre-approved, low-risk; reviewed after." },
];

export default function Workforce() {
  const { data, loading } = useApi<any[]>("/api/atlas/workforce");
  const [open, setOpen] = useState<string | null>(null);
  const runner = useAgentRunner();

  function runFirm(agentKey: string) {
    runner.run({ agentKey, subjectType: "firm", label: "Manual run" });
  }

  return (
    <div>
      <PageHeader title="Workforce" sub="Your agents — status, what each is watching, and what they've done. Autonomy is set per agent in Configuration." />

      <Card className="mb-5">
        <div className="text-xs font-semibold uppercase tracking-wide text-ink-muted mb-2">Autonomy ladder</div>
        <div className="grid sm:grid-cols-3 gap-2">
          {LADDER.map((l) => (
            <div key={l.tier} className="rounded-lg border border-navy-100 p-2.5 flex items-start gap-2">
              <TierBadge tier={l.tier} />
              <span className="text-xs text-ink-soft">{l.d}</span>
            </div>
          ))}
        </div>
      </Card>

      <div className="grid lg:grid-cols-3 gap-5">
        <div className="lg:col-span-2 space-y-3">
          {loading ? (
            <div className="grid sm:grid-cols-2 gap-3">
              {Array.from({ length: 6 }).map((_, i) => <SkeletonCard key={i} rows={3} />)}
            </div>
          ) : (
            <div className="grid sm:grid-cols-2 gap-3">
              {data?.map((a) => {
                const st = STATUS[a.status] || STATUS["on-duty"];
                const isOpen = open === a.agent_key;
                const isHouseholdScope = a.subject === "household";
                return (
                  <Card key={a.agent_key} className={a.paused ? "border-critical/30" : ""}>
                    <div className="flex items-start gap-3">
                      <span className="h-10 w-10 rounded-xl bg-navy-800 text-white flex items-center justify-center shrink-0"><Bot size={18} /></span>
                      <div className="min-w-0 flex-1">
                        <div className="flex items-center justify-between gap-2">
                          <div className="font-semibold text-ink text-sm truncate">{a.name}</div>
                          <span className="inline-flex items-center gap-1 text-[11px] text-ink-muted shrink-0">
                            <span className={`h-1.5 w-1.5 rounded-full ${st.dot}`} /> {st.label}
                          </span>
                        </div>
                        <div className="text-xs text-ink-muted">{a.stage}</div>
                        <div className="mt-1.5"><TierBadge tier={a.tier} /></div>
                      </div>
                    </div>

                    {a.paused && (
                      <div className="mt-2 rounded-lg bg-critical/5 px-3 py-2 flex items-center justify-between gap-2">
                        <span className="text-xs text-critical font-medium">Paused by surveillance</span>
                        <Link href="/admin" className="text-xs text-navy-700 hover:text-ink font-medium">Resume in Config →</Link>
                      </div>
                    )}

                    <div className="mt-3 text-xs space-y-1.5">
                      <div className="flex gap-1.5"><Eye size={13} className="text-ink-muted mt-0.5 shrink-0" /><span className="text-ink-soft">{a.watching}</span></div>
                      <div className="flex gap-1.5">
                        <Activity size={13} className="text-ink-muted mt-0.5 shrink-0" />
                        <span className="text-ink-soft" title={formatDateFull(a.last_at)}>
                          {a.last_action}{a.last_at ? ` · ${timeAgo(a.last_at)}` : ""}
                        </span>
                      </div>
                    </div>

                    {isOpen && (
                      <div className="mt-2 pt-2 border-t border-navy-50 text-xs space-y-1.5">
                        <div><span className="text-ink-muted">Acts: </span><span className="text-ink-soft">{a.acts}</span></div>
                        <div><span className="text-ink-muted">Checkpoint: </span><span className="text-ink-soft">{a.checkpoint}</span></div>
                      </div>
                    )}

                    <div className="mt-3 pt-2 border-t border-navy-50 flex items-center gap-3 text-xs text-ink-muted">
                      <span><b className="text-ink">{a.runs}</b> runs</span>
                      <span><b className="text-ink">{a.contributions}</b> proposed</span>
                      <div className="ml-auto flex items-center gap-2">
                        {!a.paused && (
                          isHouseholdScope ? (
                            <span className="text-[11px] text-ink-muted" title="Run this agent on a specific client from the client detail page">Per-client</span>
                          ) : (
                            <button className="btn-ghost text-xs px-2 py-1" onClick={() => runFirm(a.agent_key)}>
                              <Play size={12} /> Run
                            </button>
                          )
                        )}
                        <button
                          className="btn-ghost text-xs px-1.5 py-1"
                          title={isOpen ? "Collapse" : "See what this agent can do and when it checks in"}
                          onClick={() => setOpen(isOpen ? null : a.agent_key)}
                        >
                          {isOpen ? <ChevronUp size={14} /> : <ChevronDown size={14} />}
                        </button>
                      </div>
                    </div>
                  </Card>
                );
              })}
            </div>
          )}
        </div>
        <div><ActivityRail /></div>
      </div>
    </div>
  );
}
