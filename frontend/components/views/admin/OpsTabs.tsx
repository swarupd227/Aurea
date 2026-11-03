"use client";

import { useState } from "react";
import { Save, Check, RefreshCw, AlertCircle, CheckCircle2, CircleDot, Wifi, WifiOff, Bell, X, Power } from "lucide-react";
import { Card, Spinner, Empty, ErrorState } from "@/components/ui";
import { useApi } from "@/lib/hooks";
import { api } from "@/lib/api";
import { titleCase } from "@/lib/format";
import { ViewLink } from "../ViewNav";
import { chip } from "../ViewHeader";
import { CONNECTOR_STATUS_TONE, ReasonInput, TableCard, TD, TH, because, connectorEnabled, connectorSay, useAsk } from "./shared";

// ── Connectors ────────────────────────────────────────────────────────────────

const STATUS_ICONS: Record<string, any> = {
  connected: CheckCircle2, mock: CircleDot, error: AlertCircle,
  configured: Wifi, disabled: WifiOff,
};

export function ConnectorsTab() {
  const ask = useAsk();
  const { data: connectors, loading, error, refetch } = useApi<any[]>("/api/conduit/connectors");
  const [syncing, setSyncing] = useState<string | null>(null);
  const [syncResults, setSyncResults] = useState<Record<string, any>>({});
  const [cronEdit, setCronEdit] = useState<Record<string, string>>({});

  async function triggerSync(c: any) {
    if (ask) return ask(connectorSay.sync(c));
    setSyncing(c.id);
    try {
      const res = await api(`/api/conduit/connectors/${c.id}/sync`, { method: "POST", body: {} });
      setSyncResults((prev) => ({ ...prev, [c.id]: res }));
      refetch();
    } finally {
      setSyncing(null);
    }
  }

  async function toggleMock(c: any) {
    if (ask) return ask(connectorSay.live(c));
    await api(`/api/conduit/connectors/${c.id}`, { method: "PATCH", body: { use_mock: !c.use_mock } });
    refetch();
  }

  async function toggleEnabled(c: any) {
    if (ask) return ask(connectorSay.enabled(c));
    await api(`/api/conduit/connectors/${c.id}`, { method: "PATCH", body: { enabled: !connectorEnabled(c) } });
    refetch();
  }

  async function saveCron(c: any) {
    const cron = cronEdit[c.id] ?? "";
    const done = () => setCronEdit((p) => { const n = { ...p }; delete n[c.id]; return n; });
    if (ask) { ask(connectorSay.cron(c, cron)); return done(); }
    await api(`/api/conduit/connectors/${c.id}`, { method: "PATCH", body: { sync_cron: cron.trim() } });
    done();
    refetch();
  }

  if (loading && !connectors) return <Spinner />;
  if (error && !connectors) return <Card><ErrorState what="connectors" message={error} onRetry={refetch} /></Card>;
  if (!connectors?.length) return <Card><Empty>No connectors</Empty></Card>;

  return (
    <div className="max-w-5xl space-y-3" data-testid="admin-connectors">
      <div className="flex justify-end">
        <ViewLink href="/admin/connectors" className="btn-outline text-xs" data-testid="admin-connectors-all">Catalogue</ViewLink>
      </div>
      {connectors.map((c) => {
        const StatusIcon = STATUS_ICONS[c.status] || CircleDot;
        const syncRes = syncResults[c.id];
        const enabled = connectorEnabled(c);
        return (
          <Card key={c.id} className="space-y-2" data-testid="admin-connector-row">
            <div className="flex flex-wrap items-start gap-3">
              <div className="min-w-[12rem] flex-1">
                <div className="flex flex-wrap items-center gap-1.5">
                  <span className="font-medium text-ink">{c.display_name}</span>
                  <span className={chip}>{titleCase(c.domain)}</span>
                  <span className={`chip ${CONNECTOR_STATUS_TONE[c.status] || chip}`}><StatusIcon size={12} /> {titleCase(c.status)}</span>
                  {c.use_mock && <span className="chip bg-warn-bg text-warn">mock data</span>}
                </div>
                <div className="mt-1 flex flex-wrap items-center gap-1.5 text-xs text-ink-muted">
                  {c.last_synced_at
                    ? <span className={`${chip} font-mono`}>synced {new Date(c.last_synced_at).toLocaleString()}</span>
                    : !c.last_error && <span className={chip}>never synced</span>}
                  {c.last_error && <span className="chip max-w-xs truncate bg-crit-bg text-crit" title={c.last_error}>{c.last_error}</span>}
                  {syncRes && (
                    <span className={`chip ${syncRes.status === "success" ? "bg-ok-bg text-ok" : "bg-crit-bg text-crit"}`}>
                      {syncRes.status} · {syncRes.records_ingested} records
                    </span>
                  )}
                </div>
              </div>
              <div className="flex flex-wrap items-center gap-2">
                <button className="btn-outline px-2 py-1 text-xs" onClick={() => toggleMock(c)} data-testid="admin-connector-live">
                  {c.use_mock ? <><WifiOff size={11} /> Mock</> : <><Wifi size={11} /> Live</>}
                </button>
                <button className="btn-outline px-2 py-1 text-xs" onClick={() => toggleEnabled(c)} data-testid="admin-connector-enabled">
                  <Power size={11} /> {enabled ? "Enabled" : "Disabled"}
                </button>
                <button className="btn-outline px-2 py-1 text-xs" onClick={() => triggerSync(c)} disabled={syncing === c.id} data-testid="admin-connector-sync">
                  <RefreshCw size={11} className={syncing === c.id ? "animate-spin" : ""} />
                  {syncing === c.id ? "Syncing…" : ask ? "Ask Astra to sync" : "Sync now"}
                </button>
              </div>
            </div>
            <div className="flex flex-wrap items-center gap-2 border-t border-border-soft pt-2 text-xs">
              <span className="tile-label">Sync schedule</span>
              {cronEdit[c.id] !== undefined ? (
                <>
                  <input className="input w-40 py-1 font-mono text-xs" placeholder="0 6 * * *" aria-label="Sync schedule"
                    value={cronEdit[c.id]} onChange={(e) => setCronEdit((p) => ({ ...p, [c.id]: e.target.value }))} />
                  <button className="btn-primary px-2 py-1 text-xs" onClick={() => saveCron(c)}>{ask ? "Ask Astra" : "Save"}</button>
                  <button className="btn-outline px-2 py-1 text-xs" onClick={() => setCronEdit((p) => { const n = { ...p }; delete n[c.id]; return n; })} aria-label="Cancel"><X size={12} /></button>
                </>
              ) : (
                <>
                  <code className="font-mono text-ink">{c.sync_cron || "—"}</code>
                  <button className="text-ink-muted underline hover:text-ink" onClick={() => setCronEdit((p) => ({ ...p, [c.id]: c.sync_cron || "" }))}>Edit</button>
                </>
              )}
            </div>
          </Card>
        );
      })}
    </div>
  );
}

// ── Schedules ─────────────────────────────────────────────────────────────────

type ScheduleDraft = { key: string; cron?: string; enabled?: boolean; reason: string };

export function SchedulesTab() {
  const ask = useAsk();
  const { data, loading, error, refetch } = useApi<any[]>("/api/admin/schedules");
  const [draft, setDraft] = useState<ScheduleDraft | null>(null);
  const [saving, setSaving] = useState(false);
  const [msg, setMsg] = useState<string | null>(null);

  if (loading && !data) return <Spinner />;
  if (error || !data) return <Card><ErrorState what="schedules" message={error} onRetry={refetch} /></Card>;

  async function apply(s: any) {
    if (!draft) return;
    setMsg(null);
    if (ask) {
      const what = draft.cron !== undefined
        ? `Set the schedule of ${s.label} (${s.agent_key}) to "${draft.cron.trim()}"`
        : `Turn ${draft.enabled ? "on" : "off"} the schedule of ${s.label} (${s.agent_key})`;
      ask(what + because(draft.reason));
      setDraft(null);
      return;
    }
    setSaving(true);
    try {
      const body = draft.cron !== undefined ? { cron: draft.cron, reason: draft.reason.trim() } : { enabled: draft.enabled, reason: draft.reason.trim() };
      const r = await api<any>(`/api/admin/schedules/${s.agent_key}`, { method: "PATCH", body });
      setMsg(draft.cron !== undefined ? `Saved · picked up ${r.takes_effect}` : "Saved");
      setDraft(null);
      refetch();
    } catch (e: any) {
      setMsg(e.message);
    } finally {
      setSaving(false);
    }
  }

  function fmtDate(iso: string | null) {
    if (!iso) return "Never";
    const d = new Date(iso);
    return d.toLocaleString(undefined, { month: "short", day: "numeric", hour: "2-digit", minute: "2-digit" });
  }

  return (
    <div className="space-y-5" data-testid="admin-schedules">
      <div className="flex flex-wrap items-center gap-2">
        <span className={chip}>UTC · 5-field cron · picked up within 15 minutes</span>
        <span className={chip}>prices are fixed · changed in code</span>
        {msg && <span className={`${chip} text-ink`} role="status">{msg}</span>}
      </div>

      <TableCard testid="schedules-table">
        <thead>
          <tr className="border-b border-border-soft">
            <th className={TH}>Job</th>
            <th className={TH}>Runs</th>
            <th className={TH}>Default</th>
            <th className={TH}>Last run</th>
            <th className={`${TH} text-right`}>Run count</th>
            <th className={TH}>Status</th>
            <th className={TH} />
          </tr>
        </thead>
        <tbody className="divide-y divide-border-soft">
          {data.map((s: any) => {
            const mine = draft?.key === s.agent_key ? draft : null;
            return (
              <tr key={s.agent_key} title={s.why ?? undefined}>
                <td className={`${TD} font-medium text-ink`}>{s.label}</td>
                <td className={TD}>
                  {mine && mine.cron !== undefined ? (
                    <input
                      value={mine.cron}
                      onChange={(e) => setDraft({ ...mine, cron: e.target.value })}
                      className="input w-36 py-1 font-mono text-sm"
                      placeholder="45 2 * * *"
                      aria-label="Cron expression"
                    />
                  ) : (
                    <span className="font-mono text-ink">{s.cron ?? "—"}</span>
                  )}
                </td>
                <td className={`${TD} font-mono text-xs text-ink-muted`}>{s.default_cron ?? "—"}</td>
                <td className={`${TD} text-xs text-ink-muted`}>{fmtDate(s.last_run_at)}</td>
                <td className={`${TD} text-right font-mono text-xs tabular-nums text-ink-muted`}>{s.run_count.toLocaleString()}</td>
                <td className={TD}>
                  {s.paused ? <span className="chip bg-warn-bg text-warn">Paused</span>
                    : s.enabled ? <span className="chip bg-ok-bg text-ok">Active</span>
                    : <span className={`${chip} text-ink-muted`}>Off</span>}
                </td>
                <td className={`${TD} whitespace-nowrap text-right`}>
                  {s.fixed ? (
                    <span className={`${chip} text-ink-muted`}>fixed</span>
                  ) : mine ? (
                    <div className="flex items-center justify-end gap-1">
                      {mine.enabled !== undefined && <span className={chip}>{mine.enabled ? "turn on" : "turn off"}</span>}
                      <ReasonInput className="w-32" value={mine.reason} onChange={(v) => setDraft({ ...mine, reason: v })} />
                      <button onClick={() => apply(s)} disabled={saving || !mine.reason.trim()} className="btn-primary px-2 py-1 text-xs" data-testid="admin-schedule-apply">
                        {saving ? "…" : ask ? "Ask Astra" : "Save"}
                      </button>
                      <button onClick={() => setDraft(null)} className="btn-outline px-2 py-1 text-xs" aria-label="Cancel"><X size={12} /></button>
                    </div>
                  ) : (
                    <div className="flex items-center justify-end gap-1">
                      <button onClick={() => setDraft({ key: s.agent_key, cron: s.cron ?? s.default_cron ?? "", reason: "" })} className="btn-ghost px-2 py-1 text-xs">Edit</button>
                      <button onClick={() => setDraft({ key: s.agent_key, enabled: !s.enabled, reason: "" })} className="btn-ghost px-2 py-1 text-xs">
                        {s.enabled ? "Turn off" : "Turn on"}
                      </button>
                    </div>
                  )}
                </td>
              </tr>
            );
          })}
        </tbody>
      </TableCard>
    </div>
  );
}

// ── Notifications ─────────────────────────────────────────────────────────────

const NOTIF_EVENTS: { key: string; label: string; description: string }[] = [
  { key: "high_severity_flag", label: "High-severity flag", description: "Conduct surveillance raises a HIGH flag or auto-pauses an agent" },
  { key: "agent_paused",       label: "Agent paused",       description: "Kill-switch fires and an agent is paused" },
  { key: "recommendation_pending", label: "Pending recommendation", description: "A Tier-2 recommendation is awaiting adviser review" },
  { key: "daily_digest",       label: "Daily digest",       description: "Summary of activity, flags, and pending actions" },
];
const NOTIF_CHANNELS: { key: string; label: string }[] = [
  { key: "email",  label: "Email" },
  { key: "in_app", label: "In-app" },
];

const sameList = (a: string[], b: string[]) => [...a].sort().join(",") === [...b].sort().join(",");

export function NotificationsTab() {
  const ask = useAsk();
  const { data: configs, loading, error, refetch } = useApi<any[]>("/api/admin/notifications");
  const { data: users } = useApi<any[]>("/api/admin/users");
  const [local, setLocal] = useState<Record<string, any>>({});
  const [saving, setSaving] = useState(false);
  const [saved, setSaved] = useState(false);

  const key = (ev: string, ch: string) => `${ev}::${ch}`;
  const people = (users || []).filter((u) => u.is_active && u.email);

  function stored(ev: string, ch: string) {
    return configs?.find((c: any) => c.event_type === ev && c.channel === ch) || { enabled: false, recipients: [], config: {} };
  }
  function getCell(ev: string, ch: string) {
    const k = key(ev, ch);
    return local[k] !== undefined ? local[k] : stored(ev, ch);
  }

  function toggle(ev: string, ch: string) {
    const cell = getCell(ev, ch);
    setLocal((prev) => ({ ...prev, [key(ev, ch)]: { ...cell, enabled: !cell.enabled } }));
    setSaved(false);
  }

  function toggleRecipient(ev: string, ch: string, email: string) {
    const cell = getCell(ev, ch);
    const list: string[] = cell.recipients || [];
    const next = list.includes(email) ? list.filter((e) => e !== email) : [...list, email];
    setLocal((prev) => ({ ...prev, [key(ev, ch)]: { ...cell, recipients: next } }));
    setSaved(false);
  }

  /** set_notification, in words, for every cell that changed; null when none did. */
  function sentence(): string | null {
    const parts: string[] = [];
    for (const ev of NOTIF_EVENTS) {
      for (const ch of NOTIF_CHANNELS) {
        const before = stored(ev.key, ch.key);
        const after = getCell(ev.key, ch.key);
        const recips: string[] = after.recipients || [];
        const moved = !!before.enabled !== !!after.enabled || (ch.key === "email" && !sameList(before.recipients || [], recips));
        if (!moved) continue;
        const to = ch.key === "email" && after.enabled ? `, to ${recips.length ? recips.join(", ") : "no one"}` : "";
        parts.push(`${ev.label} (${ev.key}) by ${ch.key === "email" ? "email" : "in-app"} ${after.enabled ? "on" : "off"}${to}`);
      }
    }
    return parts.length ? `Change the notifications: ${parts.join("; ")}` : null;
  }

  async function save() {
    if (ask) {
      const s = sentence();
      if (s) ask(s);
      setLocal({});
      return;
    }
    setSaving(true);
    try {
      const updates = NOTIF_EVENTS.flatMap((ev) =>
        NOTIF_CHANNELS.map((ch) => {
          const cell = getCell(ev.key, ch.key);
          return { event_type: ev.key, channel: ch.key, enabled: cell.enabled, recipients: cell.recipients || [], config: cell.config || {} };
        })
      );
      await api("/api/admin/notifications", { method: "PATCH", body: updates });
      setSaved(true);
      setLocal({});
      refetch();
    } finally { setSaving(false); }
  }

  if (loading && !configs) return <Spinner />;
  if (error && !configs) return <Card><ErrorState what="notifications" message={error} onRetry={refetch} /></Card>;
  const pending = ask ? sentence() : null;

  return (
    <div className="max-w-4xl space-y-4" data-testid="admin-notifications">
      <Card>
        <div className="mb-4 flex flex-wrap items-center justify-between gap-2">
          <div className="flex items-center gap-2 font-semibold text-ink"><Bell size={15} /> Alerts</div>
          <div className="flex flex-wrap items-center gap-1.5">
            <span className={chip}>email via SMTP connector</span>
            <button className="btn-primary px-3 py-1.5 text-sm" onClick={save} disabled={saving || (!!ask && !pending)} data-testid="admin-notifications-save">
              {saving ? "Saving…" : saved ? <><Check size={14} /> Saved</> : <><Save size={14} /> {ask ? "Ask Astra" : "Save changes"}</>}
            </button>
          </div>
        </div>

        <div className="overflow-x-auto">
          <table className="w-full text-sm">
            <thead>
              <tr className="border-b border-border-soft">
                <th className={`${TH} px-0`}>Event</th>
                {NOTIF_CHANNELS.map((ch) => (
                  <th key={ch.key} className={`${TH} w-24 text-center`}>{ch.label}</th>
                ))}
                <th className={TH}>Email recipients</th>
              </tr>
            </thead>
            <tbody className="divide-y divide-border-soft">
              {NOTIF_EVENTS.map((ev) => {
                const email = getCell(ev.key, "email");
                const recips: string[] = email.recipients || [];
                const strangers = recips.filter((r) => !people.some((u) => u.email === r));
                return (
                  <tr key={ev.key}>
                    <td className="py-3 pr-4 align-top">
                      <div className="text-sm font-medium text-ink" title={ev.description}>{ev.label}</div>
                      <div className="font-mono text-[10px] text-ink-muted">{ev.key}</div>
                    </td>
                    {NOTIF_CHANNELS.map((ch) => {
                      const cell = getCell(ev.key, ch.key);
                      return (
                        <td key={ch.key} className="px-4 py-3 text-center align-top">
                          <button
                            onClick={() => toggle(ev.key, ch.key)}
                            role="switch"
                            aria-checked={!!cell.enabled}
                            aria-label={`${ev.label} by ${ch.label}`}
                            data-testid={`admin-notif-${ev.key}-${ch.key}`}
                            className={`relative h-5 w-9 rounded-full border transition-colors ${cell.enabled ? "border-ok/40 bg-ok/30" : "border-border bg-border-soft"}`}
                          >
                            <span className={`absolute top-0.5 h-3.5 w-3.5 rounded-full transition-all ${cell.enabled ? "left-4 bg-ok" : "left-0.5 bg-ink-muted"}`} />
                          </button>
                        </td>
                      );
                    })}
                    <td className="px-4 py-3 align-top">
                      {email.enabled ? (
                        <div className="flex flex-wrap gap-1" data-testid="admin-notif-recipients">
                          {people.map((u) => {
                            const on = recips.includes(u.email);
                            return (
                              <button key={u.id} aria-pressed={on} title={u.email}
                                onClick={() => toggleRecipient(ev.key, "email", u.email)}
                                className={`chip ${on ? "bg-border text-ink" : "bg-border-soft text-ink-muted"}`}>
                                {on && <Check size={11} />} {u.full_name}
                              </button>
                            );
                          })}
                          {strangers.map((r) => (
                            <button key={r} onClick={() => toggleRecipient(ev.key, "email", r)} className="chip bg-warn-bg text-warn" title="Not a user of this firm">
                              {r} <X size={11} />
                            </button>
                          ))}
                          {!people.length && <span className={`${chip} text-ink-muted`}>no users</span>}
                        </div>
                      ) : (
                        <span className={`${chip} text-ink-muted`}>email off</span>
                      )}
                    </td>
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

// ── Data quality ──────────────────────────────────────────────────────────────

const HEALTH: Record<string, [string, string]> = {
  green: ["Healthy", "bg-ok-bg text-ok"],
  amber: ["Attention", "bg-warn-bg text-warn"],
  red: ["Critical", "bg-crit-bg text-crit"],
};

export function DataQualityTab() {
  const { data, loading, error, refetch } = useApi<{
    summary: { total: number; green: number; amber: number; red: number };
    households: any[];
    days_since_sync: number | null;
  }>("/api/admin/data-quality");

  if (loading && !data) return <Spinner />;
  if (error || !data) return <Card><ErrorState what="data quality" message={error} onRetry={refetch} /></Card>;

  const { summary, households } = data;

  return (
    <div className="space-y-5" data-testid="admin-data-quality">
      <div className="grid grid-cols-2 gap-3 cq-lg:grid-cols-4">
        {[
          { label: "Households", value: summary.total, cls: "text-ink" },
          { label: "Healthy", value: summary.green, cls: "text-ok" },
          { label: "Needs attention", value: summary.amber, cls: summary.amber ? "text-warn" : "text-ink-muted" },
          { label: "Critical", value: summary.red, cls: summary.red ? "text-crit" : "text-ink-muted" },
        ].map(({ label, value, cls }) => (
          <Card key={label} className="p-4">
            <div className="tile-label">{label}</div>
            <div className={`mt-1 font-mono text-2xl tabular-nums ${cls}`}>{value}</div>
          </Card>
        ))}
      </div>

      <div className="flex flex-wrap items-center gap-2">
        {data.days_since_sync !== null && <span className={chip}>Last feed run {data.days_since_sync}d ago</span>}
        <ViewLink href="/studio/prices" className={`${chip} hover:text-ink`} data-testid="dq-prices-link">Prices by instrument</ViewLink>
      </div>

      <TableCard>
        <thead>
          <tr className="border-b border-border-soft">
            <th className={TH}>Household</th>
            <th className={TH}>Health</th>
            <th className={TH}>Completeness</th>
            <th className={TH}>Missing fields</th>
            <th className={TH}>Prices</th>
            <th className={`${TH} text-right`}>Mandates</th>
            <th className={`${TH} text-right`}>Members</th>
          </tr>
        </thead>
        <tbody className="divide-y divide-border-soft">
          {households.map((hh: any) => {
            const [word, tone] = HEALTH[hh.health] || HEALTH.red;
            return (
              <tr key={hh.household_id}>
                <td className={`${TD} font-medium text-ink`}>{hh.name}</td>
                <td className={TD}><span className={`chip ${tone}`}>{word}</span></td>
                <td className={TD}>
                  <div className="flex items-center gap-2">
                    <div className="h-1.5 w-20 overflow-hidden rounded-full bg-border-soft">
                      <div
                        className={`h-full rounded-full ${hh.completeness >= 0.9 ? "bg-ok" : hh.completeness >= 0.6 ? "bg-warn" : "bg-crit"}`}
                        style={{ width: `${Math.round(hh.completeness * 100)}%` }}
                      />
                    </div>
                    <span className="font-mono text-xs tabular-nums text-ink-muted">{Math.round(hh.completeness * 100)}%</span>
                  </div>
                </td>
                <td className={TD}>
                  {hh.missing_fields.length === 0
                    ? <span className="text-xs text-ok">None</span>
                    : <span className="text-xs text-crit">{hh.missing_fields.join(", ")}</span>}
                </td>
                <td className={TD} data-testid="dq-prices">
                  {(hh.untrusted_prices ?? []).length > 0 ? (
                    <span className="text-xs text-crit" title="Held at a price that is missing, a placeholder, or past its limit">
                      {hh.untrusted_prices.join(", ")}
                    </span>
                  ) : hh.price_age_days != null ? (
                    <span className="font-mono text-xs text-ink-muted">{hh.price_age_days}d · {hh.stalest_symbol}</span>
                  ) : (
                    <span className="text-xs text-ink-muted">—</span>
                  )}
                </td>
                <td className={`${TD} text-right font-mono text-xs tabular-nums text-ink-muted`}>{hh.mandate_count}</td>
                <td className={`${TD} text-right font-mono text-xs tabular-nums text-ink-muted`}>{hh.person_count}</td>
              </tr>
            );
          })}
        </tbody>
      </TableCard>
    </div>
  );
}
