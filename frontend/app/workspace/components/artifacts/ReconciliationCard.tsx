export interface ReconBreak {
  id: string;
  account_id: string;
  account: string | null;
  household: string | null;
  custodian: string | null;
  kind: "quantity" | "missing_in_book" | "missing_at_custodian" | "unmapped_security" | "cash";
  symbol: string | null;
  name: string | null;
  currency: string | null;
  book: number;
  custodian_value: number;
  difference: number;
  status: "open" | "explained" | "escalated";
  cause: string;
  confidence: number;
  next_step: string;
  evidence: Record<string, any>;
  first_seen: string;
  last_seen: string;
  times_seen: number;
}

export interface ReconAccount {
  account_id: string;
  account: string;
  account_number: string | null;
  household: string | null;
  custodian: string | null;
  currency: string;
  state: "reconciled" | "breaks" | "stale_statement" | "no_statement";
  statement_as_of: string | null;
  statement_age_days: number | null;
  statement_source: string | null;
  open_breaks: number;
  explained_breaks: number;
  cash: string | null;
  not_comparable: string[];
}

interface Overview {
  accounts: ReconAccount[];
  breaks: ReconBreak[];
  summary: {
    accounts: number; reconciled: number; breaks: number; stale_statement: number; no_statement: number;
    open_breaks: number; explained_breaks: number; by_cause: Record<string, number>; fresh_within_days: number;
  };
}

interface Imported {
  account: string;
  household: string | null;
  as_of: string;
  positions_compared: number;
  positions_matched: number;
  cash: string;
  not_comparable: string[];
  breaks: { opened: number; refreshed: number; cleared: number; open: number };
}

interface Decided {
  status: string;
  was: string;
  resolved_by: string;
}

export const STATE_TEXT: Record<string, { text: string; tone: string }> = {
  reconciled: { text: "reconciled", tone: "text-ok" },
  breaks: { text: "breaks", tone: "text-crit" },
  stale_statement: { text: "statement out of date", tone: "text-warn" },
  no_statement: { text: "no statement", tone: "text-ink-muted" },
};

export const CAUSE_TEXT: Record<string, string> = {
  settlement_timing: "settlement timing",
  fee_timing: "fee timing",
  corporate_action_pending: "corporate action not posted",
  unmapped_security: "unmapped security",
  unexplained: "unexplained",
};

export const STEP_TEXT: Record<string, string> = {
  wait_for_next_statement: "clears on next statement",
  post_entitlement: "post the entitlement",
  map_instrument: "map the instrument",
  investigate: "investigate",
};

export const KIND_TEXT: Record<string, string> = {
  quantity: "quantity",
  missing_in_book: "not in book",
  missing_at_custodian: "not at custodian",
  unmapped_security: "unmapped",
  cash: "cash",
};

export const BREAK_STATUS_TONE: Record<string, string> = {
  open: "text-crit",
  escalated: "text-crit",
  explained: "text-ink-muted",
};

export function signed(v: number, d = 0) {
  const s = Math.abs(v).toLocaleString(undefined, { minimumFractionDigits: d, maximumFractionDigits: d });
  return v > 0 ? `+${s}` : v < 0 ? `−${s}` : s;
}

export function figure(b: Pick<ReconBreak, "kind" | "book" | "custodian_value" | "difference">) {
  const d = b.kind === "cash" ? 2 : 4;
  const f = (v: number) => v.toLocaleString(undefined, { minimumFractionDigits: 0, maximumFractionDigits: d });
  return { book: f(b.book), custodian: f(b.custodian_value), diff: signed(b.difference, b.kind === "cash" ? 2 : 4) };
}

const SHOWN = 10;

export function ReconciliationCard({ result }: { result: Overview | Imported | Decided }) {
  if ("breaks" in result && Array.isArray(result.breaks) && "accounts" in result) {
    const o = result as Overview;
    const s = o.summary;
    const rows = o.breaks.slice(0, SHOWN);
    return (
      <div className="overflow-hidden rounded-lg border border-border bg-surface" data-testid="reconciliation-card">
        <div className="flex flex-wrap items-center justify-between gap-2 border-b border-border-soft px-3 py-2">
          <div className="text-sm font-medium text-ink">Reconciliation</div>
          <div className="flex flex-wrap gap-1.5">
            <span className="chip text-ok">{s.reconciled} reconciled</span>
            {s.breaks > 0 && <span className="chip text-crit">{s.breaks} with breaks</span>}
            {s.stale_statement > 0 && <span className="chip text-warn">{s.stale_statement} out of date</span>}
            {s.no_statement > 0 && <span className="chip text-ink-muted">{s.no_statement} no statement</span>}
          </div>
        </div>
        {rows.length === 0 ? (
          <div className="px-3 py-3 font-mono text-xs text-ink-muted">no open breaks</div>
        ) : (
          <table className="w-full text-sm">
            <thead>
              <tr className="text-left text-[10px] uppercase tracking-wide text-ink-muted">
                <th className="px-3 py-1.5 font-medium">Break</th>
                <th className="px-3 py-1.5 text-right font-medium">Book · custodian</th>
                <th className="px-3 py-1.5 font-medium">Likely cause</th>
              </tr>
            </thead>
            <tbody className="divide-y divide-border-soft">
              {rows.map((b) => {
                const f = figure(b);
                return (
                  <tr key={b.id}>
                    <td className="px-3 py-1.5">
                      <div className="text-ink">{b.symbol ?? "Cash"} <span className="text-xs text-ink-muted">{KIND_TEXT[b.kind]}</span></div>
                      <div className="text-xs text-ink-muted">{b.household ? `${b.household} · ` : ""}{b.account}</div>
                    </td>
                    <td className="px-3 py-1.5 text-right font-mono tabular-nums text-ink-muted">
                      {f.book} · {f.custodian} <span className={b.status === "explained" ? "text-ink-muted" : "text-crit"}>{f.diff}</span>
                    </td>
                    <td className="px-3 py-1.5">
                      <span className="chip text-ink-muted">{CAUSE_TEXT[b.cause] ?? b.cause}{b.confidence > 0 ? ` · ${(b.confidence * 100).toFixed(0)}%` : ""}</span>
                      <span className="chip ml-1 text-ink-muted">{STEP_TEXT[b.next_step] ?? b.next_step}</span>
                      {b.status !== "open" && <span className={`chip ml-1 ${BREAK_STATUS_TONE[b.status]}`}>{b.status}</span>}
                    </td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        )}
        {o.breaks.length > SHOWN && (
          <div className="border-t border-border-soft px-3 py-2"><span className="chip text-ink-muted">+{o.breaks.length - SHOWN} more</span></div>
        )}
      </div>
    );
  }

  if ("positions_compared" in result) {
    const r = result as Imported;
    return (
      <div className="overflow-hidden rounded-lg border border-border bg-surface" data-testid="reconciliation-import-card">
        <div className="border-b border-border-soft px-3 py-2 text-sm font-medium text-ink">Statement compared · {r.household ? `${r.household} · ` : ""}{r.account} · {r.as_of}</div>
        <div className="flex flex-wrap gap-x-8 gap-y-2 px-3 py-3">
          <div>
            <div className="text-[10px] uppercase tracking-wide text-ink-muted">Positions matched</div>
            <div className="font-mono text-xl tabular-nums text-ink">{r.positions_matched} / {r.positions_compared}</div>
          </div>
          <div>
            <div className="text-[10px] uppercase tracking-wide text-ink-muted">Opened · cleared</div>
            <div className="font-mono text-xl tabular-nums text-ink">{r.breaks.opened} · {r.breaks.cleared}</div>
          </div>
          <div>
            <div className="text-[10px] uppercase tracking-wide text-ink-muted">Open on account</div>
            <div className={`font-mono text-xl tabular-nums ${r.breaks.open ? "text-crit" : "text-ok"}`}>{r.breaks.open}</div>
          </div>
        </div>
        <div className="flex flex-wrap gap-1.5 border-t border-border-soft px-3 py-2">
          <span className="chip text-ink-muted">cash {r.cash.replace(/_/g, " ")}</span>
          {r.not_comparable.map((s) => <span key={s} className="chip text-warn" title="Moved since the statement date in a way the book cannot be rolled back over">{s} not comparable</span>)}
        </div>
      </div>
    );
  }

  const r = result as Decided;
  return (
    <div className="rounded-lg border border-border bg-surface px-3 py-2" data-testid="reconciliation-decision-card">
      <span className="chip text-ink-muted">{r.was}</span>
      <span className="mx-1.5 text-ink-muted">→</span>
      <span className="chip text-ink">{r.status}</span>
      <span className="chip ml-2 text-ink-muted">{r.resolved_by}</span>
    </div>
  );
}
