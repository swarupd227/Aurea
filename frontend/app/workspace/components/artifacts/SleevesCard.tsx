import { AlertTriangle, Check } from "lucide-react";

interface SleeveRow {
  id: string;
  name: string;
  target_weight: number;
  status: string;
}

interface Break {
  holding_id: string;
  instrument_id: string;
  unattributed_quantity: number;
}

interface SleevesPayload {
  sleeves: SleeveRow[];
  reconciliation: { breaks: Break[]; orphaned_attributions: string[]; clean: boolean } | null;
}

export function SleevesCard({ result }: { result: SleevesPayload }) {
  const clean = result.reconciliation?.clean ?? true;

  return (
    <div className="overflow-hidden rounded-lg border border-border bg-surface">
      <div className="flex items-center justify-between border-b border-border-soft px-3 py-2">
        <div className="text-sm font-medium text-ink">Sleeves</div>
        <div className={`inline-flex items-center gap-1.5 font-mono text-xs ${clean ? "text-ok" : "text-warn"}`}>
          {clean ? <Check className="h-3.5 w-3.5" /> : <AlertTriangle className="h-3.5 w-3.5" />}
          {clean ? "reconciled" : "break found"}
        </div>
      </div>
      <table className="w-full text-sm">
        <tbody className="divide-y divide-border-soft">
          {result.sleeves.map((s) => (
            <tr key={s.id}>
              <td className="px-3 py-1.5 text-ink">{s.name}</td>
              <td className="px-3 py-1.5 text-right font-mono text-xs tabular-nums text-ink-muted whitespace-nowrap">
                {(s.target_weight * 100).toFixed(0)}% target
              </td>
              <td className="px-3 py-1.5 text-right text-xs text-ink-muted whitespace-nowrap">{s.status}</td>
            </tr>
          ))}
        </tbody>
      </table>
      {result.reconciliation && !result.reconciliation.clean && (
        <div className="border-t border-border-soft px-3 py-2 text-xs text-warn">
          {result.reconciliation.breaks.length} unattributed break(s),{" "}
          {result.reconciliation.orphaned_attributions.length} orphaned attribution(s)
        </div>
      )}
    </div>
  );
}
