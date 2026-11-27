"use client";

import { money } from "@/lib/format";
import { CounselChip } from "./GovernanceCard";

// India's tax planning (B-396): what is left after tax on stated returns, and the year's regime and harvest reviews.
// Figures, tables and chips only: the agent's message carries the sentences, and every source and caveat is a chip's title.

const rupees = (v: number) => money(v, "INR");
const pct = (v: number) => `${Number(v.toFixed(2))}%`;
const th = "px-2 py-1 text-right font-medium";
const td = "whitespace-nowrap px-2 py-1 text-right tabular-nums";

/** The stated returns: each vehicle's figure, its day and its document. */
function Rates({ rates, recorded }: { rates: any[]; recorded?: any }) {
  return (
    <div className="flex flex-wrap gap-1.5 px-3 py-2" data-testid="stated-returns">
      {rates.map((r) => (
        <span key={r.vehicle} className={`chip tabular-nums ${r.state !== "on_file" ? "text-warn" : recorded?.id === r.id ? "text-ok" : "text-ink-muted"}`}
          title={r.state === "on_file" ? `${r.name} · ${r.document} · ${r.recorded_by}` : "No stated return on file"} data-testid="stated-return">
          {r.label} {r.state === "on_file" ? `${pct(r.rate)} · ${r.stated_on}` : "not on file"}
        </span>
      ))}
    </div>
  );
}

function Taxpayer({ t }: { t: any }) {
  return (
    <div className="border-t border-border-soft" data-testid="after-tax-taxpayer">
      <div className="flex flex-wrap items-center gap-1.5 px-3 pt-2">
        <span className="mr-auto text-sm font-medium text-ink">{t.name}</span>
        {t.state === "assessed" ? (
          <>
            <span className="chip tabular-nums text-ink-muted">{Math.round(t.slab_rate * 100)}% slab</span>
            {t.surcharge_assessed
              ? <span className="chip tabular-nums text-ink-muted" title="From the income on file">{Math.round(t.surcharge_rate * 100)}% surcharge</span>
              : <span className="chip text-warn" title="Income is not on file">Surcharge left off</span>}
          </>
        ) : <span className="chip text-warn" title={t.needs} data-testid="after-tax-not-assessed">Not assessed</span>}
      </div>
      {t.state === "assessed" && (
        <div className="overflow-x-auto px-1 pb-2">
          <table className="w-full text-sm" data-testid="after-tax-held">
            <thead>
              <tr className="text-left text-[10px] uppercase tracking-wide text-ink-muted">
                <th className="px-2 py-1 font-medium">Held</th><th className={th}>Stated</th><th className={th}>After tax</th>
                <th className={th}>Tax takes</th><th className={th}>A year after tax</th>
              </tr>
            </thead>
            <tbody className="divide-y divide-border-soft">
              {t.held.map((h: any) => (
                <tr key={h.vehicle} title={h.state === "assessed" ? `${h.name} · ${h.basis} at ${pct(h.rate_borne * 100)}` : h.needs}>
                  <td className="px-2 py-1 text-ink-soft">{h.label}</td>
                  {h.state === "assessed" ? (
                    <>
                      <td className={`${td} text-ink-muted`}>{pct(h.rate)}</td>
                      <td className={`${td} text-ink`}>
                        {rupees(h.after_tax_value)}
                        {h.if_exemption_unused && (
                          <div className="text-[10px] text-ink-muted" title="With the year's whole long-term equity exemption unused in the year of sale">
                            {rupees(h.if_exemption_unused.after_tax_value)} with exemption
                          </div>
                        )}
                      </td>
                      <td className={`${td} text-ink-muted`}>{rupees(h.tax_takes)}</td>
                      <td className={`${td} text-ink`}>{pct(h.after_tax_return)}</td>
                    </>
                  ) : <td colSpan={4} className="px-2 py-1 text-right"><span className="chip text-warn">No stated return</span></td>}
                </tr>
              ))}
            </tbody>
          </table>
          {t.plans.length > 0 && (
            <table className="mt-1 w-full text-sm" data-testid="after-tax-plans">
              <thead>
                <tr className="text-left text-[10px] uppercase tracking-wide text-ink-muted">
                  <th className="px-2 py-1 font-medium">Same scheme</th><th className={th}>Growth, after tax</th><th className={th}>Income (IDCW), after tax</th>
                </tr>
              </thead>
              <tbody className="divide-y divide-border-soft">
                {t.plans.map((p: any) => (
                  <tr key={p.vehicle} title={p.name}>
                    <td className="px-2 py-1 text-ink-soft">{p.label}</td>
                    <td className={`${td} text-ink`} title={p.growth.basis}>{rupees(p.growth.after_tax_value)}</td>
                    <td className={`${td} text-ink`} title={p.income.basis}>{rupees(p.income.after_tax_value)}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          )}
        </div>
      )}
    </div>
  );
}

export function AfterTaxRows({ data }: { data: any }) {
  return (
    <div data-testid="after-tax">
      <div className="flex flex-wrap gap-1.5 px-3 pt-2">
        <span className="chip tabular-nums text-ink-muted" title={data.amount_assumed ? "Assumed for illustration" : "As asked"}>
          {rupees(data.amount)}{data.amount_assumed ? " assumed" : ""}
        </span>
        <span className="chip tabular-nums text-ink-muted" title={data.months_assumed ? "Assumed for illustration" : "As asked"}>
          {data.months} months{data.months_assumed ? " assumed" : ""}
        </span>
        <span className="chip text-warn" title={data.note}>Estimate</span>
      </div>
      <Rates rates={data.rates} recorded={data.recorded} />
      {data.taxpayers.map((t: any) => <Taxpayer key={t.id} t={t} />)}
      <div className="flex flex-wrap gap-1.5 border-t border-border-soft px-3 py-2">
        <span className="chip text-ink-muted" title={data.assumptions.join(" · ")}>{data.assumptions.length} assumptions</span>
        <span className="chip text-ink-muted" title={data.not_modelled.join(" · ")}>{data.not_modelled.length} things not modelled</span>
        <span className="chip text-warn" title={data.figures.map((f: any) => `${f.label} (${f.source})`).join(" | ")}>{data.figures.length} tax rules, assumed</span>
        {data.figures.some((f: any) => f.counsel) && <CounselChip counsel={data.figures.find((f: any) => f.counsel?.state === "not_reviewed")?.counsel} />}
      </div>
    </div>
  );
}

const share = (c: { done: number; of: number; share: number | null }) => (c.share == null ? "none to count" : `${Math.round(c.share * 100)}%`);
const REVIEW_TONE: Record<string, string> = { done: "text-ok", not_recorded: "text-warn", not_applicable: "text-ink-muted" };
const QUEUE_TONE: Record<string, string> = { to_raise: "text-warn", raised: "text-ink-muted", reviewed: "text-ok" };
const QUEUE_TEXT: Record<string, string> = { to_raise: "Task not raised", raised: "Task raised", reviewed: "Reviewed" };

export function TaxReviewRows({ data, full = false }: { data: any; full?: boolean }) {
  const c = data.completion;
  const queue: any[] = data.harvest.queue;
  const people: any[] = data.taxpayers;
  const shown = full || data.scope === "household" ? people : people.filter((t) => t.regime.state === "not_recorded" || t.harvest.state === "not_recorded");
  const listed = full ? shown : shown.slice(0, 8);
  const reviews: any[] = full || data.scope === "household" ? data.reviews : data.reviews.slice(0, 4);
  return (
    <div data-testid="tax-reviews">
      <div className="grid grid-cols-3 gap-x-4 px-3 py-2">
        {([["Regime reviews", c.regime], ["Harvest reviews", c.harvest], ["Both", c.both]] as [string, any][]).map(([label, v]) => (
          <div key={label} data-testid="tax-completion" title={data.note}>
            <div className="text-[10px] uppercase tracking-wide text-ink-muted">{label}</div>
            <div className="font-mono text-sm tabular-nums text-ink">{share(v)}</div>
            <div className="text-xs tabular-nums text-ink-muted">{v.done} of {v.of}</div>
          </div>
        ))}
      </div>
      {queue.length > 0 && (
        <div className="divide-y divide-border-soft border-t border-border-soft" data-testid="harvest-queue">
          <div className="flex flex-wrap items-center gap-1.5 px-3 py-1.5">
            <span className="mr-auto text-[10px] font-medium uppercase tracking-wide text-ink-muted">Year-end gain harvest · by {data.tax_year_end}</span>
            <span className="chip tabular-nums text-ink-muted" title={data.harvest.note}>Raised automatically from {data.harvest.scheduled_from}</span>
          </div>
          {queue.map((q) => (
            <div key={q.taxpayer_id} className="flex flex-wrap items-center gap-1.5 px-3 py-1.5" data-testid="harvest-candidate"
              title={q.lots.map((l: any) => `${l.symbol} ${l.quantity}`).join(", ")}>
              <span className="mr-auto text-sm text-ink">{q.name}<span className="ml-1.5 text-xs text-ink-muted">{q.household}</span></span>
              <span className="chip tabular-nums text-ink-muted" title="Exemption unused">{rupees(q.exemption_left)} left</span>
              <span className="chip tabular-nums text-ink-muted" title="Long-term gain at the front of the queue that would use it">Gain {rupees(q.gain)}</span>
              <span className={`chip ${q.adviser ? "text-ink-muted" : "text-warn"}`}>{q.adviser ?? "No adviser recorded"}</span>
              <span className={`chip ${QUEUE_TONE[q.state]}`} data-testid="harvest-state">
                {q.state === "raised" && q.task_status ? `Task ${q.task_status}` : QUEUE_TEXT[q.state]}
              </span>
            </div>
          ))}
        </div>
      )}
      {listed.length > 0 && (
        <div className="divide-y divide-border-soft border-t border-border-soft" data-testid="tax-review-taxpayers">
          {listed.map((t) => (
            <div key={t.taxpayer_id} className="flex flex-wrap items-center gap-1.5 px-3 py-1.5" data-testid="tax-review-taxpayer">
              <span className="mr-auto text-sm text-ink">{t.name}{data.scope === "firm" && <span className="ml-1.5 text-xs text-ink-muted">{t.household}</span>}</span>
              {(["regime", "harvest"] as const).map((k) => t[k].state !== "not_applicable" && (
                <span key={k} className={`chip tabular-nums ${REVIEW_TONE[t[k].state]}`} title={t[k].by}>
                  {k === "regime" ? "Regime" : "Harvest"} {t[k].state === "done" ? t[k].reviewed_on : "not recorded"}
                </span>
              ))}
            </div>
          ))}
          {shown.length > listed.length && <div className="px-3 py-1"><span className="chip text-ink-muted">+{shown.length - listed.length} more</span></div>}
        </div>
      )}
      {reviews.length > 0 && (
        <div className="divide-y divide-border-soft border-t border-border-soft" data-testid="tax-review-records">
          {reviews.map((r) => (
            <div key={r.id} className="flex flex-wrap items-center gap-1.5 px-3 py-1.5" data-testid="tax-review-record" title={`${r.covered} · ${r.document}`}>
              <span className="mr-auto text-sm text-ink-soft">{r.name} · {r.label}</span>
              {r.figures?.new_tax != null && <span className="chip tabular-nums text-ink-muted" title="As they stood">New {rupees(r.figures.new_tax)} · old {rupees(r.figures.old_tax)}</span>}
              {r.figures?.exemption_left != null && <span className="chip tabular-nums text-ink-muted" title="As it stood">{rupees(r.figures.exemption_left)} exemption left</span>}
              <span className="chip tabular-nums text-ink-muted">{r.reviewed_on} · {r.tax_year}</span>
              <span className="chip text-ink-muted">{r.recorded_by}</span>
            </div>
          ))}
        </div>
      )}
    </div>
  );
}

function Frame({ title, chips, children, testId }: { title: string; chips?: React.ReactNode; children: React.ReactNode; testId: string }) {
  return (
    <div className="overflow-hidden rounded-lg border border-border bg-surface" data-testid={testId}>
      <div className="flex flex-wrap items-center justify-between gap-2 border-b border-border-soft px-3 py-2">
        <div className="text-sm font-medium text-ink">{title}</div>
        <div className="flex flex-wrap gap-1.5">{chips}</div>
      </div>
      {children}
    </div>
  );
}

const notIndian = <div className="rounded-lg border border-border bg-surface px-3 py-2"><span className="chip text-ink-muted">Not an Indian firm</span></div>;

export function AfterTaxCard({ result }: { result: any }) {
  if (result.applies === false) return notIndian;
  return (
    <Frame title={result.household ? `After tax · ${result.household}` : "Stated returns"} testId="after-tax-card">
      <AfterTaxRows data={result} />
    </Frame>
  );
}

export function TaxReviewsCard({ result }: { result: any }) {
  if (result.applies === false) return notIndian;
  return (
    <Frame title={result.household ? `Tax reviews · ${result.household}` : "Tax reviews"} testId="tax-reviews-card"
      chips={<>
        <span className="chip tabular-nums text-ink-muted">Tax Year {result.tax_year}</span>
        <span className="chip tabular-nums text-ink-muted">{result.days_left} days left</span>
        {result.raised && <span className="chip tabular-nums text-ok" data-testid="harvest-raised">{result.raised.length} task(s) raised</span>}
      </>}>
      <TaxReviewRows data={result} />
    </Frame>
  );
}
