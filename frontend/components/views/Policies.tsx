import { TierBadge } from "@/components/ui";
import { money } from "@/lib/format";

/** `/api/autonomy/policies` and the read/set_autonomy_policy tools (backend `aurea_core/autonomy.py`). */
export interface PoliciesData {
  policies: {
    id: string; agent: string; agent_key: string; mandate_type: string | null; tier: string; effective_tier: string;
    guardrails: { key: string; value: any; enforced: boolean; does: string }[]; rationale: string | null; agent_default: string | null;
  }[];
  licence_ceiling: string | null;
}

const RAIL: Record<string, (v: any) => string> = {
  max_trade_value: (v) => `orders over ${money(Number(v))} wait`,
  requires_compliance: (v) => (v ? "every proposal waits" : "compliance not required"),
};

/** Each policy: the agent, the mandates it covers, its tier (and the licence's cap), its guardrails and whether each is enforced. */
export function PoliciesTable({ data }: { data: PoliciesData }) {
  if (!data.policies.length) return <div className="px-3 py-2 text-xs text-ink-muted">No policies · every agent at its configured tier</div>;
  return (
    <div className="overflow-x-auto">
      <table className="w-full text-xs" data-testid="policies-table">
        <thead><tr className="text-left text-[10px] uppercase tracking-wide text-ink-muted">
          <th className="px-3 py-1.5 font-medium">Agent</th><th className="px-3 py-1.5 font-medium">Mandates</th>
          <th className="px-3 py-1.5 font-medium">Tier</th><th className="px-3 py-1.5 font-medium">Guardrails</th>
        </tr></thead>
        <tbody className="divide-y divide-border-soft">
          {data.policies.map((p) => (
            <tr key={p.id}>
              <td className="px-3 py-1.5 text-ink">{p.agent}</td>
              <td className="px-3 py-1.5 text-ink-soft">{p.mandate_type ?? "All"}</td>
              <td className="px-3 py-1.5">
                <div className="flex flex-wrap items-center gap-1">
                  <TierBadge tier={p.effective_tier} />
                  {p.effective_tier !== p.tier && <span className="chip text-warn">licence caps {p.tier.replace("_", " ")}</span>}
                </div>
              </td>
              <td className="px-3 py-1.5">
                <div className="flex flex-wrap gap-1">
                  {p.guardrails.length === 0 && <span className="text-ink-muted">—</span>}
                  {p.guardrails.map((g) => (
                    <span key={g.key} className={`chip ${g.enforced ? "" : "text-ink-muted line-through"}`} title={g.does}>
                      {g.enforced ? (RAIL[g.key]?.(g.value) ?? g.key) : `${g.key.replace(/_/g, " ")} · not enforced`}
                    </span>
                  ))}
                </div>
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}
