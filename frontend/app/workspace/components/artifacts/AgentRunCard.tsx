import { Check, CircleAlert, Loader2 } from "lucide-react";

// A run narrated into the thread: published when it starts, settled when it finishes. Figures,
// chips and the beats of the run - the sentence ("what it found, who decides") is the agent's
// message above it, never a paragraph in here.
interface Beat {
  phase: string;
  label: string | null;
  state: "running" | "done" | "failed";
  detail: string | null;
}

interface Proposal {
  id: string;
  title: string;
  priority: number;
  confidence: number;
  status: string;
}

export interface AgentRunData {
  status: "running" | "proposed" | "acted" | "nothing" | "failed" | "interrupted";
  agent_key: string;
  agent_name: string;
  subject_label: string | null;
  tier: string;
  tier_label: string;
  held_to_proposal: boolean;
  beats: Beat[];
  flags: number;
  proposals: Proposal[];
}

const PHASE: Record<string, string> = { sense: "Sense", think: "Reason", check: "Check" };

const STATUS: Record<AgentRunData["status"], { label: string; tone: string }> = {
  running: { label: "Working", tone: "text-ink" },
  proposed: { label: "Proposed", tone: "text-ok" },
  acted: { label: "Acted", tone: "text-ok" },
  nothing: { label: "Nothing to surface", tone: "text-ink-muted" },
  failed: { label: "Failed", tone: "text-crit" },
  interrupted: { label: "Interrupted", tone: "text-warn" },
};

export function isAgentRunData(d: any): d is AgentRunData {
  return !!d && typeof d.status === "string" && Array.isArray(d.beats) && typeof d.agent_name === "string";
}

export function AgentRunCard({ data }: { data: AgentRunData }) {
  const status = STATUS[data.status] ?? STATUS.nothing;
  return (
    <div className="overflow-hidden rounded-lg border border-border bg-surface" data-testid="agent-run-card">
      <div className="flex flex-wrap items-center gap-x-3 gap-y-1 border-b border-border-soft px-3 py-2">
        <div className="text-sm font-medium text-ink">{data.agent_name}</div>
        {data.subject_label && <div className="text-xs text-ink-muted">{data.subject_label}</div>}
        <div className="flex-1" />
        <span className="rounded border border-border px-1.5 py-0.5 font-mono text-[11px] text-ink-muted" title="Autonomy tier">
          {data.tier_label}
        </span>
        {data.held_to_proposal && (
          <span className="rounded border border-border px-1.5 py-0.5 font-mono text-[11px] text-ink-muted" title="Held to a proposal">
            proposal only
          </span>
        )}
        <span className={`inline-flex items-center gap-1.5 font-mono text-xs ${status.tone}`} data-testid="agent-run-status">
          {data.status === "running" && <Loader2 className="h-3 w-3 animate-spin text-accent" aria-hidden />}
          {status.label}
        </span>
      </div>

      {data.beats.length > 0 && (
        <ol className="space-y-1 px-3 py-2 font-mono text-xs" aria-label="Beats of the run">
          {data.beats.map((b, i) => (
            <li key={i} className="flex min-w-0 items-center gap-2 text-ink-muted">
              {b.state === "running" ? (
                <Loader2 className="h-3 w-3 shrink-0 animate-spin text-accent" aria-label="running" />
              ) : b.state === "done" ? (
                <Check className="h-3 w-3 shrink-0 text-ok" aria-label="done" />
              ) : (
                <CircleAlert className="h-3 w-3 shrink-0 text-crit" aria-label="failed" />
              )}
              <span className="w-14 shrink-0 text-ink-soft">{PHASE[b.phase] ?? b.phase}</span>
              <span className="min-w-0 truncate">{b.detail ?? b.label}</span>
            </li>
          ))}
        </ol>
      )}

      {data.proposals.length > 0 && (
        <table className="w-full border-t border-border-soft text-sm">
          <tbody className="divide-y divide-border-soft">
            {data.proposals.map((p) => (
              <tr key={p.id}>
                <td className="px-3 py-1.5 text-ink">{p.title}</td>
                <td className="px-3 py-1.5 font-mono text-xs tabular-nums text-ink-muted">P{p.priority}</td>
                <td className="px-3 py-1.5 text-right font-mono text-xs tabular-nums text-ink-muted">
                  {Math.round(p.confidence * 100)}%
                </td>
                <td className="px-3 py-1.5 text-right text-xs text-ink-muted whitespace-nowrap">{p.status}</td>
              </tr>
            ))}
          </tbody>
        </table>
      )}

      {data.flags > 0 && (
        <div className="border-t border-border-soft px-3 py-1.5 font-mono text-xs text-warn">
          {data.flags} surveillance flag{data.flags === 1 ? "" : "s"}
        </div>
      )}
    </div>
  );
}
