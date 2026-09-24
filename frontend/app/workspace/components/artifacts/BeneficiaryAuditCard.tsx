import { AlertTriangle, Check } from "lucide-react";

interface Gap {
  account_name: string;
  registration_type: string;
  issue: string;
}

interface AuditPayload {
  accounts_checked: number;
  gaps: Gap[];
  clean: boolean;
}

export function BeneficiaryAuditCard({ result }: { result: AuditPayload }) {
  return (
    <div className="overflow-hidden rounded-lg border border-border bg-surface">
      <div className="flex items-center justify-between border-b border-border-soft px-3 py-2">
        <div className="text-sm font-medium text-ink">Beneficiary audit</div>
        <div className={`inline-flex items-center gap-1.5 font-mono text-xs ${result.clean ? "text-ok" : "text-warn"}`}>
          {result.clean ? <Check className="h-3.5 w-3.5" /> : <AlertTriangle className="h-3.5 w-3.5" />}
          {result.clean ? "all covered" : `${result.gaps.length} gap(s)`}
        </div>
      </div>
      {result.clean ? (
        <div className="px-3 py-3 text-xs text-ink-muted">
          {result.accounts_checked} account(s) needing beneficiaries, all covered.
        </div>
      ) : (
        <table className="w-full text-sm">
          <tbody className="divide-y divide-border-soft">
            {result.gaps.map((g, i) => (
              <tr key={i}>
                <td className="px-3 py-1.5">
                  <div className="text-ink">{g.account_name}</div>
                  <div className="text-xs text-ink-muted">{g.registration_type.replace(/_/g, " ")}</div>
                </td>
                <td className="px-3 py-1.5 text-right text-xs text-warn whitespace-nowrap">{g.issue}</td>
              </tr>
            ))}
          </tbody>
        </table>
      )}
    </div>
  );
}
