import { money } from "@/lib/format";

const STAGE_COLOR: Record<string, string> = {
  lead: "bg-ink-muted", qualified: "bg-info", proposal: "bg-warn", won: "bg-ok", lost: "bg-crit",
};
const STAGE_LABEL: Record<string, string> = { lead: "Lead", qualified: "Qualified", proposal: "Proposal", won: "Won", lost: "Lost" };

interface Onboarding { case_id: string; state: string; label?: string; step?: number; steps?: number; owner?: string; overdue?: boolean }
interface Deal {
  id: string; title: string; stage: string; contact: string | null; owner: string | null;
  estimated_aum: number | null; probability_pct: number | null; days_since_touch: number | null;
  quiet?: { days: number; limit: number; over: number } | null; next_step?: string;
  onboarding?: Onboarding | null; client?: boolean; lost_reason?: string | null;
}
export interface FunnelPayload {
  scope: "mine" | "firm"; scope_label: string; currency?: string; contacts: number;
  stages: { stage: string; label: string; count: number; estimated_aum: number; no_estimate: number; quiet: number }[];
  open: { count: number; estimated_aum: number; weighted_aum: number; weighted_from: number; no_probability: number; quiet: number };
  won: { count: number; in_onboarding: number; invested: number; no_case: number };
  lost: { count: number; reasons: Record<string, number> };
  last_year: { won: number; lost: number; win_rate_pct: number | null; median_days_to_won: number | null;
               median_days_won_to_invested: number | null; invested_measured: number };
  quiet: Deal[];
  opportunities: Deal[];
}

const Chip = ({ children, tone = "text-ink-muted", testid }: { children: React.ReactNode; tone?: string; testid?: string }) => (
  <span className={`chip ${tone} tabular-nums`} data-testid={testid}>{children}</span>
);

function Step({ label, count, sub, color, testid }: { label: string; count: number; sub?: React.ReactNode; color: string; testid: string }) {
  return (
    <div className="min-w-0 px-2 py-2" data-testid={testid}>
      <div className={`mb-1.5 h-1 rounded-full ${color}`} />
      <div className="text-[11px] uppercase tracking-wide text-ink-muted">{label}</div>
      <div className="font-mono text-base font-semibold tabular-nums text-ink">{count}</div>
      {sub && <div className="truncate text-[11px] text-ink-muted tabular-nums">{sub}</div>}
    </div>
  );
}

const days = (d: number | null | undefined) => (d === null || d === undefined ? "—" : `${Math.round(d)}d`);

/** The funnel from lead to invested: figures, chips and rows only. */
export function CrmPipelineCard({ result }: { result: FunnelPayload }) {
  const ccy = result.currency;
  const y = result.last_year;
  const won = result.opportunities.filter((o) => o.stage === "won");
  return (
    <div className="overflow-hidden rounded-lg border border-border bg-surface" data-testid="crm-funnel-card">
      <div className="flex flex-wrap items-center gap-2 border-b border-border-soft px-3 py-2">
        <div className="text-sm font-medium text-ink">Growth funnel</div>
        <Chip testid="crm-funnel-scope">{result.scope_label}</Chip>
        {result.open.estimated_aum > 0 && (
          <span className="ml-auto font-mono text-sm tabular-nums text-ink">{money(result.open.estimated_aum, ccy)} open</span>
        )}
      </div>
      <div className="grid grid-cols-3 divide-x divide-border-soft border-b border-border-soft cq-sm:grid-cols-6">
        {result.stages.map((s) => (
          <Step key={s.stage} label={s.label} count={s.count} color={STAGE_COLOR[s.stage]} testid={`crm-funnel-${s.stage}`}
            sub={s.estimated_aum ? money(s.estimated_aum, ccy) : s.count ? "no estimate" : undefined} />
        ))}
        <Step label="Onboarding" count={result.won.in_onboarding} color="bg-ok" testid="crm-funnel-onboarding" />
        <Step label="Invested" count={result.won.invested} color="bg-ok" testid="crm-funnel-invested" />
        <Step label="Lost" count={result.lost.count} color="bg-crit" testid="crm-funnel-lost" />
      </div>
      <div className="flex flex-wrap gap-1.5 border-b border-border-soft px-3 py-2">
        <Chip>12 months: {y.won} won · {y.lost} lost</Chip>
        {y.win_rate_pct !== null && <Chip tone="text-ok">{y.win_rate_pct}% won</Chip>}
        <Chip>lead to won {days(y.median_days_to_won)}</Chip>
        <Chip>won to invested {y.invested_measured ? days(y.median_days_won_to_invested) : "not yet measured"}</Chip>
        {result.open.weighted_from > 0 && <Chip>{money(result.open.weighted_aum, ccy)} weighted · {result.open.weighted_from} with a probability</Chip>}
        {result.open.no_probability > 0 && <Chip>{result.open.no_probability} with no probability given</Chip>}
        {result.won.no_case > 0 && <Chip tone="text-warn">{result.won.no_case} won with no onboarding case</Chip>}
      </div>
      {result.quiet.length > 0 && (
        <div className="border-b border-border-soft">
          <div className="px-3 pt-2 text-[11px] uppercase tracking-wide text-warn">Gone quiet · {result.quiet.length}</div>
          <table className="w-full text-sm">
            <tbody className="divide-y divide-border-soft">
              {result.quiet.slice(0, 6).map((o) => (
                <tr key={o.id} data-testid="crm-funnel-quiet-row">
                  <td className="px-3 py-1.5">
                    <div className="text-ink">{o.contact || o.title}</div>
                    <div className="text-xs text-ink-muted">{o.next_step}</div>
                  </td>
                  <td className="px-3 py-1.5 whitespace-nowrap">
                    <span className="inline-flex items-center gap-1.5 text-xs text-ink-muted">
                      <span className={`h-1.5 w-1.5 rounded-full ${STAGE_COLOR[o.stage]}`} />{STAGE_LABEL[o.stage]}
                    </span>
                  </td>
                  <td className="px-3 py-1.5 text-right font-mono text-xs tabular-nums text-warn whitespace-nowrap">
                    {days(o.quiet?.days)} / {o.quiet?.limit}d
                  </td>
                  <td className="hidden px-3 py-1.5 text-right text-xs text-ink-muted cq-sm:table-cell">{o.owner || "no owner"}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
      {won.length > 0 && (
        <table className="w-full text-sm">
          <tbody className="divide-y divide-border-soft">
            {won.slice(0, 6).map((o) => (
              <tr key={o.id} data-testid="crm-funnel-won-row">
                <td className="px-3 py-1.5 text-ink">{o.contact || o.title}</td>
                <td className="px-3 py-1.5 text-xs text-ink-muted">
                  {o.onboarding
                    ? o.onboarding.state === "invested" ? "Invested" : `${o.onboarding.label} · step ${o.onboarding.step} of ${o.onboarding.steps}`
                    : "No onboarding case"}
                </td>
                <td className="px-3 py-1.5 text-right text-xs whitespace-nowrap">
                  {o.onboarding?.overdue ? <span className="text-crit">past SLA · {o.onboarding.owner}</span>
                    : o.onboarding && o.onboarding.state !== "invested" ? <span className="text-ink-muted">{o.onboarding.owner}</span> : null}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      )}
    </div>
  );
}

export interface ProspectBriefPayload {
  currency?: string;
  contact: { id: string; name: string; type: string; source: string | null; email: string | null; phone: string | null;
             notes: string | null; owner: string | null; on_file_since: string | null; client: boolean };
  opportunities: { id: string; title: string; stage: string; stage_label: string; estimated_aum: number | null;
                   probability_pct: number | null; expected_close_date: string | null; days_open: number | null;
                   quiet: { days: number; limit: number } | null; next_step: string | null; lost_reason: string | null }[];
  activity: { type: string; at: string; detail: string; by: string | null }[];
  activity_count: number;
  booked: { at: string; detail: string; by: string | null }[];
  not_on_file: string[];
}

const when = (iso: string) => new Date(iso).toLocaleDateString(undefined, { day: "numeric", month: "short", year: "numeric" });
const whenTime = (iso: string) => new Date(iso).toLocaleString(undefined, { day: "numeric", month: "short", hour: "2-digit", minute: "2-digit" });

/** A prospect's file before a meeting: what was recorded, and what is not on file. */
export function ProspectBriefCard({ result }: { result: ProspectBriefPayload }) {
  const c = result.contact;
  return (
    <div className="overflow-hidden rounded-lg border border-border bg-surface" data-testid="prospect-brief-card">
      <div className="flex flex-wrap items-center gap-2 border-b border-border-soft px-3 py-2">
        <div className="text-sm font-medium text-ink">{c.name}</div>
        <Chip>{c.client ? "client" : c.type.replace(/_/g, " ")}</Chip>
        {c.source && <Chip>came via {c.source.replace(/_/g, " ")}</Chip>}
        {c.owner && <Chip>{c.owner}</Chip>}
        {c.on_file_since && <span className="ml-auto text-xs text-ink-muted">on file since {when(c.on_file_since)}</span>}
      </div>
      {c.notes && <div className="border-b border-border-soft px-3 py-2 text-xs text-ink-muted" data-testid="prospect-brief-notes">{c.notes}</div>}
      <div className="flex flex-wrap gap-1.5 border-b border-border-soft px-3 py-2">
        {result.booked.length
          ? <Chip tone="text-ok" testid="prospect-brief-booked">booked {whenTime(result.booked[0].at)}</Chip>
          : <Chip testid="prospect-brief-booked">no meeting booked</Chip>}
        <Chip>{result.activity_count} logged</Chip>
        {result.not_on_file.map((m) => <Chip key={m} tone="text-warn" testid="prospect-brief-missing">not on file: {m.toLowerCase()}</Chip>)}
      </div>
      {result.opportunities.length > 0 && (
        <table className="w-full border-b border-border-soft text-sm">
          <tbody className="divide-y divide-border-soft">
            {result.opportunities.map((o) => (
              <tr key={o.id}>
                <td className="px-3 py-1.5">
                  <div className="text-ink">{o.title}</div>
                  {o.next_step && <div className="text-xs text-ink-muted">{o.next_step}</div>}
                  {o.lost_reason && <div className="text-xs text-ink-muted">lost: {o.lost_reason}</div>}
                </td>
                <td className="px-3 py-1.5 whitespace-nowrap">
                  <span className="inline-flex items-center gap-1.5 text-xs text-ink-muted">
                    <span className={`h-1.5 w-1.5 rounded-full ${STAGE_COLOR[o.stage]}`} />{o.stage_label}
                  </span>
                </td>
                <td className="px-3 py-1.5 text-right font-mono text-xs tabular-nums whitespace-nowrap">
                  <div className="text-ink">{o.estimated_aum !== null ? money(o.estimated_aum, result.currency) : "no estimate"}</div>
                  <div className="text-ink-muted">{o.probability_pct !== null ? `${o.probability_pct}%` : "no probability"}</div>
                </td>
                <td className="px-3 py-1.5 text-right font-mono text-xs tabular-nums whitespace-nowrap">
                  {o.quiet ? <span className="text-warn">quiet {days(o.quiet.days)}</span>
                    : o.days_open !== null ? <span className="text-ink-muted">{days(o.days_open)} open</span> : null}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      )}
      {result.activity.length > 0 && (
        <ul className="divide-y divide-border-soft text-xs">
          {result.activity.slice(0, 6).map((a, i) => (
            <li key={i} className="flex gap-3 px-3 py-1.5" data-testid="prospect-brief-activity">
              <span className="w-20 shrink-0 tabular-nums text-ink-muted">{when(a.at)}</span>
              <span className="w-14 shrink-0 text-ink-muted">{a.type}</span>
              <span className="min-w-0 flex-1 truncate text-ink" title={a.detail}>{a.detail}</span>
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}
