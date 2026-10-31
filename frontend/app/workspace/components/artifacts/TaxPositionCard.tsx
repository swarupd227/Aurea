import { money } from "@/lib/format";

export const BUCKET_TEXT: Record<string, string> = {
  st_equity: "Short-term equity",
  lt_equity: "Long-term equity",
  st_slab: "At slab",
  lt_other: "Long-term other",
};

const KIND_TEXT: Record<string, string> = { individual: "Individual", huf: "HUF", trust: "Trust" };
const REGIME_TEXT: Record<string, string> = { new: "New regime", old: "Old regime" };
const SHOWN = 4;

interface Opportunity {
  kind: "loss" | "exemption" | "turning_long";
  symbol: string;
  account: string;
  quantity: number;
  net_loss?: number;
  gain_en_route?: number;
  gain?: number;
  loss?: number;
  tax_saved?: number;
  carries_forward?: number;
  turns_on?: string;
  days_left?: number;
}

function action(o: Opportunity) {
  if (o.kind === "loss") return { text: "Harvest loss", amount: o.net_loss };
  if (o.kind === "exemption") return { text: "Use exemption", amount: o.gain };
  return { text: `Long-term in ${o.days_left}d`, amount: o.loss };
}

function Taxpayer({ t, full }: { t: any; full: boolean }) {
  const shown: Opportunity[] = full ? t.opportunities : t.opportunities.slice(0, SHOWN);
  const brought = t.brought_forward || {};
  const buckets = Object.entries(t.realised as Record<string, number>).filter(([, v]) => v);
  return (
    <div className="border-t border-border-soft first:border-t-0" data-testid="tax-taxpayer">
      <div className="flex flex-wrap items-center gap-1.5 px-3 pt-2.5">
        <span className="text-sm font-medium text-ink">{t.name}</span>
        <span className="chip text-ink-muted">{KIND_TEXT[t.kind] ?? t.kind}</span>
        <span className="chip text-ink-muted">{REGIME_TEXT[t.regime] ?? t.regime}</span>
        {t.slab_rate != null && <span className="chip text-ink-muted">{Math.round(t.slab_rate * 100)}% slab</span>}
        {t.surcharge_rate > 0 && <span className="chip text-ink-muted">{Math.round(t.surcharge_rate * 100)}% surcharge</span>}
        {(t.incomplete as string[]).map((m) => <span key={m} className="chip text-warn">{m.replace(/_/g, " ")} not on file</span>)}
      </div>
      <div className="grid grid-cols-2 gap-x-4 gap-y-1 px-3 py-2 sm:grid-cols-4">
        <Figure label="Estimated tax" value={money(t.estimated_tax)} />
        <Figure label="Exemption left" value={money(t.position.exempt_left)} />
        <Figure label="Taxable long-term" value={money(t.position.taxable_lt_equity)} />
        <Figure label="Carries forward" value={money(t.position.carry_forward.short_term + t.position.carry_forward.long_term)} />
      </div>
      {(buckets.length > 0 || brought.short_term || brought.long_term) && (
        <div className="flex flex-wrap gap-1.5 px-3 pb-2">
          {buckets.map(([k, v]) => <span key={k} className="chip text-ink-muted">{BUCKET_TEXT[k] ?? k} {money(v)}</span>)}
          {brought.short_term ? <span className="chip text-ink-muted">Brought forward short {money(brought.short_term)}</span> : null}
          {brought.long_term ? <span className="chip text-ink-muted">Brought forward long {money(brought.long_term)}</span> : null}
        </div>
      )}
      {shown.length > 0 && (
        <div className="overflow-x-auto">
          <table className="w-full text-sm">
            <thead>
              <tr className="text-left text-[10px] uppercase tracking-wide text-ink-muted">
                <th className="px-3 py-1 font-medium">Before 31 March</th>
                <th className="px-3 py-1 font-medium">Instrument</th>
                <th className="px-3 py-1 text-right font-medium">Qty</th>
                <th className="px-3 py-1 text-right font-medium">Amount</th>
                <th className="px-3 py-1 text-right font-medium">Saves</th>
              </tr>
            </thead>
            <tbody className="divide-y divide-border-soft">
              {shown.map((o, i) => {
                const a = action(o);
                return (
                  <tr key={`${o.kind}-${o.symbol}-${i}`} title={o.account}>
                    <td className="px-3 py-1 text-ink-soft">{a.text}</td>
                    <td className="px-3 py-1 font-mono text-xs text-ink">{o.symbol}</td>
                    <td className="px-3 py-1 text-right tabular-nums text-ink-muted">{o.quantity.toLocaleString()}</td>
                    <td className="px-3 py-1 text-right tabular-nums text-ink-soft">{money(a.amount)}</td>
                    <td className="px-3 py-1 text-right tabular-nums text-ok">{o.tax_saved ? money(o.tax_saved) : "—"}</td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        </div>
      )}
      <div className="flex flex-wrap gap-1.5 px-3 py-2">
        {t.opportunities.length > shown.length && <span className="chip text-ink-muted">+{t.opportunities.length - shown.length} more</span>}
        {t.return_needed_by && <span className="chip text-warn">File by {t.return_needed_by} to carry the loss</span>}
        {(t.grandfathered as any[]).map((g) => (
          <span key={`${g.symbol}-${g.account}`} className="chip text-warn" title={g.account}>{g.symbol} needs its 31 Jan 2018 value</span>
        ))}
        {t.demats.map((d: any) => <span key={d.id} className="chip text-ink-muted">{d.custodian}</span>)}
      </div>
    </div>
  );
}

function Figure({ label, value }: { label: string; value: string }) {
  return (
    <div>
      <div className="text-[10px] uppercase tracking-wide text-ink-muted">{label}</div>
      <div className="font-mono text-sm tabular-nums text-ink">{value}</div>
    </div>
  );
}

/** India's tax position: one block per taxpayer, because a loss never crosses between them. */
export function TaxPositionCard({ result, full = false }: { result: any; full?: boolean }) {
  const s = result.summary;
  return (
    <div className="overflow-hidden rounded-lg border border-border bg-surface" data-testid="tax-position-card">
      <div className="flex flex-wrap items-center justify-between gap-2 border-b border-border-soft px-3 py-2">
        <div className="text-sm font-medium text-ink">{result.household_name}</div>
        <div className="flex flex-wrap gap-1.5">
          <span className="chip text-ink-muted">Tax Year {result.tax_year}</span>
          <span className="chip text-ink-muted">{result.days_to_year_end} days left</span>
          <span className="chip text-ink-muted">{s.taxpayers} taxpayers</span>
          <span className="chip text-ink-muted">FIFO</span>
          <span className="chip text-warn">estimate</span>
        </div>
      </div>
      {result.taxpayers.map((t: any) => <Taxpayer key={t.id} t={t} full={full} />)}
      <div className="flex flex-wrap gap-1.5 border-t border-border-soft px-3 py-2">
        <span className="chip text-ink-muted">{result.law}</span>
        <span className="chip text-ink-muted" title={result.not_modelled.join(" · ")}>{result.not_modelled.length} things not modelled</span>
      </div>
    </div>
  );
}
