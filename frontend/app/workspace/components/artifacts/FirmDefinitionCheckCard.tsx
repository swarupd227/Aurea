import { AlertTriangle, Check } from "lucide-react";

interface FirmDefinitionPayload {
  discretionary_aum: number;
  composite_eligible_aum: number;
  uncaptured_aum: number;
  clean: boolean;
}

function money(n: number) {
  return n.toLocaleString(undefined, { style: "currency", currency: "USD", maximumFractionDigits: 0 });
}

export function FirmDefinitionCheckCard({ result }: { result: FirmDefinitionPayload }) {
  return (
    <div className="overflow-hidden rounded-lg border border-border bg-surface">
      <div className="flex items-center justify-between border-b border-border-soft px-3 py-2">
        <div className="text-sm font-medium text-ink">Firm definition check</div>
        <div className={`inline-flex items-center gap-1.5 font-mono text-xs ${result.clean ? "text-ok" : "text-warn"}`}>
          {result.clean ? <Check className="h-3.5 w-3.5" /> : <AlertTriangle className="h-3.5 w-3.5" />}
          {result.clean ? "fully captured" : `${money(result.uncaptured_aum)} uncaptured`}
        </div>
      </div>
      <table className="w-full text-sm">
        <tbody className="divide-y divide-border-soft">
          <tr>
            <td className="px-3 py-1.5 text-ink-muted">Discretionary AUM</td>
            <td className="px-3 py-1.5 text-right font-mono tabular-nums text-ink">{money(result.discretionary_aum)}</td>
          </tr>
          <tr>
            <td className="px-3 py-1.5 text-ink-muted">Composite-eligible AUM</td>
            <td className="px-3 py-1.5 text-right font-mono tabular-nums text-ink">{money(result.composite_eligible_aum)}</td>
          </tr>
        </tbody>
      </table>
    </div>
  );
}
