// Figures and chips only - the agent's message carries the sentences. Each row is one proposal;
// "may decide" is the server's answer for the caller's role (decision_rights), never a guess here.
interface RecRow {
  id: string;
  title: string;
  agent_key: string;
  subject_label: string | null;
  status: string;
  priority: number;
  confidence: number;
  tier: string;
  can_decide: boolean;
}

interface RecsPayload {
  total: number;
  recommendations: RecRow[];
}

const PRIORITY_COLOR: Record<number, string> = { 1: "text-crit", 2: "text-warn" };

export function RecommendationsCard({ result }: { result: RecsPayload }) {
  return (
    <div className="overflow-hidden rounded-lg border border-border bg-surface" data-testid="recommendations-card">
      <div className="flex items-center justify-between border-b border-border-soft px-3 py-2">
        <div className="text-sm font-medium text-ink">Recommendations</div>
        <div className="font-mono text-xs tabular-nums text-ink-muted">
          {result.recommendations.length}
          {result.total > result.recommendations.length ? ` of ${result.total}` : ""}
        </div>
      </div>
      <table className="w-full text-sm">
        <tbody className="divide-y divide-border-soft">
          {result.recommendations.map((r) => (
            <tr key={r.id}>
              <td className="px-3 py-1.5">
                <div className="text-ink">{r.title}</div>
                <div className="text-xs text-ink-muted">
                  {r.subject_label || "Firm-wide"} · {r.agent_key.replace(/_/g, " ")}
                </div>
              </td>
              <td className={`px-3 py-1.5 font-mono text-xs tabular-nums ${PRIORITY_COLOR[r.priority] || "text-ink-muted"}`}>
                P{r.priority}
              </td>
              <td className="px-3 py-1.5 text-right font-mono text-xs tabular-nums text-ink-muted whitespace-nowrap">
                {Math.round(r.confidence * 100)}%
              </td>
              <td className="px-3 py-1.5 text-right text-xs whitespace-nowrap">
                {r.status !== "proposed" ? (
                  <span className="text-ink-muted">{r.status}</span>
                ) : r.can_decide ? (
                  <span className="text-ok">you may decide</span>
                ) : (
                  <span className="text-ink-muted">not yours to decide</span>
                )}
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}
