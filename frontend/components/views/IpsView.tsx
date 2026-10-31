"use client";

import { Fragment, useState } from "react";
import { Card, Empty, ErrorState, SkeletonList } from "@/components/ui";
import { api } from "@/lib/api";
import { useApi } from "@/lib/hooks";
import { withRef } from "@/lib/refs";
import { reviewDay } from "@/app/workspace/components/artifacts/ReviewsCard";
import {
  IpsFindings, IpsStatusChip, measuredText, share, type IpsMandate,
} from "@/app/workspace/components/artifacts/IpsCard";
import { ViewLink, useViewNav } from "./ViewNav";
import { ViewHeader } from "./ViewHeader";

interface Panel {
  m: IpsMandate;
  accepted_on: string;
  document: string;
  targets: Record<string, string>;
  band: string;
  max_position: string;
  min_cash: string;
  restricted: string;
  reason: string;
}

const percent = (v: number | null | undefined) => (v == null ? "" : String(Math.round(v * 10000) / 100));
const fraction = (v: string) => (v.trim() === "" ? null : Number(v) / 100);
const given = (targets: Record<string, string>) => Object.entries(targets).filter(([, v]) => Number(v) > 0);

/** The whole record in words, so the conversation's Confirm card has every term to name. */
function spoken(p: Panel): string {
  return [
    `Record the IPS for ${p.m.mandate}, accepted on ${p.accepted_on}, from the document "${p.document.trim()}".`,
    `Targets: ${given(p.targets).map(([c, v]) => `${c} ${v}`).join(", ")}. Band ${p.band}%.`,
    p.max_position.trim() ? `Position limit ${p.max_position}%.` : "No position limit.",
    p.min_cash.trim() ? `Cash floor ${p.min_cash}%.` : "No cash floor.",
    p.restricted.trim() ? `Restricted: ${p.restricted.trim()}.` : "Nothing restricted.",
    p.reason.trim() ? `Reason: ${p.reason.trim()}.` : "",
  ].filter(Boolean).join(" ");
}

/** Each mandate against the IPS its client accepted. In the work pane, recording one is the
 * conversation's tool behind its Confirm card; on a page the form acts directly. */
export function IpsView() {
  const nav = useViewNav();
  const { data, loading, error, refetch } = useApi<any>("/api/ips");
  const [msg, setMsg] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [panel, setPanel] = useState<Panel | null>(null);

  if (error) return <div className="view-container"><Card><ErrorState what="IPS monitoring" message={error} onRetry={refetch} /></Card></div>;
  if (loading || !data) return <div className="view-container"><SkeletonList count={4} /></div>;
  const s = data.summary;
  const rows: IpsMandate[] = data.mandates;
  const classes: string[] = data.classes;
  const canRecord: boolean = data.can_record;

  const total = panel ? Object.values(panel.targets).reduce((sum, v) => sum + (Number(v) || 0), 0) : 0;
  const ready = !!panel && !!panel.accepted_on && panel.document.trim().length >= 3 && Math.abs(total - 100) < 0.1
    && !!panel.band.trim() && (!panel.m.ips || panel.reason.trim().length >= 3);

  const toRecord = (m: IpsMandate): Panel => ({
    m, accepted_on: data.as_of, document: "", reason: "",
    targets: Object.fromEntries(classes.map((c) => [c, percent(m.targets[c])])),
    band: percent(m.drift_band), max_position: percent(m.ips?.max_position), min_cash: percent(m.ips?.min_cash),
    restricted: (m.ips?.restricted ?? []).join(", "),
  });

  async function send() {
    if (!panel) return;
    if (nav.say) {
      nav.say(withRef(spoken(panel), "mandate", panel.m.mandate_id));
      setPanel(null);
      return;
    }
    setBusy(true);
    setMsg(null);
    try {
      const r = await api<any>(`/api/ips/mandates/${panel.m.mandate_id}`, {
        method: "POST",
        body: {
          accepted_on: panel.accepted_on, document: panel.document, reason: panel.reason || null,
          targets: Object.fromEntries(given(panel.targets).map(([c, v]) => [c, Number(v) / 100])),
          drift_band: fraction(panel.band), max_position: fraction(panel.max_position), min_cash: fraction(panel.min_cash),
          restricted: panel.restricted.split(",").map((x) => x.trim()).filter(Boolean),
        },
      });
      setMsg(`${r.mandate}: IPS v${r.version}, ${r.status.replace(/_/g, " ")}`);
      setPanel(null);
      await refetch();
    } catch (e: any) {
      setMsg(e?.message ?? "That did not work.");
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="view-container">
      <ViewHeader title="IPS monitoring" />
      {msg && <div className="mb-4 font-mono text-xs text-ink-muted" role="status" data-testid="ips-page-status">{msg}</div>}

      <div className="mb-4 grid grid-cols-2 gap-3 cq-md:grid-cols-4" data-testid="ips-summary">
        {[
          ["In breach", s.breach, s.breach ? "text-crit" : "text-ink-muted"],
          ["Exception accepted", s.excepted, s.excepted ? "text-warn" : "text-ink-muted"],
          ["Within", s.within, "text-ok"],
          ["No IPS on file", s.no_ips, s.no_ips ? "text-warn" : "text-ink-muted"],
        ].map(([label, v, tone]: any) => (
          <Card key={label}>
            <div className="tile-label">{label}</div>
            <div className={`font-mono text-2xl tabular-nums ${tone}`}>{v}</div>
          </Card>
        ))}
      </div>

      <Card className="mb-4">
        <div className="flex flex-wrap gap-1.5" data-testid="ips-basis">
          <span className="chip text-ink-muted">the IPS outranks the model</span>
          <span className="chip text-ink-muted">no IPS on file does not mean no IPS</span>
          <span className="chip text-ink-muted">an exception lasts {data.exception_days} days</span>
          {s.outside_model_band > 0 && <span className="chip text-warn">{s.outside_model_band} with no IPS, outside the model's band</span>}
          {s.not_assessed > 0 && <span className="chip text-ink-muted">{s.not_assessed} not assessed</span>}
        </div>
      </Card>

      {rows.length === 0 ? <Empty>No mandates.</Empty> : (
        <Card className="overflow-hidden p-0">
          <div className="overflow-x-auto">
            <table className="w-full text-sm" data-testid="ips-rows">
              <thead>
                <tr className="text-left text-[10px] uppercase tracking-wide text-ink-muted">
                  <th className="px-4 py-2 font-medium">Mandate</th>
                  <th className="px-4 py-2 font-medium">Measured against</th>
                  <th className="px-4 py-2 font-medium">Accepted</th>
                  <th className="px-4 py-2 text-right font-medium">Band</th>
                  <th className="px-4 py-2 font-medium">Outside</th>
                  <th className="px-4 py-2 font-medium">Status</th>
                  <th className="px-4 py-2 text-right font-medium">Act</th>
                </tr>
              </thead>
              <tbody className="divide-y divide-border-soft">
                {rows.map((m) => {
                  const open = panel?.m.mandate_id === m.mandate_id ? panel : null;
                  return (
                    <Fragment key={m.mandate_id}>
                      <tr>
                        <td className="px-4 py-2">
                          <div className="text-ink">{m.mandate}</div>
                          {m.household_id
                            ? <ViewLink href={`/studio/clients/${m.household_id}`} className="text-xs text-ink-muted hover:underline" data-testid="ips-client">{m.household}</ViewLink>
                            : <div className="text-xs text-ink-muted">—</div>}
                        </td>
                        <td className="px-4 py-2 font-mono text-xs text-ink-muted" title={m.ips?.document}>{measuredText(m)}</td>
                        <td className="px-4 py-2 font-mono text-xs text-ink-muted">{reviewDay(m.ips?.accepted_on)}</td>
                        <td className="px-4 py-2 text-right font-mono text-xs tabular-nums text-ink-muted">{share(m.drift_band)}</td>
                        <td className="px-4 py-2">
                          <IpsFindings row={m} />
                          {m.model_drift?.outside && <span className="chip text-warn">model drift {share(m.model_drift.max_drift)}</span>}
                        </td>
                        <td className="px-4 py-2">
                          <div className="flex flex-wrap gap-1.5">
                            <IpsStatusChip status={m.status} />
                            {m.price_source === "synthetic" && <span className="chip text-warn">synthetic price</span>}
                          </div>
                        </td>
                        <td className="px-4 py-2 text-right">
                          {!open && (
                            <button className="btn-outline text-xs" data-testid="ips-record" disabled={!canRecord || busy}
                              title={!canRecord ? "Only an adviser records an IPS" : undefined}
                              onClick={() => { setMsg(null); setPanel(toRecord(m)); }}>{m.ips ? "New version" : "Record"}</button>
                          )}
                        </td>
                      </tr>
                      {open && (
                        <tr>
                          <td colSpan={7} className="bg-border-soft/30 px-4 py-3">
                            <div className="space-y-2" data-testid="ips-confirm">
                              <div className="flex flex-wrap items-center gap-2">
                                <input type="date" className="input text-xs" aria-label="Accepted on" value={open.accepted_on} max={data.as_of}
                                  onChange={(e) => setPanel({ ...open, accepted_on: e.target.value })} />
                                <input className="input min-w-48 flex-1 text-xs" aria-label="Document" placeholder="Document" value={open.document}
                                  onChange={(e) => setPanel({ ...open, document: e.target.value })} />
                              </div>
                              <div className="flex flex-wrap items-center gap-2" data-testid="ips-targets">
                                {classes.map((c) => (
                                  <label key={c} className="chip flex items-center gap-1 text-ink">
                                    {c.replace(/_/g, " ")}
                                    <input className="input w-14 text-right text-xs" inputMode="decimal" aria-label={`${c} target percent`} value={open.targets[c]}
                                      onChange={(e) => setPanel({ ...open, targets: { ...open.targets, [c]: e.target.value } })} />
                                  </label>
                                ))}
                                <span className={`chip tabular-nums ${Math.abs(total - 100) < 0.1 ? "text-ok" : "text-crit"}`} data-testid="ips-total">{total.toFixed(1)}%</span>
                              </div>
                              <div className="flex flex-wrap items-center gap-2">
                                {([["band", "Band ±%"], ["max_position", "Largest holding %"], ["min_cash", "Cash floor %"]] as const).map(([key, label]) => (
                                  <input key={key} className="input w-36 text-xs" inputMode="decimal" aria-label={label} placeholder={label} value={open[key]}
                                    onChange={(e) => setPanel({ ...open, [key]: e.target.value })} />
                                ))}
                                <input className="input min-w-48 flex-1 text-xs" aria-label="Restricted" placeholder="Restricted" value={open.restricted}
                                  onChange={(e) => setPanel({ ...open, restricted: e.target.value })} />
                                {open.m.ips && (
                                  <input className="input min-w-48 flex-1 text-xs" aria-label="Reason" placeholder="Reason" value={open.reason}
                                    onChange={(e) => setPanel({ ...open, reason: e.target.value })} />
                                )}
                              </div>
                              <div className="flex gap-2">
                                <button className="btn-primary text-xs" data-testid="ips-confirm-yes" disabled={busy || !ready} onClick={send}>
                                  {nav.say ? "Ask Astra to record" : "Confirm"}
                                </button>
                                <button className="btn-outline text-xs" disabled={busy} onClick={() => setPanel(null)}>Not now</button>
                              </div>
                            </div>
                          </td>
                        </tr>
                      )}
                    </Fragment>
                  );
                })}
              </tbody>
            </table>
          </div>
        </Card>
      )}
    </div>
  );
}
