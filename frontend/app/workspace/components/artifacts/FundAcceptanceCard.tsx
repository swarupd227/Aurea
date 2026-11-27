import { reviewDay } from "./ReviewsCard";

/** B-391: the fund-house acceptance list for US and Canada residents, every scheme read against both, and the clients the
 * rule reaches. Figures, tables and chips. */

const STANCE: Record<string, [string, string]> = {
  accepts: ["accepts", "text-ok"], conditions: ["on conditions", "text-warn"], refuses: ["does not accept", "text-crit"],
  not_assessed: ["not assessed", "text-ink-muted"],
};

export function Stance({ state, title }: { state: string; title?: string | null }) {
  const [text, tone] = STANCE[state] ?? [state, "text-ink-muted"];
  return <span className={`chip ${tone}`} title={title ?? undefined}>{text}</span>;
}

export function AcceptanceLines({ view }: { view: any }) {
  if (!view.lines.length) return <div className="px-3 py-2"><span className="chip text-ink-muted">no line on file</span></div>;
  return (
    <div className="overflow-x-auto">
      <table className="w-full text-xs" data-testid="acceptance-lines">
        <thead><tr className="text-left text-[10px] uppercase tracking-wide text-ink-muted">
          <th className="px-3 py-1.5 font-medium">Fund house or scheme</th><th className="px-3 py-1.5 font-medium">Residents of</th>
          <th className="px-3 py-1.5 font-medium">Answer</th><th className="px-3 py-1.5 font-medium">Dated</th>
        </tr></thead>
        <tbody className="divide-y divide-border-soft">
          {view.lines.map((l: any) => (
            <tr key={l.id}>
              <td className="px-3 py-1.5 text-ink" title={l.document}>{l.symbol ?? l.fund_house}</td>
              <td className="px-3 py-1.5 text-ink-muted">{l.country_label.replace("the ", "")}</td>
              <td className="px-3 py-1.5"><Stance state={l.stance} title={l.conditions} /></td>
              <td className="px-3 py-1.5 text-ink-muted">{reviewDay(l.stated_on)}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

export function AcceptanceSchemes({ view, limit }: { view: any; limit?: number }) {
  const rows: any[] = limit ? view.schemes.slice(0, limit) : view.schemes;
  return (
    <div className="overflow-x-auto">
      <table className="w-full text-xs" data-testid="acceptance-schemes">
        <thead><tr className="text-left text-[10px] uppercase tracking-wide text-ink-muted">
          <th className="px-3 py-1.5 font-medium">Scheme</th><th className="px-3 py-1.5 font-medium">US residents</th>
          <th className="px-3 py-1.5 font-medium">Canada residents</th>
        </tr></thead>
        <tbody className="divide-y divide-border-soft">
          {rows.map((s) => (
            <tr key={s.symbol}>
              <td className="px-3 py-1.5 text-ink" title={s.fund_house ?? undefined}>{s.symbol}{s.held ? <span className="text-ink-muted"> · held</span> : null}</td>
              {["US", "CA"].map((c) => <td key={c} className="px-3 py-1.5"><Stance state={s.countries[c].state} title={s.countries[c].conditions ?? s.countries[c].document} /></td>)}
            </tr>
          ))}
        </tbody>
      </table>
      {limit && view.schemes.length > limit && <div className="px-3 py-1.5"><span className="chip text-ink-muted">+{view.schemes.length - limit} more</span></div>}
    </div>
  );
}

export function AcceptanceClients({ view }: { view: any }) {
  return (
    <div className="flex flex-wrap gap-1.5 px-3 py-2" data-testid="acceptance-clients">
      {view.clients.length === 0 ? <span className="chip text-ink-muted">no holder recorded as US or Canada resident</span>
        : view.clients.map((c: any) => (
          <span key={c.mandate_id} className={`chip tabular-nums ${c.barred ? "text-crit" : "text-warn"}`} title={c.reached.map((r: any) => `${r.name}: ${r.why}`).join("; ")}>
            {c.mandate} · {c.barred} barred · {c.not_assessed} not assessed
          </span>
        ))}
    </div>
  );
}

export function FundAcceptanceCard({ result }: { result: any }) {
  if (!result?.applies) return null;
  return (
    <div className="overflow-hidden rounded-lg border border-border bg-surface" data-testid="fund-acceptance-card">
      <div className="flex flex-wrap items-center justify-between gap-2 border-b border-border-soft px-3 py-2">
        <span className="text-sm font-medium text-ink">Fund-house acceptance · US and Canada</span>
        <div className="flex flex-wrap gap-1.5">
          <span className="chip text-ink-muted">stops purchases only</span>
          <span className="chip text-ink-muted">rules assumed</span>
        </div>
      </div>
      <AcceptanceClients view={result} />
      <div className="border-t border-border-soft"><AcceptanceLines view={result} /></div>
      <div className="border-t border-border-soft"><AcceptanceSchemes view={result} limit={6} /></div>
    </div>
  );
}
