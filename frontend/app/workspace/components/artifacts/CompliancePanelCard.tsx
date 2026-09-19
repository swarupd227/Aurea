import { AlertTriangle } from "lucide-react";

interface ConflictRow {
  conflict_key: string;
  title: string;
  status: string;
  last_reviewed_at: string | null;
}

interface WspRow {
  rule_key: string;
  obligation: string;
  frequency: string;
  evidence_automated: boolean;
  last_evidence_at: string | null;
}

interface CompliancePayload {
  conflicts: ConflictRow[];
  wsp_rules: WspRow[];
  summary: {
    conflicts: { overdue_review: { conflict_key: string }[] };
    evidence_coverage: { coverage_pct: number; stale_evidence: { rule_key: string }[] };
  };
}

export function CompliancePanelCard({ result }: { result: CompliancePayload }) {
  const overdueKeys = new Set(result.summary.conflicts.overdue_review.map((r) => r.conflict_key));
  const staleKeys = new Set(result.summary.evidence_coverage.stale_evidence.map((r) => r.rule_key));

  return (
    <div className="overflow-hidden rounded-lg border border-border bg-surface">
      <div className="flex items-center justify-between border-b border-border-soft px-3 py-2">
        <div className="text-sm font-medium text-ink">Compliance program</div>
        <div className="font-mono text-xs tabular-nums text-ink-muted">
          {result.summary.evidence_coverage.coverage_pct}% evidence coverage
        </div>
      </div>
      <table className="w-full text-sm">
        <tbody className="divide-y divide-border-soft">
          {result.conflicts.map((c) => (
            <tr key={c.conflict_key}>
              <td className="px-3 py-1.5">
                {overdueKeys.has(c.conflict_key) && (
                  <AlertTriangle className="h-3.5 w-3.5 text-warn" aria-label="Overdue for review" />
                )}
              </td>
              <td className="px-3 py-1.5 text-ink">{c.title}</td>
              <td className="px-3 py-1.5 text-right font-mono text-xs tabular-nums text-ink-muted whitespace-nowrap">
                {c.last_reviewed_at ? `reviewed ${c.last_reviewed_at.slice(0, 10)}` : "never reviewed"}
              </td>
            </tr>
          ))}
        </tbody>
      </table>
      {result.wsp_rules.length > 0 && (
        <div className="flex flex-wrap gap-1.5 border-t border-border-soft px-3 py-2">
          {result.wsp_rules.map((r) => (
            <span
              key={r.rule_key}
              className={`inline-flex items-center gap-1 rounded-full px-2 py-0.5 text-[11px] ${
                staleKeys.has(r.rule_key) ? "bg-warn-bg text-warn" : "bg-border-soft text-ink-muted"
              }`}
              title={r.obligation}
            >
              {r.rule_key.replace(/_/g, " ").replace(/\./g, " · ")}
            </span>
          ))}
        </div>
      )}
    </div>
  );
}
