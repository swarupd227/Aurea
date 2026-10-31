import { firmCurrency, firmLocale } from "@/lib/format";
interface Check {
  code: string;
  severity: "block" | "warn" | "info";
  detail: string;
}

interface InvoiceRow {
  id: string;
  number: string | null;
  kind: "invoice" | "credit_note";
  status: string;
  mandate_name: string;
  household_name: string | null;
  period_start: string;
  period_end: string;
  amount: number;
  currency: string;
  checks: Check[];
  debit_status: string | null;
  shortfall?: number | null;
  credit_of?: string | null;
}

interface Unagreed {
  mandate_name: string;
  household_name: string | null;
  value: number;
  pending_confirmation?: boolean;
}

interface BillingPayload {
  scope?: "firm" | "household";
  household_name?: string;
  invoices: InvoiceRow[];
  open_run?: { as_of: string; awaiting_decision?: boolean; summary?: { amount_by_currency?: Record<string, number> } } | null;
  exceptions?: { no_agreement: Unagreed[]; held: InvoiceRow[]; unpaid: InvoiceRow[] } | null;
  billed_this_year?: Record<string, number>;
}

function money(n: number, cur = firmCurrency()) {
  return `${cur} ${n.toLocaleString(firmLocale(), { minimumFractionDigits: 2, maximumFractionDigits: 2 })}`;
}

const STATUS_TONE: Record<string, string> = {
  debited: "text-ok", issued: "text-ink", draft: "text-ink-muted", held: "text-crit",
  credited: "text-ink-muted", void: "text-ink-muted",
};

function Chip({ text, tone }: { text: string; tone?: string }) {
  return <span className={`chip ${tone ?? "text-ink-muted"}`}>{text}</span>;
}

function label(s: string) {
  return s.replace(/_/g, " ");
}

export function BillingCard({ result }: { result: BillingPayload }) {
  const firm = result.scope !== "household";
  const ex = result.exceptions;
  const billed = Object.entries(result.billed_this_year ?? {});
  const runAmounts = Object.entries(result.open_run?.summary?.amount_by_currency ?? {});

  return (
    <div className="overflow-hidden rounded-lg border border-border bg-surface" data-testid="billing-card">
      <div className="flex items-center justify-between border-b border-border-soft px-3 py-2">
        <div className="text-sm font-medium text-ink">Billing{result.household_name ? ` · ${result.household_name}` : ""}</div>
        {result.open_run && (
          <span className="font-mono text-xs text-warn">
            run awaiting approval · {result.open_run.as_of}
            {runAmounts.length > 0 && ` · ${runAmounts.map(([c, a]) => money(a, c)).join(" + ")}`}
          </span>
        )}
      </div>

      {firm && (
        <div className="flex flex-wrap gap-x-6 gap-y-2 px-3 py-3">
          <div>
            <div className="text-[10px] uppercase tracking-wide text-ink-muted">Billed this year</div>
            <div className="font-mono text-base tabular-nums text-ink">
              {billed.length ? billed.map(([c, a]) => money(a, c)).join(" + ") : "—"}
            </div>
          </div>
          {ex && (
            <div className="flex flex-wrap items-end gap-1.5">
              <Chip text={`${ex.no_agreement.length} no agreement`} tone={ex.no_agreement.length ? "text-warn" : undefined} />
              <Chip text={`${ex.held.length} held`} tone={ex.held.length ? "text-crit" : undefined} />
              <Chip text={`${ex.unpaid.length} unpaid`} tone={ex.unpaid.length ? "text-warn" : undefined} />
            </div>
          )}
        </div>
      )}

      {result.invoices.length > 0 && (
        <table className="w-full border-t border-border-soft text-sm">
          <thead>
            <tr className="text-left text-[10px] uppercase tracking-wide text-ink-muted">
              <th className="px-3 py-1.5 font-medium">Bill</th>
              <th className="px-3 py-1.5 font-medium">Period</th>
              <th className="px-3 py-1.5 text-right font-medium">Amount</th>
              <th className="px-3 py-1.5 font-medium">Status</th>
              <th className="px-3 py-1.5 font-medium">Checks</th>
            </tr>
          </thead>
          <tbody className="divide-y divide-border-soft">
            {result.invoices.map((i) => {
              const blocks = i.checks.filter((c) => c.severity === "block");
              const warns = i.checks.filter((c) => c.severity === "warn");
              return (
                <tr key={i.id}>
                  <td className="px-3 py-1.5">
                    <div className="text-ink">{i.number ?? "Draft"}{i.kind === "credit_note" ? " · credit note" : ""}</div>
                    <div className="text-xs text-ink-muted">{i.household_name ?? i.mandate_name}</div>
                  </td>
                  <td className="px-3 py-1.5 font-mono text-xs text-ink-muted whitespace-nowrap">{i.period_start} → {i.period_end}</td>
                  <td className={`px-3 py-1.5 text-right font-mono tabular-nums whitespace-nowrap ${i.amount < 0 ? "text-ok" : "text-ink"}`}>
                    {money(i.amount, i.currency)}
                  </td>
                  <td className="px-3 py-1.5 whitespace-nowrap">
                    <span className={`font-mono text-xs ${STATUS_TONE[i.status] ?? "text-ink-muted"}`}>{i.status}</span>
                    {i.debit_status === "insufficient_cash" && <div className="font-mono text-[10px] text-warn">unpaid, no cash{i.shortfall ? ` · short ${i.shortfall.toLocaleString()}` : ""}</div>}
                  </td>
                  <td className="px-3 py-1.5">
                    <div className="flex flex-wrap gap-1">
                      {blocks.map((c) => <span key={c.code} className="chip text-crit" title={c.detail}>{label(c.code)}</span>)}
                      {warns.map((c) => <span key={c.code} className="chip text-warn" title={c.detail}>{label(c.code)}</span>)}
                    </div>
                  </td>
                </tr>
              );
            })}
          </tbody>
        </table>
      )}

      {ex && ex.no_agreement.length > 0 && (
        <table className="w-full border-t border-border-soft text-sm">
          <thead>
            <tr className="text-left text-[10px] uppercase tracking-wide text-ink-muted">
              <th className="px-3 py-1.5 font-medium">No agreement on file</th>
              <th className="px-3 py-1.5 text-right font-medium">Balance</th>
            </tr>
          </thead>
          <tbody className="divide-y divide-border-soft">
            {ex.no_agreement.map((u) => (
              <tr key={u.mandate_name}>
                <td className="px-3 py-1.5 text-ink">{u.household_name ?? u.mandate_name}{u.pending_confirmation ? " · awaiting confirmation" : ""}</td>
                <td className="px-3 py-1.5 text-right font-mono tabular-nums text-ink-muted">{u.value.toLocaleString(undefined, { maximumFractionDigits: 0 })}</td>
              </tr>
            ))}
          </tbody>
        </table>
      )}

      {result.invoices.length === 0 && !(ex && ex.no_agreement.length > 0) && (
        <div className="px-3 py-3 font-mono text-xs text-ink-muted">No invoices</div>
      )}
    </div>
  );
}
