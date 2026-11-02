// Figures and chips only - the agent's message carries the sentences. Each row is one proposal;
// whether it may be decided is the server's answer for the caller's role (decision_rights), never a
// guess here. A row says so only when the answer is no: "you may decide" on every line says nothing.
interface RecOrder {
  side: string;
  symbol: string;
  quantity: number;
  est_value: number;
}

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
  orders?: RecOrder[];
}

interface RecsPayload {
  total: number;
  recommendations: RecRow[];
}

const PRIORITY_COLOR: Record<number, string> = { 1: "text-crit", 2: "text-warn" };

const units = (n: number) => Number(n).toLocaleString("en-NZ", { maximumFractionDigits: 3 });

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
      <ul className="divide-y divide-border-soft text-sm">
        {result.recommendations.map((r) => (
          <li key={r.id} className="flex items-start gap-3 px-3 py-1.5">
            <div className="min-w-0 flex-1">
              <div className="text-ink">{r.title}</div>
              <div className="text-xs text-ink-muted">
                {r.subject_label || "Firm-wide"} · {r.agent_key.replace(/_/g, " ")}
              </div>
              {!!r.orders?.length && (
                <div className="mt-1 flex flex-wrap gap-1.5" data-testid="recommendation-orders">
                  {r.orders.map((o, i) => (
                    <span key={i} className="chip text-ink-muted">
                      {o.side} {units(o.quantity)} {o.symbol}
                    </span>
                  ))}
                </div>
              )}
            </div>
            <div className="shrink-0 text-right font-mono text-xs tabular-nums">
              <div className={PRIORITY_COLOR[r.priority] || "text-ink-muted"} title="Priority">P{r.priority}</div>
              <div className="text-ink-muted" title="Agent's confidence">{Math.round(r.confidence * 100)}%</div>
              {r.status !== "proposed" ? (
                <div className="font-sans text-ink-muted">{r.status}</div>
              ) : !r.can_decide ? (
                <div className="font-sans text-ink-muted">not yours</div>
              ) : null}
            </div>
          </li>
        ))}
      </ul>
    </div>
  );
}
