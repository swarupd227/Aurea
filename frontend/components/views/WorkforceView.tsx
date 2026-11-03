"use client";

import { useState } from "react";
import { Bot, Eye, Activity, ChevronDown, ChevronUp, Play } from "lucide-react";
import { Card, SkeletonCard, TierBadge, Empty, ErrorState } from "@/components/ui";
import ActivityRail from "@/components/ActivityRail";
import { useAgentRunner } from "@/components/AgentConsole";
import { useApi } from "@/lib/hooks";
import { formatDateFull, timeAgo } from "@/lib/format";
import { useViewNav, ViewLink } from "./ViewNav";
import { ViewHeader, chip } from "./ViewHeader";
import { PoliciesTable, type PoliciesData } from "./Policies";

const STATUS: Record<string, { dot: string; label: string }> = {
  working: { dot: "bg-accent animate-pulse", label: "Working" },
  "on-duty": { dot: "bg-ink-muted", label: "On duty" },
  paused: { dot: "bg-crit", label: "Paused" },
};
const TIERS = ["tier_1", "tier_2", "tier_3"];

/** The agent workforce: status, what each watches, what each has done. In the work pane a firm-wide
 * run is the conversation's `run_agent`, behind its Confirm card; on a page it runs directly. */
export function WorkforceView() {
  const nav = useViewNav();
  const { data, loading, error, refetch } = useApi<any[]>("/api/atlas/workforce");
  const { data: policies } = useApi<PoliciesData>("/api/autonomy/policies");
  const [open, setOpen] = useState<string | null>(null);
  const runner = useAgentRunner();

  function runFirm(a: any) {
    if (nav.say) return nav.say(`Run the ${a.name} agent (${a.agent_key}) across the firm.`);
    runner.run({ agentKey: a.agent_key, subjectType: "firm", label: "Manual run" });
  }

  const agents = data || [];
  const paused = agents.filter((a) => a.paused).length;
  const working = agents.filter((a) => a.status === "working").length;

  return (
    <div className="view-container" data-testid="workforce-view">
      <ViewHeader
        title="Workforce"
        meta={agents.length ? (
          <>
            <span className={`${chip} tabular-nums`}>{agents.length} agents</span>
            {working > 0 && <span className="chip bg-border-soft text-accent tabular-nums">Working {working}</span>}
            {paused > 0 && <span className="chip bg-crit-bg text-crit tabular-nums">Paused {paused}</span>}
            {TIERS.map((t) => {
              const n = agents.filter((a) => a.tier === t).length;
              return n ? (
                <span key={t} className="inline-flex items-center gap-1">
                  <TierBadge tier={t} /><span className="font-mono text-xs tabular-nums text-ink-muted">{n}</span>
                </span>
              ) : null;
            })}
          </>
        ) : undefined}
      />

      {policies && (
        <Card className="mb-5 overflow-hidden p-0" data-testid="workforce-policies">
          <div className="flex flex-wrap items-center justify-between gap-2 border-b border-border-soft px-3 py-2">
            <span className="text-sm font-medium text-ink">Autonomy policies</span>
            {policies.licence_ceiling && <span className={chip}>licence ceiling {policies.licence_ceiling.replace("_", " ")}</span>}
          </div>
          <PoliciesTable data={policies} />
        </Card>
      )}

      <div className="grid gap-5 cq-lg:grid-cols-3">
        <div className="space-y-3 cq-lg:col-span-2">
          {loading ? (
            <div className="grid gap-3 cq-sm:grid-cols-2">
              {Array.from({ length: 6 }).map((_, i) => <SkeletonCard key={i} rows={3} />)}
            </div>
          ) : error ? (
            <Card><ErrorState what="your workforce" message={error} onRetry={refetch} /></Card>
          ) : !agents.length ? (
            <Card>
              <Empty>
                No agents configured ·{" "}
                <ViewLink href="/admin" className="text-ink underline">Configuration</ViewLink>
              </Empty>
            </Card>
          ) : (
            <div className="grid gap-3 cq-sm:grid-cols-2">
              {agents.map((a) => {
                const st = STATUS[a.status] || STATUS["on-duty"];
                const isOpen = open === a.agent_key;
                const isHouseholdScope = a.subject === "household";
                return (
                  <Card key={a.agent_key} className={`min-w-0 ${a.paused ? "border-crit" : ""}`} data-testid="workforce-agent">
                    <div className="flex items-start gap-3">
                      <span className="flex h-10 w-10 shrink-0 items-center justify-center rounded-xl border border-border bg-surface text-ink-soft">
                        <Bot size={18} />
                      </span>
                      <div className="min-w-0 flex-1">
                        <div className="flex items-center justify-between gap-2">
                          <div className="truncate text-sm font-semibold text-ink">{a.name}</div>
                          <span className="inline-flex shrink-0 items-center gap-1 text-[11px] text-ink-muted">
                            <span className={`h-1.5 w-1.5 rounded-full ${st.dot}`} /> {st.label}
                          </span>
                        </div>
                        <div className="text-xs text-ink-muted">{a.stage}</div>
                        <div className="mt-1.5"><TierBadge tier={a.tier} /></div>
                      </div>
                    </div>

                    {a.paused && (
                      <div className="mt-2 flex flex-wrap items-center justify-between gap-2">
                        <span className="chip bg-crit-bg text-crit">Paused by surveillance</span>
                        <ViewLink href="/admin" className="text-xs font-medium text-ink-soft hover:text-ink">Configuration</ViewLink>
                      </div>
                    )}

                    <div className="mt-3 space-y-1.5 text-xs">
                      <div className="flex gap-1.5"><Eye size={13} className="mt-0.5 shrink-0 text-ink-muted" /><span className="text-ink-soft">{a.watching}</span></div>
                      <div className="flex gap-1.5">
                        <Activity size={13} className="mt-0.5 shrink-0 text-ink-muted" />
                        <span className="text-ink-soft" title={formatDateFull(a.last_at)}>
                          {a.last_action}{a.last_at ? ` · ${timeAgo(a.last_at)}` : ""}
                        </span>
                      </div>
                    </div>

                    {isOpen && (
                      <div className="mt-2 space-y-1.5 border-t border-border-soft pt-2 text-xs">
                        <div><span className="text-ink-muted">Acts </span><span className="text-ink-soft">{a.acts}</span></div>
                        <div><span className="text-ink-muted">Checkpoint </span><span className="text-ink-soft">{a.checkpoint}</span></div>
                      </div>
                    )}

                    <div className="mt-3 flex flex-wrap items-center gap-3 border-t border-border-soft pt-2 text-xs text-ink-muted">
                      <span className="tabular-nums"><b className="text-ink">{a.runs}</b> runs</span>
                      <span className="tabular-nums"><b className="text-ink">{a.contributions}</b> proposed</span>
                      <div className="ml-auto flex items-center gap-2">
                        {!a.paused && (
                          isHouseholdScope ? (
                            <span className="chip bg-border-soft text-ink-muted">Per client</span>
                          ) : (
                            <button className="btn-ghost px-2 py-1 text-xs" onClick={() => runFirm(a)} data-testid="workforce-run">
                              <Play size={12} /> {nav.say ? "Ask Astra to run" : "Run"}
                            </button>
                          )
                        )}
                        <button
                          className="btn-ghost px-1.5 py-1 text-xs"
                          aria-label={isOpen ? "Collapse" : "Expand"}
                          aria-expanded={isOpen}
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
        <div className="min-w-0"><ActivityRail /></div>
      </div>
    </div>
  );
}
