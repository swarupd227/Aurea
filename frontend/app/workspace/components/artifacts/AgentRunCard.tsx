import { Check, CircleAlert, Loader2 } from "lucide-react";
import { MeetingBrief } from "@/components/MeetingBrief";
import { money } from "@/lib/format";

// A run narrated into the thread: published when it starts, settled when it finishes. Figures,
// chips and the beats of the run - the sentence ("what it found, who decides") is the agent's
// message above it, never a paragraph in here.
interface Beat {
  phase: string;
  label: string | null;
  state: "running" | "done" | "failed";
  detail: string | null;
}

interface Rebalance {
  currency: string | null;
  max_drift: number | null;
  band: number | null;
  classes: { class: string; now: number; target: number | null; after: number }[];
  orders: { side: string; symbol: string; quantity: number; value: number; gain: number | null }[];
  gain: number | null;
  budget: number | null;
  harvested: number;
  cash: { now: number; after: number };
  measured_against: string;
}

interface Proposal {
  id: string;
  title: string;
  priority: number;
  confidence: number;
  status: string;
  rebalance?: Rebalance | null;
  brief?: any;
}

const pct = (v: number | null | undefined) => (v == null ? "—" : `${(v * 100).toFixed(1)}%`);
const label = (c: string) => c.charAt(0).toUpperCase() + c.slice(1).replace(/_/g, " ");

/** What a rebalance would do: each class now, its target and after the orders; the orders; gain, budget and cash. */
function RebalanceFigures({ r }: { r: Rebalance }) {
  const say = (v: number | null | undefined) => (v == null ? "—" : money(v, r.currency ?? undefined));
  const over = r.budget != null && r.gain != null && r.gain > r.budget;
  // The optimiser stops selling where the tax budget runs out, which is why a class can stay outside its band.
  const atBudget = !over && r.budget != null && r.budget > 0 && r.gain != null && r.gain >= r.budget * 0.995;
  return (
    <div className="space-y-2 border-t border-border-soft px-3 py-2" data-testid="rebalance-figures">
      <table className="w-full text-xs">
        <thead><tr className="text-left text-[10px] uppercase tracking-wide text-ink-muted">
          <th className="py-1 font-medium">Class</th><th className="py-1 text-right font-medium">Now</th>
          <th className="py-1 text-right font-medium">Target</th><th className="py-1 text-right font-medium">After</th>
        </tr></thead>
        <tbody className="divide-y divide-border-soft font-mono tabular-nums">
          {r.classes.map((c) => {
            const off = c.target != null && r.band != null && Math.abs(c.after - c.target) > r.band;
            return (
              <tr key={c.class}>
                <td className="py-1 font-sans text-ink">{label(c.class)}</td>
                <td className="py-1 text-right text-ink-muted">{pct(c.now)}</td>
                <td className="py-1 text-right text-ink-muted">{pct(c.target)}</td>
                <td className={`py-1 text-right ${off ? "text-warn" : "text-ink"}`}>{pct(c.after)}</td>
              </tr>
            );
          })}
        </tbody>
      </table>
      {r.orders.length > 0 && (
        <div className="flex flex-wrap gap-1">
          {r.orders.map((o, i) => (
            <span key={i} className={`chip font-mono ${o.side === "sell" ? "text-crit" : "text-ok"}`}>
              {o.side === "sell" ? "Sell" : "Buy"} {Number(o.quantity).toLocaleString(undefined, { maximumFractionDigits: 2 })} {o.symbol} · {say(o.value)}
            </span>
          ))}
        </div>
      )}
      <div className="flex flex-wrap gap-1">
        <span className="chip">drift {pct(r.max_drift)} / band {pct(r.band)} · {r.measured_against}</span>
        <span className={`chip ${over ? "text-crit" : ""}`}>gain {say(r.gain)}{r.budget != null ? ` / budget ${say(r.budget)}` : " · no budget"}</span>
        {atBudget && <span className="chip text-warn">at the tax budget</span>}
        {r.harvested > 0 && <span className="chip text-ok">harvested {say(r.harvested)}</span>}
        <span className="chip">cash {say(r.cash.now)} → {say(r.cash.after)}</span>
      </div>
    </div>
  );
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
      {data.proposals.length === 1 && data.proposals[0].rebalance && <RebalanceFigures r={data.proposals[0].rebalance} />}
      {data.proposals.length === 1 && data.proposals[0].brief && (
        <div className="border-t border-border-soft px-3 py-2"><MeetingBrief brief={data.proposals[0].brief} /></div>
      )}

      {data.flags > 0 && (
        <div className="border-t border-border-soft px-3 py-1.5 font-mono text-xs text-warn">
          {data.flags} surveillance flag{data.flags === 1 ? "" : "s"}
        </div>
      )}
    </div>
  );
}
