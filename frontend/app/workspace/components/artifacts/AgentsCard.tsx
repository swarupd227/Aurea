// The firm's agents with the caller's rights over each. Figures and chips only; the reason an
// agent cannot be started is the server's wording, shown as a chip.
interface AgentRow {
  agent_key: string;
  name: string;
  stage: string;
  tier: string;
  tier_label: string;
  status: string;
  paused: boolean;
  runs: number;
  proposals: number;
  can_run: boolean;
  why_not: string | null;
}

interface AgentsPayload {
  total: number;
  startable: number;
  paused: number;
  agents: AgentRow[];
}

export function AgentsCard({ result }: { result: AgentsPayload }) {
  return (
    <div className="overflow-hidden rounded-lg border border-border bg-surface" data-testid="agents-card">
      <div className="flex items-center justify-between border-b border-border-soft px-3 py-2">
        <div className="text-sm font-medium text-ink">Agents</div>
        <div className="font-mono text-xs tabular-nums text-ink-muted">
          {result.startable} of {result.total} startable
          {result.paused > 0 ? ` · ${result.paused} paused` : ""}
        </div>
      </div>
      <table className="w-full text-sm">
        <tbody className="divide-y divide-border-soft">
          {result.agents.map((a) => (
            <tr key={a.agent_key}>
              <td className="px-3 py-1.5">
                <div className="text-ink">{a.name}</div>
                <div className="text-xs text-ink-muted">{a.stage}</div>
              </td>
              <td className="px-3 py-1.5 font-mono text-xs text-ink-muted whitespace-nowrap">{a.tier_label}</td>
              <td className="px-3 py-1.5 text-right font-mono text-xs tabular-nums text-ink-muted whitespace-nowrap">
                {a.runs} runs · {a.proposals} proposals
              </td>
              <td className="px-3 py-1.5 text-right text-xs whitespace-nowrap">
                {a.can_run ? (
                  <span className="text-ok">can start</span>
                ) : (
                  <span className={a.paused ? "text-warn" : "text-ink-muted"}>{a.why_not}</span>
                )}
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}
