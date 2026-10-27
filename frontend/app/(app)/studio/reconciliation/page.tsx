"use client";
import { useState } from "react";
import { Upload } from "lucide-react";
import PageHeader from "@/components/PageHeader";
import { Card, Empty, Spinner } from "@/components/ui";
import { api } from "@/lib/api";
import { useApi } from "@/lib/hooks";
import {
  BREAK_STATUS_TONE, CAUSE_TEXT, KIND_TEXT, STATE_TEXT, STEP_TEXT, figure,
} from "@/app/workspace/components/artifacts/ReconciliationCard";

const NEEDS_NOTE = ["dismissed", "resolved"];
const ACTION_LABEL: Record<string, string> = { explained: "Explained", escalated: "Escalate", dismissed: "Set aside", resolved: "Fixed" };
const ALLOWED: Record<string, string[]> = {
  open: ["explained", "escalated", "dismissed", "resolved"],
  explained: ["escalated", "dismissed", "resolved"],
  escalated: ["dismissed", "resolved"],
};

function parsePositions(text: string): { symbol: string; quantity: number }[] {
  const rows: { symbol: string; quantity: number }[] = [];
  for (const line of text.split(/\r?\n/)) {
    const raw = line.trim();
    if (!raw) continue;
    const parts = raw.split(/[\s,;\t]+/).filter(Boolean);
    const qty = Number(parts[1]);
    if (parts.length !== 2 || !parts[0] || !Number.isFinite(qty)) throw new Error(`"${raw}" is not a symbol and a quantity.`);
    rows.push({ symbol: parts[0], quantity: qty });
  }
  return rows;
}

export default function Reconciliation() {
  const { data, loading, refetch } = useApi<any>("/api/reconciliation");
  const [msg, setMsg] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  const [importing, setImporting] = useState(false);
  const [confirmImport, setConfirmImport] = useState(false);
  const [form, setForm] = useState({ account_id: "", as_of: "", source_document: "", cash_balance: "", positions: "" });

  const [acting, setActing] = useState<{ id: string; action: string; note: string } | null>(null);

  if (loading || !data) return <Spinner />;
  const s = data.summary;
  const canAct: boolean = data.can_act;

  async function doImport() {
    setBusy(true);
    setMsg(null);
    try {
      const positions = parsePositions(form.positions);
      const r = await api<any>("/api/reconciliation/statements", {
        method: "POST",
        body: {
          account_id: form.account_id, as_of: form.as_of, positions, source_document: form.source_document,
          cash_balance: form.cash_balance === "" ? null : Number(form.cash_balance),
        },
      });
      const b = r.breaks;
      setMsg(`${r.account} as at ${r.as_of}: ${r.positions_matched} of ${r.positions_compared} position(s) matched, cash ${r.cash.replace(/_/g, " ")}. ${b.opened} opened, ${b.refreshed} refreshed, ${b.cleared} cleared.`);
      setConfirmImport(false);
      setImporting(false);
      setForm({ account_id: "", as_of: "", source_document: "", cash_balance: "", positions: "" });
      await refetch();
    } catch (e: any) {
      setMsg(e?.message ?? "That did not work.");
      setConfirmImport(false);
    } finally {
      setBusy(false);
    }
  }

  async function decide() {
    if (!acting) return;
    setBusy(true);
    setMsg(null);
    try {
      const r = await api<any>(`/api/reconciliation/breaks/${acting.id}/resolve`, {
        method: "POST", body: { action: acting.action, note: acting.note || null },
      });
      setMsg(`Break ${r.was} → ${r.status}.`);
      setActing(null);
      await refetch();
    } catch (e: any) {
      setMsg(e?.message ?? "That did not work.");
      setActing(null);
    } finally {
      setBusy(false);
    }
  }

  const formReady = form.account_id && form.as_of && form.source_document.trim().length >= 3 && form.positions.trim();

  return (
    <div>
      <PageHeader title="Reconciliation" sub="Whether the book agrees with what each custodian says the account holds." />
      {msg && <div className="mb-4 font-mono text-xs text-ink-muted" role="status" data-testid="recon-status">{msg}</div>}

      <div className="mb-4 grid grid-cols-2 gap-3 lg:grid-cols-5" data-testid="recon-summary">
        {[
          ["Reconciled", s.reconciled, "text-ok"],
          ["With breaks", s.breaks, s.breaks ? "text-crit" : "text-ink-muted"],
          ["Out of date", s.stale_statement, s.stale_statement ? "text-warn" : "text-ink-muted"],
          ["No statement", s.no_statement, "text-ink-muted"],
          ["Open breaks", s.open_breaks, s.open_breaks ? "text-crit" : "text-ink-muted"],
        ].map(([label, v, tone]: any) => (
          <Card key={label}>
            <div className="tile-label">{label}</div>
            <div className={`font-mono text-2xl tabular-nums ${tone}`}>{v}</div>
          </Card>
        ))}
      </div>

      <Card className="mb-4">
        <div className="flex flex-wrap items-center justify-between gap-3">
          <div className="flex flex-wrap gap-1.5" data-testid="recon-causes">
            <span className="chip text-ink-muted">statements count for {s.fresh_within_days} days</span>
            {Object.entries(s.by_cause as Record<string, number>).map(([c, n]) => (
              <span key={c} className="chip text-ink-muted">{CAUSE_TEXT[c] ?? c} · {n}</span>
            ))}
          </div>
          {!importing && (
            <button className="btn-outline text-xs" data-testid="recon-import" disabled={!canAct}
              title={!canAct ? "Only operations or the portfolio team may import a statement" : undefined}
              onClick={() => setImporting(true)}>
              <Upload size={13} className="mr-1.5 inline" /> Import a statement
            </button>
          )}
        </div>

        {importing && (
          <div className="mt-4 grid gap-3 lg:grid-cols-2" data-testid="recon-form">
            <label className="block">
              <div className="tile-label">Account</div>
              <select className="input w-full" value={form.account_id} aria-label="Account"
                onChange={(e) => setForm({ ...form, account_id: e.target.value })}>
                <option value="">Choose an account</option>
                {data.accounts.map((a: any) => <option key={a.account_id} value={a.account_id}>{a.account}{a.custodian ? ` · ${a.custodian}` : ""}</option>)}
              </select>
            </label>
            <label className="block">
              <div className="tile-label">Statement date</div>
              <input type="date" className="input w-full" value={form.as_of} aria-label="Statement date"
                onChange={(e) => setForm({ ...form, as_of: e.target.value })} />
            </label>
            <label className="block lg:col-span-2">
              <div className="tile-label">Source document</div>
              <input className="input w-full" value={form.source_document} aria-label="Source document"
                placeholder="Custodian statement, 27 Oct 2026, Custody Co"
                onChange={(e) => setForm({ ...form, source_document: e.target.value })} />
            </label>
            <label className="block">
              <div className="tile-label">Positions — one per line, symbol then quantity</div>
              <textarea className="input h-32 w-full font-mono text-xs" value={form.positions} aria-label="Positions"
                placeholder={"AAPL 120\nMSFT 80"}
                onChange={(e) => setForm({ ...form, positions: e.target.value })} />
            </label>
            <label className="block">
              <div className="tile-label">Cash on the statement (leave empty if it shows none)</div>
              <input className="input w-full font-mono" inputMode="decimal" value={form.cash_balance} aria-label="Cash"
                onChange={(e) => setForm({ ...form, cash_balance: e.target.value })} />
            </label>
            <div className="flex flex-wrap items-center gap-2 lg:col-span-2">
              {!confirmImport ? (
                <>
                  <button className="btn-primary text-xs" disabled={!formReady} data-testid="recon-import-go"
                    onClick={() => setConfirmImport(true)}>Compare with the book</button>
                  <button className="btn-outline text-xs" onClick={() => { setImporting(false); setConfirmImport(false); }}>Cancel</button>
                </>
              ) : (
                <div className="flex items-center gap-2" data-testid="recon-import-confirm">
                  <span className="chip text-ink-muted">records the statement · opens breaks · changes nothing in the book</span>
                  <button className="btn-primary text-xs" disabled={busy} onClick={doImport}>{busy ? "Comparing…" : "Confirm"}</button>
                  <button className="btn-outline text-xs" disabled={busy} onClick={() => setConfirmImport(false)}>Not now</button>
                </div>
              )}
            </div>
          </div>
        )}
      </Card>

      <h2 className="mb-2 text-sm font-medium text-ink">Breaks</h2>
      {data.breaks.length === 0 ? <Empty>No open breaks.</Empty> : (
        <Card className="mb-4 overflow-hidden p-0">
          <div className="overflow-x-auto">
            <table className="w-full text-sm" data-testid="recon-breaks">
              <thead>
                <tr className="text-left text-[10px] uppercase tracking-wide text-ink-muted">
                  <th className="px-4 py-2 font-medium">Break</th>
                  <th className="px-4 py-2 text-right font-medium">Book · custodian · gap</th>
                  <th className="px-4 py-2 font-medium">Likely cause</th>
                  <th className="px-4 py-2 font-medium">Seen</th>
                  <th className="px-4 py-2" />
                </tr>
              </thead>
              <tbody className="divide-y divide-border-soft">
                {data.breaks.map((b: any) => {
                  const f = figure(b);
                  const mine = acting?.id === b.id;
                  return (
                    <tr key={b.id} data-testid="recon-break-row">
                      <td className="px-4 py-2">
                        <div className="text-ink">{b.symbol ?? "Cash"} <span className="text-xs text-ink-muted">{KIND_TEXT[b.kind]}</span></div>
                        <div className="text-xs text-ink-muted">{b.account}{b.custodian ? ` · ${b.custodian}` : ""}</div>
                      </td>
                      <td className="px-4 py-2 text-right font-mono tabular-nums text-ink-muted">
                        {f.book} · {f.custodian} · <span className={b.status === "explained" ? "text-ink-muted" : "text-crit"}>{f.diff}</span>
                      </td>
                      <td className="px-4 py-2">
                        <span className="chip text-ink-muted">{CAUSE_TEXT[b.cause] ?? b.cause}{b.confidence > 0 ? ` · ${(b.confidence * 100).toFixed(0)}%` : ""}</span>
                        <span className="chip ml-1 text-ink-muted">{STEP_TEXT[b.next_step] ?? b.next_step}</span>
                        <span className={`chip ml-1 ${BREAK_STATUS_TONE[b.status]}`}>{b.status}</span>
                      </td>
                      <td className="px-4 py-2 font-mono text-xs tabular-nums text-ink-muted">{b.times_seen}× since {b.first_seen}</td>
                      <td className="px-4 py-2 text-right">
                        {canAct && !mine && (
                          <div className="flex flex-wrap justify-end gap-1">
                            {(ALLOWED[b.status] ?? []).map((a) => (
                              <button key={a} className="btn-outline text-xs" data-testid={`recon-${a}`}
                                onClick={() => setActing({ id: b.id, action: a, note: "" })}>{ACTION_LABEL[a]}</button>
                            ))}
                          </div>
                        )}
                        {mine && acting && (
                          <div className="flex flex-wrap items-center justify-end gap-2" data-testid="recon-decide">
                            {NEEDS_NOTE.includes(acting.action) && (
                              <input className="input w-56 py-1 text-xs" aria-label="Reason" placeholder="Reason, kept on the record"
                                value={acting.note} onChange={(e) => setActing({ ...acting, note: e.target.value })} />
                            )}
                            <span className="chip text-ink-muted">{ACTION_LABEL[acting.action]} · holdings and cash unchanged</span>
                            <button className="btn-primary text-xs" disabled={busy || (NEEDS_NOTE.includes(acting.action) && !acting.note.trim())}
                              onClick={decide}>{busy ? "…" : "Confirm"}</button>
                            <button className="btn-outline text-xs" disabled={busy} onClick={() => setActing(null)}>Not now</button>
                          </div>
                        )}
                      </td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
          </div>
        </Card>
      )}

      <h2 className="mb-2 text-sm font-medium text-ink">Accounts</h2>
      <Card className="overflow-hidden p-0">
        <div className="overflow-x-auto">
          <table className="w-full text-sm" data-testid="recon-accounts">
            <thead>
              <tr className="text-left text-[10px] uppercase tracking-wide text-ink-muted">
                <th className="px-4 py-2 font-medium">Account</th>
                <th className="px-4 py-2 font-medium">State</th>
                <th className="px-4 py-2 font-medium">Statement</th>
                <th className="px-4 py-2 text-right font-medium">Open · explained</th>
              </tr>
            </thead>
            <tbody className="divide-y divide-border-soft">
              {data.accounts.map((a: any) => {
                const st = STATE_TEXT[a.state];
                return (
                  <tr key={a.account_id}>
                    <td className="px-4 py-2">
                      <div className="text-ink">{a.account}</div>
                      <div className="text-xs text-ink-muted">{a.custodian ?? "—"}{a.account_number ? ` · ${a.account_number}` : ""} · {a.currency}</div>
                    </td>
                    <td className="px-4 py-2">
                      <span className={`chip ${st.tone}`}>{st.text}</span>
                      {a.not_comparable.length > 0 && <span className="chip ml-1 text-warn">{a.not_comparable.join(", ")} not comparable</span>}
                      {a.cash && a.cash !== "compared" && <span className="chip ml-1 text-ink-muted">cash {a.cash.replace(/_/g, " ")}</span>}
                    </td>
                    <td className="px-4 py-2 font-mono text-xs tabular-nums text-ink-muted">
                      {a.statement_as_of ? `${a.statement_as_of} · ${a.statement_age_days}d` : "—"}
                      {a.statement_source && <div className="max-w-xs truncate font-sans text-ink-muted" title={a.statement_source}>{a.statement_source}</div>}
                    </td>
                    <td className="px-4 py-2 text-right font-mono tabular-nums text-ink-muted">{a.open_breaks} · {a.explained_breaks}</td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        </div>
      </Card>
    </div>
  );
}
