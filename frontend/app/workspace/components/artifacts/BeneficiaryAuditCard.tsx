import { AlertTriangle, Check } from "lucide-react";
import { titleCase } from "@/lib/format";

interface Gap {
  account_name: string;
  registration_type: string;
  issue: string;
}

interface AuditPayload {
  market?: string | null;
  accounts_total?: number;
  accounts_checked: number;
  accounts_covered?: number;
  gaps: Gap[];
  not_checked?: { account_name: string; reason: string }[];
  state?: "nothing_checked" | "gaps" | "covered";
  clean: boolean;
}

export function BeneficiaryAuditCard({ result }: { result: AuditPayload }) {
  // An earlier card in a conversation has no state: nothing checked is still never "all covered".
  const nothing = !result.accounts_checked;
  const covered = !nothing && result.gaps.length === 0;
  const left = result.not_checked?.length ?? 0;
  return (
    <div className="overflow-hidden rounded-lg border border-border bg-surface" data-testid="beneficiary-audit-card">
      <div className="flex items-center justify-between border-b border-border-soft px-3 py-2">
        <div className="text-sm font-medium text-ink">{result.market === "IN" ? "Nominations" : "Beneficiary audit"}</div>
        <div className={`inline-flex items-center gap-1.5 font-mono text-xs ${covered ? "text-ok" : "text-warn"}`}>
          {covered ? <Check className="h-3.5 w-3.5" /> : <AlertTriangle className="h-3.5 w-3.5" />}
          {nothing ? "nothing checked" : covered ? "all covered" : `${result.gaps.length} gap(s)`}
        </div>
      </div>
      <div className="flex flex-wrap gap-1.5 px-3 py-2">
        <span className="chip">{result.accounts_checked} checked</span>
        {result.accounts_covered != null && !nothing && <span className="chip">{result.accounts_covered} covered</span>}
        {left > 0 && <span className="chip text-ink-muted">{left} not checked</span>}
      </div>
      {result.gaps.length > 0 && (
        <table className="w-full text-sm">
          <tbody className="divide-y divide-border-soft">
            {result.gaps.map((g, i) => (
              <tr key={i}>
                <td className="px-3 py-1.5">
                  <div className="text-ink">{g.account_name}</div>
                  <div className="text-xs text-ink-muted">{titleCase(g.registration_type)}</div>
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
