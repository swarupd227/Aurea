import { API_BASE, getToken } from "@/lib/api";

interface Check {
  code: string;
  severity: "block" | "warn" | "info";
  detail: string;
}

interface InvoicePayload {
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
  overrides: { code: string; accepted_by: string }[];
  debit_status: string | null;
  shortfall?: number | null;
  credit_of?: string | null;
  calculation?: {
    schedule?: { name: string } | null;
    billing_method?: string;
    billing_frequency?: string;
    householded?: boolean;
    period_fraction?: number;
    periods_per_year?: number;
    billable_value?: number;
    group_value?: number | null;
    annual_fee?: number | null;
    effective_bps?: number | null;
    breakdown?: { band: string; amount: number }[];
    accounts?: { account_name: string; value: number; cash: number }[];
    valuation?: {
      basis_date: string;
      oldest_price_date: string | null;
      stale_days: number | null;
      synthetic_price_symbols: string[];
      declared_price_symbols?: string[];
      unpriced_symbols: string[];
    };
  };
}

function money(n: number, cur: string) {
  return `${cur} ${n.toLocaleString(undefined, { minimumFractionDigits: 2, maximumFractionDigits: 2 })}`;
}

function label(s: string) {
  return s.replace(/_/g, " ");
}

async function downloadPdf(id: string, number: string | null) {
  try {
    const r = await fetch(`${API_BASE}/api/billing/invoices/${id}/pdf`, { headers: { Authorization: `Bearer ${getToken()}` } });
    if (!r.ok) return;
    const a = document.createElement("a");
    a.href = URL.createObjectURL(await r.blob());
    a.download = `${number ?? "invoice-draft"}.pdf`;
    a.click();
  } catch {}
}

export function InvoiceCard({ result }: { result: InvoicePayload }) {
  const c = result.calculation ?? {};
  const val = c.valuation;
  const cur = result.currency;
  const stale = val?.stale_days != null && val.stale_days > 35;
  const payment =
    result.debit_status === "posted" ? { text: "paid from cash", tone: "text-ok" }
    : result.debit_status === "insufficient_cash" ? { text: `unpaid, short ${(result.shortfall ?? 0).toLocaleString()}`, tone: "text-warn" }
    : null;

  return (
    <div className="overflow-hidden rounded-lg border border-border bg-surface" data-testid="invoice-card">
      <div className="flex items-center justify-between border-b border-border-soft px-3 py-2">
        <div className="text-sm font-medium text-ink">
          {result.number ?? "Draft"}{result.kind === "credit_note" ? " · credit note" : ""} · {result.household_name ?? result.mandate_name}
        </div>
        <button className="btn-ghost text-xs" onClick={() => downloadPdf(result.id, result.number)}>PDF</button>
      </div>

      <div className="flex flex-wrap items-end gap-x-8 gap-y-2 px-3 py-3">
        <div>
          <div className="text-[10px] uppercase tracking-wide text-ink-muted">{result.kind === "credit_note" ? "Credited" : "Fee"}</div>
          <div className="font-mono text-xl tabular-nums text-ink">{money(Math.abs(result.amount), cur)}</div>
        </div>
        <div className="font-mono text-xs text-ink-muted">{result.period_start} → {result.period_end}</div>
        <div className="flex flex-wrap gap-1.5">
          <span className="chip text-ink-muted">{result.status}</span>
          {payment && <span className={`chip ${payment.tone}`}>{payment.text}</span>}
          {c.schedule && <span className="chip text-ink-muted">{c.schedule.name}</span>}
          {c.billing_frequency && <span className="chip text-ink-muted">{c.billing_frequency} · {c.billing_method}</span>}
          {c.householded && <span className="chip text-ink-muted">householded</span>}
          {c.period_fraction != null && c.period_fraction < 1 && <span className="chip text-warn">prorated {(c.period_fraction * 100).toFixed(0)}%</span>}
        </div>
      </div>

      {(c.breakdown?.length ?? 0) > 0 && (
        <table className="w-full border-t border-border-soft text-sm">
          <thead>
            <tr className="text-left text-[10px] uppercase tracking-wide text-ink-muted">
              <th className="px-3 py-1.5 font-medium">Band</th>
              <th className="px-3 py-1.5 text-right font-medium">Annual</th>
            </tr>
          </thead>
          <tbody className="divide-y divide-border-soft">
            {c.breakdown!.map((b) => (
              <tr key={b.band}>
                <td className="px-3 py-1.5 text-ink">{b.band}</td>
                <td className="px-3 py-1.5 text-right font-mono tabular-nums text-ink-muted">{b.amount.toLocaleString(undefined, { minimumFractionDigits: 2, maximumFractionDigits: 2 })}</td>
              </tr>
            ))}
            {c.annual_fee != null && (
              <tr>
                <td className="px-3 py-1.5 text-ink-muted">Annual fee · {c.effective_bps?.toFixed(1)} bps · 1 of {c.periods_per_year}</td>
                <td className="px-3 py-1.5 text-right font-mono tabular-nums text-ink">{c.annual_fee.toLocaleString(undefined, { minimumFractionDigits: 2, maximumFractionDigits: 2 })}</td>
              </tr>
            )}
          </tbody>
        </table>
      )}

      {(c.accounts?.length ?? 0) > 0 && (
        <table className="w-full border-t border-border-soft text-sm">
          <thead>
            <tr className="text-left text-[10px] uppercase tracking-wide text-ink-muted">
              <th className="px-3 py-1.5 font-medium">Account</th>
              <th className="px-3 py-1.5 text-right font-medium">Cash</th>
              <th className="px-3 py-1.5 text-right font-medium">Value billed on</th>
            </tr>
          </thead>
          <tbody className="divide-y divide-border-soft">
            {c.accounts!.map((a) => (
              <tr key={a.account_name}>
                <td className="px-3 py-1.5 text-ink">{a.account_name}</td>
                <td className="px-3 py-1.5 text-right font-mono tabular-nums text-ink-muted">{a.cash.toLocaleString(undefined, { maximumFractionDigits: 0 })}</td>
                <td className="px-3 py-1.5 text-right font-mono tabular-nums text-ink">{a.value.toLocaleString(undefined, { maximumFractionDigits: 0 })}</td>
              </tr>
            ))}
          </tbody>
        </table>
      )}

      <div className="flex flex-wrap gap-1.5 border-t border-border-soft px-3 py-2">
        {val && <span className="chip text-ink-muted">snapshot valued {val.basis_date}</span>}
        {val?.oldest_price_date && <span className={`chip ${stale ? "text-crit" : "text-ink-muted"}`}>oldest price {val.oldest_price_date}{val.stale_days != null ? ` · ${val.stale_days}d` : ""}</span>}
        {val?.synthetic_price_symbols?.length ? <span className="chip text-warn">synthetic price: {val.synthetic_price_symbols.join(", ")}</span> : null}
        {val?.declared_price_symbols?.length ? <span className="chip text-ink-muted">stated valuation: {val.declared_price_symbols.join(", ")}</span> : null}
        {val?.unpriced_symbols?.length ? <span className="chip text-warn">no price: {val.unpriced_symbols.join(", ")}</span> : null}
        {result.credit_of && <span className="chip text-ink-muted">offsets {result.credit_of}</span>}
        {result.checks.map((k) => (
          <span key={k.code} className={`chip ${k.severity === "block" ? "text-crit" : k.severity === "warn" ? "text-warn" : "text-ink-muted"}`} title={k.detail}>{label(k.code)}</span>
        ))}
        {result.overrides.map((o) => <span key={o.code} className="chip text-warn">accepted by {o.accepted_by}: {label(o.code)}</span>)}
      </div>
    </div>
  );
}
