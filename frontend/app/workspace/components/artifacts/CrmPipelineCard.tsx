const STAGE_COLOR: Record<string, string> = {
  lead: "bg-ink-muted", qualified: "bg-info", proposal: "bg-warn", won: "bg-ok", lost: "bg-crit",
};

interface Opportunity {
  id: string;
  contact_name: string | null;
  title: string;
  stage: string;
  estimated_aum: number | null;
  probability_pct: number;
}

interface CrmPayload {
  opportunities: Opportunity[];
  summary: {
    by_stage: Record<string, number>;
    weighted_open_value: number;
    win_rate_pct: number | null;
  };
}

function money(n: number) {
  return n.toLocaleString(undefined, { style: "currency", currency: "USD", maximumFractionDigits: 0 });
}

export function CrmPipelineCard({ result }: { result: CrmPayload }) {
  const stages = ["lead", "qualified", "proposal", "won", "lost"];

  return (
    <div className="overflow-hidden rounded-lg border border-border bg-surface">
      <div className="flex items-center justify-between border-b border-border-soft px-3 py-2">
        <div className="text-sm font-medium text-ink">Pipeline</div>
        <div className="font-mono text-sm tabular-nums text-ink">
          {money(result.summary.weighted_open_value)} weighted
        </div>
      </div>
      <div className="flex flex-wrap items-center gap-2 border-b border-border-soft px-3 py-2">
        {stages.map((s) => (
          <span key={s} className="inline-flex items-center gap-1.5 text-xs text-ink-muted">
            <span className={`h-2 w-2 rounded-full ${STAGE_COLOR[s]}`} />
            {s} ({result.summary.by_stage[s] || 0})
          </span>
        ))}
        {result.summary.win_rate_pct !== null && (
          <span className="ml-auto font-mono text-xs tabular-nums text-ink-muted">
            {result.summary.win_rate_pct}% win rate
          </span>
        )}
      </div>
      <table className="w-full text-sm">
        <tbody className="divide-y divide-border-soft">
          {result.opportunities.map((o) => (
            <tr key={o.id}>
              <td className="px-3 py-1.5">
                <div className="text-ink">{o.title}</div>
                <div className="text-xs text-ink-muted">{o.contact_name || "—"}</div>
              </td>
              <td className="px-3 py-1.5">
                <span className={`inline-flex items-center gap-1.5 text-xs text-ink-muted`}>
                  <span className={`h-1.5 w-1.5 rounded-full ${STAGE_COLOR[o.stage] || "bg-ink-muted"}`} />
                  {o.stage}
                </span>
              </td>
              <td className="px-3 py-1.5 text-right font-mono tabular-nums text-ink whitespace-nowrap">
                {o.estimated_aum ? money(o.estimated_aum) : "—"}
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}
