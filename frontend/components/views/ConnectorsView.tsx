"use client";

import { useState } from "react";
import Link from "next/link";
import { RefreshCw, Plus, Wifi, WifiOff, CheckCircle2, Plug, Webhook, Globe, Power } from "lucide-react";
import { Card, Empty, ErrorState, SkeletonList } from "@/components/ui";
import { useApi } from "@/lib/hooks";
import { api } from "@/lib/api";
import { titleCase, timeAgo } from "@/lib/format";
import { ViewHeader, chip } from "./ViewHeader";
import { CONNECTOR_STATUS_TONE, PageOnly, ReasonInput, because, connectorEnabled, connectorSay, useAsk } from "./admin/shared";
import {
  FoundationBreaksTable, FoundationCommandsTable, FoundationDomainsTable, type FoundationDomain, type FoundationPayload,
} from "@/app/workspace/components/artifacts/FoundationSourcesCard";

const CONNECTOR_DOMAINS = [
  "custody", "portfolio_accounting", "oms_execution", "market_research_data",
  "private_markets", "held_away", "investment_engine", "crm",
  "productivity_bi", "aml_kyc", "documents_esign", "open_api_events",
];

/** Source systems, by domain. Beside the conversation adding, going live, enabling and syncing are the
 * conversation's tools; credentials and a custom connector are typed in on the page only. */
export function ConnectorsView() {
  const ask = useAsk();
  const { data: connectors, loading, error, refetch } = useApi<any[]>("/api/conduit/connectors");
  const { data: registry } = useApi<any[]>("/api/conduit/registry");
  const [showCat, setShowCat] = useState(false);
  const [showCustom, setShowCustom] = useState(false);

  const byDomain: Record<string, any[]> = {};
  (connectors || []).forEach((c) => {
    (byDomain[c.domain] ||= []).push(c);
  });
  const configuredKeys = new Set((connectors || []).map((c) => c.provider_key));
  const live = (connectors || []).filter((c) => !c.use_mock).length;

  async function add(p: any) {
    if (ask) return ask(connectorSay.add(p));
    await api("/api/conduit/connectors", { body: { provider_key: p.key } });
    refetch();
  }

  return (
    <div className="view-container" data-testid="connectors-view">
      <ViewHeader
        title="Connectors"
        meta={connectors ? (
          <>
            <span className={`${chip} tabular-nums`}>{connectors.length} connectors</span>
            <span className={`${chip} tabular-nums`}>{live} live</span>
          </>
        ) : undefined}
        actions={
          <>
            <button className="btn-outline" onClick={() => { setShowCat(!showCat); setShowCustom(false); }} data-testid="connectors-add" aria-pressed={showCat}>
              <Plus size={16} /> Add connector
            </button>
            {ask ? (
              <span className="flex items-center gap-1.5" data-testid="connectors-custom-page">
                <PageOnly label="set up on the page" />
                <Link href="/admin/connectors" target="_blank" rel="noopener" className="btn-outline text-xs"><Webhook size={14} /> Custom</Link>
              </span>
            ) : (
              <button className="btn-outline" onClick={() => { setShowCustom(!showCustom); setShowCat(false); }} data-testid="connectors-custom" aria-pressed={showCustom}>
                <Webhook size={16} /> Custom
              </button>
            )}
          </>
        }
      />

      {showCat && registry && (
        <Card className="mb-5" data-testid="connectors-catalogue">
          <div className="mb-3 font-semibold text-ink">Catalogue</div>
          <div className="grid gap-2 cq-sm:grid-cols-2 cq-lg:grid-cols-3">
            {registry.filter((p) => !p.key.startsWith("custom.")).map((p) => (
              <div key={p.key} className="flex items-start gap-2 rounded-xl border border-border-soft p-3">
                <div className="min-w-0 flex-1">
                  <div className="text-sm font-medium text-ink">{p.display_name}</div>
                  <div className="mt-1 flex flex-wrap gap-1">
                    <span className={`${chip} text-[10px]`}>{titleCase(p.domain)}</span>
                    {p.supports_live && <span className="chip bg-ok-bg text-[10px] text-ok">live</span>}
                  </div>
                </div>
                {configuredKeys.has(p.key) ? (
                  <span className="chip bg-ok-bg text-[10px] text-ok">Added</span>
                ) : (
                  <button className="btn-ghost px-2 py-1 text-xs" onClick={() => add(p)} data-testid="connectors-catalogue-add">
                    {ask ? "Ask Astra" : "Add"}
                  </button>
                )}
              </div>
            ))}
          </div>
        </Card>
      )}

      {showCustom && !ask && (
        <CustomConnectorForm onSaved={() => { setShowCustom(false); refetch(); }} />
      )}

      <FoundationSection />

      {loading && !connectors ? (
        <SkeletonList count={3} />
      ) : error && !connectors ? (
        <Card><ErrorState what="connectors" message={error} onRetry={refetch} /></Card>
      ) : !connectors?.length ? (
        <Card><Empty>No connectors</Empty></Card>
      ) : (
        <div className="space-y-6">
          {Object.entries(byDomain).map(([domain, list]) => (
            <div key={domain}>
              <div className="tile-label mb-2">{titleCase(domain)}</div>
              <div className="grid gap-3 cq-md:grid-cols-2">
                {list.map((c) => (
                  <ConnectorCard key={c.id} c={c} schema={registry?.find((p) => p.key === c.provider_key)} onChange={refetch} />
                ))}
              </div>
            </div>
          ))}
        </div>
      )}
    </div>
  );
}

function CustomConnectorForm({ onSaved }: { onSaved: () => void }) {
  const [type, setType] = useState<"custom.webhook" | "custom.rest">("custom.webhook");
  const [form, setForm] = useState({
    display_name: "", domain: "open_api_events",
    webhook_url: "", base_url: "",
    auth_header: "Authorization", auth_value: "",
    event_filter: "", field_mappings: "",
  });
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState<string | null>(null);

  async function submit(e: React.FormEvent) {
    e.preventDefault();
    setBusy(true);
    setErr(null);
    try {
      const payload: any = {
        display_name: form.display_name,
        domain: form.domain,
        connector_type: type,
      };
      if (type === "custom.webhook") {
        payload.webhook_url = form.webhook_url;
        payload.auth_header = form.auth_header || "Authorization";
        if (form.auth_value) payload.auth_value = form.auth_value;
      } else {
        payload.base_url = form.base_url;
        if (form.auth_value) payload.auth_value = form.auth_value;
      }
      if (form.event_filter.trim()) payload.event_filter = form.event_filter.trim();
      if (form.field_mappings.trim()) {
        try {
          payload.field_mappings = JSON.parse(form.field_mappings);
        } catch {
          setErr("Field mappings must be valid JSON.");
          setBusy(false);
          return;
        }
      }
      await api("/api/admin/connectors/custom", { body: payload });
      onSaved();
    } catch (e: any) {
      setErr(e.message || "Failed to create connector.");
    } finally {
      setBusy(false);
    }
  }

  const kind = (on: boolean) =>
    `flex flex-1 items-center justify-center gap-2 rounded-lg border px-3 py-2 text-sm transition ${on ? "border-ink-muted bg-border-soft font-medium text-ink" : "border-border text-ink-muted"}`;

  return (
    <Card className="mb-5" data-testid="connectors-custom-form">
      <div className="mb-4 flex items-center gap-2 font-semibold text-ink">
        <Webhook size={16} /> Custom connector
      </div>
      <form onSubmit={submit} className="space-y-3">
        <div className="flex flex-wrap gap-2">
          <button type="button" className={kind(type === "custom.webhook")} aria-pressed={type === "custom.webhook"} onClick={() => setType("custom.webhook")}>
            <Webhook size={14} /> Outbound webhook
          </button>
          <button type="button" className={kind(type === "custom.rest")} aria-pressed={type === "custom.rest"} onClick={() => setType("custom.rest")}>
            <Globe size={14} /> Inbound REST API
          </button>
        </div>

        <div className="grid gap-3 cq-sm:grid-cols-2">
          <div>
            <label className="label">Display name *</label>
            <input className="input" required value={form.display_name}
              onChange={(e) => setForm({ ...form, display_name: e.target.value })} />
          </div>
          <div>
            <label className="label">Domain</label>
            <select className="input" value={form.domain}
              onChange={(e) => setForm({ ...form, domain: e.target.value })}>
              {CONNECTOR_DOMAINS.map((d) => (
                <option key={d} value={d}>{titleCase(d)}</option>
              ))}
            </select>
          </div>
        </div>

        {type === "custom.webhook" ? (
          <>
            <div>
              <label className="label">Webhook URL *</label>
              <input className="input" type="url" required placeholder="https://…" value={form.webhook_url}
                onChange={(e) => setForm({ ...form, webhook_url: e.target.value })} />
            </div>
            <div className="grid gap-3 cq-sm:grid-cols-2">
              <div>
                <label className="label">Auth header name</label>
                <input className="input" placeholder="Authorization" value={form.auth_header}
                  onChange={(e) => setForm({ ...form, auth_header: e.target.value })} />
              </div>
              <div>
                <label className="label">Auth value / token</label>
                <input className="input" type="password" placeholder="Bearer …" value={form.auth_value}
                  onChange={(e) => setForm({ ...form, auth_value: e.target.value })} />
              </div>
            </div>
            <div>
              <label className="label">Event filter (comma-separated)</label>
              <input className="input" placeholder="recommendation.approved, agent.paused" value={form.event_filter}
                onChange={(e) => setForm({ ...form, event_filter: e.target.value })} />
            </div>
          </>
        ) : (
          <>
            <div>
              <label className="label">Base URL *</label>
              <input className="input" type="url" required placeholder="https://api.example.com" value={form.base_url}
                onChange={(e) => setForm({ ...form, base_url: e.target.value })} />
            </div>
            <div>
              <label className="label">API key</label>
              <input className="input" type="password" value={form.auth_value}
                onChange={(e) => setForm({ ...form, auth_value: e.target.value })} />
            </div>
          </>
        )}

        <div>
          <label className="label">Field mappings (JSON)</label>
          <textarea className="input font-mono text-xs" rows={2}
            placeholder='{"source_field": "dest_field"}'
            value={form.field_mappings}
            onChange={(e) => setForm({ ...form, field_mappings: e.target.value })} />
        </div>

        {err && <div className="chip bg-crit-bg text-crit" role="alert">{err}</div>}
        <div className="flex flex-wrap gap-2">
          <button type="submit" className="btn-primary" disabled={busy}>{busy ? "Saving…" : "Register connector"}</button>
          <button type="button" className="btn-outline" onClick={() => onSaved()}>Cancel</button>
        </div>
      </form>
    </Card>
  );
}

function ConnectorCard({ c, schema, onChange }: { c: any; schema: any; onChange: () => void }) {
  const ask = useAsk();
  const [cfg, setCfg] = useState<Record<string, any>>(c.config || {});
  const [busy, setBusy] = useState<string | null>(null);
  const [syncMsg, setSyncMsg] = useState<string | null>(null);
  const enabled = connectorEnabled(c);

  async function save() {
    setBusy("save");
    await api(`/api/conduit/connectors/${c.id}`, { method: "PATCH", body: { config: cfg } });
    setBusy(null);
    onChange();
  }
  async function toggleLive() {
    if (ask) return ask(connectorSay.live(c));
    setBusy("toggle");
    await api(`/api/conduit/connectors/${c.id}`, { method: "PATCH", body: { use_mock: !c.use_mock } });
    setBusy(null);
    onChange();
  }
  async function toggleEnabled() {
    if (ask) return ask(connectorSay.enabled(c));
    setBusy("enable");
    await api(`/api/conduit/connectors/${c.id}`, { method: "PATCH", body: { enabled: !enabled } });
    setBusy(null);
    onChange();
  }
  async function sync() {
    if (ask) return ask(connectorSay.sync(c));
    setBusy("sync");
    const r = await api(`/api/conduit/connectors/${c.id}/sync`, { body: {} });
    setSyncMsg(`${r.status} · ${r.records_ingested} records · ${r.detail?.mode || ""}`);
    setBusy(null);
    onChange();
  }

  const isCustom = c.provider_key?.startsWith("custom.");

  return (
    <Card data-testid="connector-card">
      <div className="flex flex-wrap items-start justify-between gap-2">
        <div className="min-w-0">
          <div className="flex flex-wrap items-center gap-2 font-medium text-ink">
            {isCustom ? <Webhook size={15} className="text-ink-muted" /> : <Plug size={15} className="text-ink-muted" />}
            {c.display_name}
            {isCustom && <span className={`${chip} text-[10px]`}>custom</span>}
          </div>
          <div className="mt-1 flex flex-wrap items-center gap-1.5">
            {c.use_mock
              ? <span className="chip bg-warn-bg text-warn"><WifiOff size={12} /> Mock data</span>
              : <span className={`chip ${CONNECTOR_STATUS_TONE[c.status] || "bg-border-soft text-ink-muted"}`}><Wifi size={12} /> {titleCase(c.status)}</span>}
            {!enabled && <span className="chip bg-border-soft text-ink-muted">disabled</span>}
            {c.last_synced_at && <span className={`${chip} font-mono`}>synced {timeAgo(c.last_synced_at)}</span>}
            {c.sync_cron && <span className={`${chip} font-mono`}>{c.sync_cron}</span>}
          </div>
        </div>
        <div className="flex flex-wrap items-center gap-1">
          {c.supports_live && (
            <button className="btn-ghost px-2 py-1 text-xs" onClick={toggleLive} disabled={busy === "toggle"} data-testid="connector-live">
              {c.use_mock ? "Go live" : "Use mock"}
            </button>
          )}
          <button className="btn-ghost px-2 py-1 text-xs" onClick={toggleEnabled} disabled={busy === "enable"} data-testid="connector-enabled">
            <Power size={12} /> {enabled ? "Disable" : "Enable"}
          </button>
        </div>
      </div>

      {schema?.config_schema?.length > 0 && !ask && (
        <div className="mt-3 space-y-2">
          {schema.config_schema.map((field: any) => (
            <div key={field.key}>
              <label className="label">{field.label}</label>
              <input
                className="input py-1.5 text-sm"
                type={field.secret ? "password" : field.type === "number" ? "number" : "text"}
                placeholder={field.default ? String(field.default) : field.secret ? "••••••••" : ""}
                value={cfg[field.key] ?? ""}
                onChange={(e) => setCfg({ ...cfg, [field.key]: e.target.value })}
              />
            </div>
          ))}
        </div>
      )}

      <div className="mt-3 flex flex-wrap items-center gap-2">
        {ask ? (
          schema?.config_schema?.length > 0 && <PageOnly label="credentials on the page" />
        ) : (
          <button className="btn-outline text-xs" onClick={save} disabled={busy === "save"}>
            {busy === "save" ? "Saving…" : "Save config"}
          </button>
        )}
        <button className="btn-outline text-xs" onClick={sync} disabled={busy === "sync"} data-testid="connector-sync">
          <RefreshCw size={13} className={busy === "sync" ? "animate-spin" : ""} /> {ask ? "Ask Astra to sync" : "Sync now"}
        </button>
        {syncMsg && <span className="chip bg-ok-bg text-ok"><CheckCircle2 size={12} /> {syncMsg}</span>}
      </div>
    </Card>
  );
}

// ── The Foundation layer (B-161 to B-163): where each domain of the firm's data comes from ──

const foundationSay = {
  sync: (d: FoundationDomain) => `Sync the ${d.label} data from ${d.adapter} now`,
  owner: (d: FoundationDomain, key: string, reason: string) =>
    `Make ${key === "native" ? "Astra's own tables" : key} the owner of the ${d.label} data${because(reason)}`,
};

/** Each domain's owner, what it supplies, its sync, freshness, records, breaks and waiting commands. Beside the
 * conversation a sync and an owner change are its tools, behind their Confirm cards. */
function FoundationSection() {
  const ask = useAsk();
  const { data, error, refetch } = useApi<FoundationPayload>("/api/conduit/foundation");
  const [busy, setBusy] = useState<string | null>(null);
  const [owner, setOwner] = useState<Record<string, string>>({});
  const [reason, setReason] = useState("");
  const [err, setErr] = useState<string | null>(null);
  if (error && !data) return null;   // a role that does not read the sources sees the connectors only
  if (!data) return null;

  async function sync(d: FoundationDomain) {
    if (ask) return ask(foundationSay.sync(d));
    setBusy(d.domain);
    setErr(null);
    try {
      await api(`/api/conduit/foundation/${d.domain}/sync`, { body: {} });
    } catch (e: any) {
      setErr(e.message || "Sync failed.");
    }
    setBusy(null);
    refetch();
  }
  async function save(d: FoundationDomain) {
    const key = owner[d.domain];
    if (!key || key === d.owner) return;
    if (ask) return ask(foundationSay.owner(d, key, reason));
    setBusy(`owner-${d.domain}`);
    setErr(null);
    try {
      await api(`/api/admin/foundation-sources/${d.domain}`, { method: "PUT", body: { owner: key, reason } });
      setReason("");
    } catch (e: any) {
      setErr(e.message || "Not changed.");
    }
    setBusy(null);
    refetch();
  }

  return (
    <div className="mb-6" data-testid="foundation-section">
      <div className="tile-label mb-2 flex flex-wrap items-center gap-2">
        Foundation sources
        <span className={`${chip} text-[10px]`}>{data.native ? "all native" : `${data.domains.filter((d) => !d.native).length} connected`}</span>
        {data.simulated && <span className={`${chip} text-[10px] text-warn`} data-testid="foundation-section-simulated">simulated</span>}
      </div>
      <Card className="overflow-hidden p-0">
        <FoundationDomainsTable
          domains={data.domains}
          action={(d) => (
            <span className="flex flex-wrap items-center justify-end gap-1">
              {d.owners_available.length > 1 && (
                <>
                  <select className="input w-36 py-1 text-xs" aria-label={`Owner of ${d.label}`} data-testid="foundation-owner-select"
                    value={owner[d.domain] ?? d.owner} onChange={(e) => setOwner({ ...owner, [d.domain]: e.target.value })}>
                    {d.owners_available.map((k) => <option key={k} value={k}>{k}</option>)}
                  </select>
                  {(owner[d.domain] ?? d.owner) !== d.owner && (
                    <button className="btn-outline px-2 py-1 text-xs" onClick={() => save(d)} disabled={busy === `owner-${d.domain}`}
                      data-testid="foundation-owner-save">
                      {ask ? "Ask Astra" : "Set owner"}
                    </button>
                  )}
                </>
              )}
              {!d.native && (
                <button className="btn-ghost px-2 py-1 text-xs" onClick={() => sync(d)} disabled={busy === d.domain} data-testid="foundation-sync">
                  <RefreshCw size={12} className={busy === d.domain ? "animate-spin" : ""} /> {ask ? "Ask Astra to sync" : "Sync"}
                </button>
              )}
            </span>
          )}
        />
        {Object.entries(owner).some(([k, v]) => v !== data.domains.find((d) => d.domain === k)?.owner) && (
          <div className="flex flex-wrap items-center gap-2 border-t border-border-soft px-3 py-2">
            <ReasonInput value={reason} onChange={setReason} className="w-72" />
          </div>
        )}
        {err && <div className="border-t border-border-soft px-3 py-2"><span className="chip bg-crit-bg text-crit" role="alert">{err}</span></div>}
      </Card>
      {data.breaks.length > 0 && (
        <Card className="mt-3 overflow-hidden p-0" data-testid="foundation-breaks-card">
          <div className="tile-label px-3 pt-2">Breaks</div>
          <FoundationBreaksTable rows={data.breaks} />
        </Card>
      )}
      {data.commands.length > 0 && (
        <Card className="mt-3 overflow-hidden p-0" data-testid="foundation-commands-card">
          <div className="tile-label px-3 pt-2">Commands</div>
          <FoundationCommandsTable rows={data.commands} />
        </Card>
      )}
    </div>
  );
}