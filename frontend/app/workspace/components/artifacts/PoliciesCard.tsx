import { PoliciesTable, type PoliciesData } from "@/components/views/Policies";

/** read_autonomy_policies and set_autonomy_policy: the firm's autonomy policies. */
export function PoliciesCard({ result }: { result: PoliciesData }) {
  return (
    <div className="overflow-hidden rounded-lg border border-border bg-surface" data-testid="policies-card">
      <div className="flex flex-wrap items-center justify-between gap-2 border-b border-border-soft px-3 py-2">
        <span className="text-sm font-medium text-ink">Autonomy policies</span>
        {result.licence_ceiling && <span className="chip">licence ceiling {result.licence_ceiling.replace("_", " ")}</span>}
      </div>
      <PoliciesTable data={result} />
    </div>
  );
}
