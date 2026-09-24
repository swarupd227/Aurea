import { AlertTriangle, Check } from "lucide-react";

interface OverdueItem {
  use_case_key: string;
  name: string;
  risk_tier: string;
}

interface AIGovernancePayload {
  active: number;
  by_risk_tier: { low: number; medium: number; high: number };
  overdue_review: OverdueItem[];
  entitlement_violations: { total: number };
}

export function AIGovernanceCard({ result }: { result: AIGovernancePayload }) {
  const clean = result.overdue_review.length === 0 && result.entitlement_violations.total === 0;

  return (
    <div className="overflow-hidden rounded-lg border border-border bg-surface">
      <div className="flex items-center justify-between border-b border-border-soft px-3 py-2">
        <div className="text-sm font-medium text-ink">AI use-case inventory</div>
        <div className={`inline-flex items-center gap-1.5 font-mono text-xs ${clean ? "text-ok" : "text-warn"}`}>
          {clean ? <Check className="h-3.5 w-3.5" /> : <AlertTriangle className="h-3.5 w-3.5" />}
          {result.active} active
        </div>
      </div>
      <div className="flex items-center gap-3 px-3 py-2 border-b border-border-soft text-xs">
        <span className="text-crit">{result.by_risk_tier.high} high</span>
        <span className="text-warn">{result.by_risk_tier.medium} medium</span>
        <span className="text-ink-muted">{result.by_risk_tier.low} low</span>
        <span className="ml-auto text-ink-muted">
          {result.entitlement_violations.total} violation(s) logged
        </span>
      </div>
      {result.overdue_review.length > 0 && (
        <div className="flex flex-wrap gap-1.5 px-3 py-2">
          {result.overdue_review.map((o) => (
            <span key={o.use_case_key} className="inline-flex items-center gap-1 rounded-full px-2 py-0.5 text-[11px] bg-warn-bg text-warn">
              {o.name} overdue
            </span>
          ))}
        </div>
      )}
    </div>
  );
}
