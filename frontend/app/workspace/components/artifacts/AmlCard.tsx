const TONE: Record<string, string> = {
  open: "text-warn", needs_info: "text-warn", awaiting_confirmation: "text-crit", true_match: "text-crit", false_positive: "text-ok",
  edd_pending: "text-warn", edd_declined: "text-crit", edd_complete: "text-ok",
};

export function AmlCard({ result }: { result: any }) {
  const s = result.summary;
  const hits: any[] = (result.hits ?? []).filter((h: any) => h.state !== "false_positive" || h.qa_due).slice(0, 6);
  return (
    <div className="overflow-hidden rounded-lg border border-border bg-surface" data-testid="aml-card">
      <div className="flex flex-wrap items-center justify-between gap-2 border-b border-border-soft px-3 py-2">
        <div className="text-sm font-medium text-ink">{result.case ? `AML · ${result.case}` : "AML reviews"}</div>
        <div className="flex flex-wrap gap-1.5">
          <span className="chip text-ink-muted">{s.open} open</span>
          {s.awaiting_confirmation > 0 && <span className="chip text-crit">{s.awaiting_confirmation} second review</span>}
          {s.edd_pending > 0 && <span className="chip text-warn">{s.edd_pending} EDD</span>}
        </div>
      </div>
      <div className="divide-y divide-border-soft">
        {hits.map((h) => (
          <div key={`${h.party_id}-${h.hit}`} className="flex flex-wrap items-center gap-1.5 px-3 py-1.5">
            <span className="text-sm text-ink">{h.party}</span>
            <span className="text-xs text-ink-muted">{h.category.replace(/_/g, " ")} · {h.matched_name}</span>
            <span className={`chip ${TONE[h.state] ?? "text-ink-muted"}`}>{h.state.replace(/_/g, " ")}</span>
          </div>
        ))}
        {(result.edd ?? []).slice(0, 4).map((e: any) => (
          <div key={e.case_id} className="flex flex-wrap items-center gap-1.5 px-3 py-1.5">
            <span className="text-sm text-ink">{e.case}</span>
            <span className="text-xs text-ink-muted">EDD · {e.risk_tier ?? "not rated"}</span>
            <span className={`chip ${TONE[e.edd_status] ?? "text-ink-muted"}`}>{String(e.edd_status).replace(/_/g, " ")}</span>
          </div>
        ))}
        {hits.length === 0 && !(result.edd ?? []).length && <div className="px-3 py-2"><span className="chip text-ink-muted">nothing waiting</span></div>}
      </div>
    </div>
  );
}
