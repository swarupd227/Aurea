import { RulesTable, type Rulebook } from "@/components/views/RulebookView";

/** read_compliance_rules, set_compliance_rule and set_agent_control: the rules as the firm runs them. */
export function RulebookCard({ result }: { result: Rulebook }) {
  const off = result.rules.filter((r) => !r.enabled).length;
  return (
    <div className="overflow-hidden rounded-lg border border-border bg-surface" data-testid="rulebook-card">
      <div className="flex flex-wrap items-center justify-between gap-2 border-b border-border-soft px-3 py-2">
        <span className="text-sm font-medium text-ink">{result.name} v{result.version}</span>
        <div className="flex gap-1">
          <span className="chip">{result.rules.length} rules</span>
          {off > 0 && <span className="chip text-crit">{off} off</span>}
        </div>
      </div>
      <RulesTable book={result} />
    </div>
  );
}
