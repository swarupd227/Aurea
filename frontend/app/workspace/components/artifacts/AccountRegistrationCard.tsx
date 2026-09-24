interface Beneficiary {
  beneficiary_name: string;
  designation_class: string;
  percentage: number;
  relationship_to_owner: string | null;
}

interface RmdStatus {
  status: string;
  amount?: number;
  account_value?: number;
  deadline?: string;
  owner_age?: number;
}

interface RegistrationPayload {
  account_name: string;
  registration_type: string | null;
  rmd: RmdStatus | null;
  beneficiaries: Beneficiary[];
}

function money(n: number) {
  return n.toLocaleString(undefined, { style: "currency", currency: "USD", maximumFractionDigits: 0 });
}

const RMD_COLOR: Record<string, string> = {
  required: "text-warn", ten_year_rule: "text-warn", needs_election: "text-crit",
  not_yet: "text-ink-muted", life_expectancy: "text-ink-muted",
};

export function AccountRegistrationCard({ result }: { result: RegistrationPayload }) {
  return (
    <div className="overflow-hidden rounded-lg border border-border bg-surface">
      <div className="flex items-center justify-between border-b border-border-soft px-3 py-2">
        <div className="text-sm font-medium text-ink">{result.account_name}</div>
        <div className="font-mono text-xs tabular-nums text-ink-muted">
          {(result.registration_type || "unset").replace(/_/g, " ")}
        </div>
      </div>
      {result.rmd && (
        <div className={`px-3 py-2 text-xs border-b border-border-soft ${RMD_COLOR[result.rmd.status] || "text-ink-muted"}`}>
          {result.rmd.status === "required" && `RMD required: ${money(result.rmd.amount!)} (age ${result.rmd.owner_age})`}
          {result.rmd.status === "not_yet" && `No RMD yet — owner is ${result.rmd.owner_age}`}
          {result.rmd.status === "ten_year_rule" && `10-year rule deadline: ${result.rmd.deadline}`}
          {result.rmd.status === "life_expectancy" && "Life-expectancy method — annual RMD"}
          {result.rmd.status === "needs_election" && "Needs a 10-year-rule vs. life-expectancy election"}
          {result.rmd.status === "unknown" && "RMD status unknown — owner's date of birth missing"}
        </div>
      )}
      <table className="w-full text-sm">
        <tbody className="divide-y divide-border-soft">
          {result.beneficiaries.map((b, i) => (
            <tr key={i}>
              <td className="px-3 py-1.5">
                <div className="text-ink">{b.beneficiary_name}</div>
                <div className="text-xs text-ink-muted">
                  {b.designation_class}{b.relationship_to_owner ? ` · ${b.relationship_to_owner}` : ""}
                </div>
              </td>
              <td className="px-3 py-1.5 text-right font-mono tabular-nums text-ink-muted whitespace-nowrap">
                {b.percentage}%
              </td>
            </tr>
          ))}
          {result.beneficiaries.length === 0 && (
            <tr><td className="px-3 py-2 text-xs text-crit">No beneficiaries on file.</td></tr>
          )}
        </tbody>
      </table>
    </div>
  );
}
