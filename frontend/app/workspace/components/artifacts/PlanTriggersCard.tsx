/** What gives a plan a reason to be looked at again, and how fast it was (aurea_core/plan_triggers.py, B-188): each life event
 * and equity grant with the day it was recorded, the day the plan was next touched and the days between, the household's
 * tier, and the firm's median. Tables and chips; it decides nothing. */
const said = (k: string) => k.replace(/_/g, " ");
const day = (iso: string | null) => (iso ? new Date(iso + "T00:00:00").toLocaleDateString(undefined, { day: "numeric", month: "short", year: "numeric" }) : "—");

function Head({ children, right = false }: { children?: React.ReactNode; right?: boolean }) {
  return <th className={`px-3 py-1.5 font-mono text-[11px] font-normal uppercase tracking-wider text-ink-muted ${right ? "text-right" : "text-left"}`}>{children}</th>;
}

export function TriggerChips({ result }: { result: any }) {
  const s = result.summary ?? {};
  const tier = result.tier;
  return (
    <>
      {"tier" in result && (tier
        ? <span className="chip text-ink" title={`${tier.reason} · ${tier.set_by}, ${day(tier.as_of)}`}>{tier.label}</span>
        : <span className="chip text-warn">tier not set</span>)}
      <span className="chip tabular-nums text-ink-muted">{s.triggers ?? 0} triggers</span>
      <span className="chip tabular-nums text-ink-muted">{s.answered ?? 0} answered</span>
      <span className={`chip tabular-nums ${s.open ? "text-warn" : "text-ink-muted"}`}>{s.open ?? 0} open</span>
      {s.median_days != null && <span className="chip tabular-nums text-ink">median {s.median_days} days</span>}
      {s.longest_open_days != null && <span className="chip tabular-nums text-warn">oldest open {s.longest_open_days} days</span>}
      <span className="chip text-ink-muted">no target supplied</span>
    </>
  );
}

export function TriggersTable({ result }: { result: any }) {
  const rows: any[] = result.triggers ?? [];
  if (!rows.length) return <div className="px-3 py-3"><span className="chip text-ink-muted">no triggers on file</span></div>;
  return (
    <div className="overflow-x-auto">
      <table className="w-full text-sm" data-testid="plan-triggers">
        <thead><tr><Head>Trigger</Head><Head right>Recorded</Head><Head right>Plan touched</Head><Head right>Days</Head></tr></thead>
        <tbody className="divide-y divide-border-soft">
          {rows.map((t) => (
            <tr key={t.id} data-testid="plan-trigger" title={t.document}>
              <td className="px-3 py-1.5">
                <span className="text-ink">{said(t.what)}</span>
                <span className="chip ml-2 text-ink-muted">{t.kind === "equity_grant" ? "equity grant" : said(t.kind)}</span>
                {t.person && <span className="ml-2 text-xs text-ink-muted">{t.person}</span>}
              </td>
              <td className="whitespace-nowrap px-3 py-1.5 text-right font-mono text-xs tabular-nums text-ink-muted">{day(t.recorded_on)}</td>
              <td className="whitespace-nowrap px-3 py-1.5 text-right font-mono text-xs tabular-nums text-ink-muted">
                {t.answered_on ? <>{day(t.answered_on)} <span className="chip ml-1 text-ink-muted">{t.answered_by}</span></> : <span className="chip text-warn">open</span>}
              </td>
              <td className={`px-3 py-1.5 text-right font-mono text-xs tabular-nums ${t.answered_on ? "text-ink" : "text-warn"}`}>{t.answered_on ? t.days_to_answer : t.open_days}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

export function PlanTriggersCard({ result }: { result: any }) {
  if (Array.isArray(result.households)) {
    return (
      <div className="overflow-hidden rounded-lg border border-border bg-surface" data-testid="plan-triggers-card">
        <div className="flex flex-wrap items-center gap-2 border-b border-border-soft px-3 py-2">
          <div className="text-sm font-medium text-ink">Plan triggers · the firm</div>
          <TriggerChips result={result} />
          {result.summary?.tier_not_set > 0 && <span className="chip tabular-nums text-warn">{result.summary.tier_not_set} with no tier</span>}
        </div>
        <div className="overflow-x-auto">
          <table className="w-full text-sm">
            <thead><tr><Head>Household</Head><Head>Tier</Head><Head right>Open</Head><Head right>Oldest open</Head><Head right>Median</Head></tr></thead>
            <tbody className="divide-y divide-border-soft">
              {result.households.map((h: any) => (
                <tr key={h.household_id}>
                  <td className="px-3 py-1.5 text-ink">{h.household}</td>
                  <td className="px-3 py-1.5">{h.tier ? <span className="chip text-ink-muted">{h.tier.label}</span> : <span className="chip text-warn">not set</span>}</td>
                  <td className="px-3 py-1.5 text-right font-mono text-xs tabular-nums text-ink">{h.open}</td>
                  <td className="px-3 py-1.5 text-right font-mono text-xs tabular-nums text-ink-muted">{h.longest_open_days ?? "—"}</td>
                  <td className="px-3 py-1.5 text-right font-mono text-xs tabular-nums text-ink-muted">{h.median_days ?? "—"}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </div>
    );
  }
  return (
    <div className="overflow-hidden rounded-lg border border-border bg-surface" data-testid="plan-triggers-card">
      <div className="flex flex-wrap items-center gap-2 border-b border-border-soft px-3 py-2">
        <div className="text-sm font-medium text-ink">Plan triggers · {result.household}</div>
        <TriggerChips result={result} />
      </div>
      <TriggersTable result={result} />
    </div>
  );
}
