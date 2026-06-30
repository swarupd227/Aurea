"use client";
import { useState } from "react";
import { RefreshCw, Plus, Wifi, WifiOff, CheckCircle2, Plug, Webhook, Globe } from "lucide-react";
import PageHeader from "@/components/PageHeader";
import { Card, Spinner } from "@/components/ui";
import { useApi } from "@/lib/hooks";
import { api } from "@/lib/api";
import { titleCase, timeAgo } from "@/lib/format";

const CONNECTOR_DOMAINS = [
  "custody", "portfolio_accounting", "oms_execution", "market_research_data",
  "private_markets", "held_away", "investment_engine", "crm",
  "productivity_bi", "aml_kyc", "documents_esign", "open_api_events",
];

export default function Connectors() {
  const { data: connectors, loading, refetch } = useApi<any[]>("/api/conduit/connectors");
  const { data: registry } = useApi<any[]>("/api/conduit/registry");
  const [showCat, setShowCat] = useState(false);
  const [showCustom, setShowCustom] = useState(false);

  const byDomain: Record<string, any[]> = {};
  (connectors || []).forEach((c) => {
    (byDomain[c.domain] ||= []).push(c);
  });
  const configuredKeys = new Set((connectors || []).map((c) => c.provider_key));

  return (
    <div>
      <PageHeader
        title="Connectors"
        sub="Connect and sync source systems. Mock data by default; add credentials to go live."
        actions={
          <div className="flex gap-2">
            <button className="btn-outline" onClick={() => { setShowCat(!showCat); setShowCustom(false); }}>
              <Plus size={16} /> Add connector
            </button>
            <button className="btn-outline" onClick={() => { setShowCustom(!showCustom); setShowCat(false); }}>
              <Webhook size={16} /> Custom
            </button>
          </div>
        }
      />

      {showCat && registry && (
        <Card className="mb-5">
          <div className="font-semibold text-ink mb-3">Connector catalogue</div>
          <div className="grid sm:grid-cols-2 lg:grid-cols-3 gap-2">
            {registry.filter((p) => !p.key.startsWith("custom.")).map((p) => (
              <div key={p.key} className="rounded-xl border border-navy-100 p-3 flex items-start gap-2">
                <div className="flex-1">
                  <div className="text-sm font-medium text-ink">{p.display_name}</div>
                  <div className="text-xs text-ink-muted">{titleCase(p.domain)}{p.supports_live ? " · live ✓" : ""}</div>
                </div>
                {configuredKeys.has(p.key) ? (
                  <span className="chip bg-positive/10 text-positive text-[10px]">Added</span>
                ) : (
                  <button
                    className="btn-ghost text-xs px-2 py-1"
                    onClick={async () => {
                      await api("/api/conduit/connectors", { body: { provider_key: p.key } });
                      refetch();
                    }}
                  >
                    Add
                  </button>
                )}
              </div>
            ))}
          </div>
        </Card>
      )}

      {showCustom && (
        <CustomConnectorForm onSaved={() => { setShowCustom(false); refetch(); }} />
      )}

      {loading ? (
        <Spinner />
      ) : (
        <div className="space-y-6">
          {Object.entries(byDomain).map(([domain, list]) => (
            <div key={domain}>
              <div className="text-xs font-semibold uppercase tracking-wide text-ink-muted mb-2">{titleCase(domain)}</div>
              <div className="grid md:grid-cols-2 gap-3">
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

  return (
    <Card className="mb-5">
      <div className="font-semibold text-ink mb-4 flex items-center gap-2">
        <Webhook size={16} /> Register custom connector
      </div>
      <form onSubmit={submit} className="space-y-3">
        <div className="flex gap-2">
          <button type="button"
            className={`flex-1 rounded-lg border px-3 py-2 text-sm flex items-center justify-center gap-2 transition ${type === "custom.webhook" ? "border-gold bg-gold-soft/10 text-ink font-medium" : "border-navy-100 text-ink-muted"}`}
            onClick={() => setType("custom.webhook")}>
            <Webhook size={14} /> Outbound Webhook
          </button>
          <button type="button"
            className={`flex-1 rounded-lg border px-3 py-2 text-sm flex items-center justify-center gap-2 transition ${type === "custom.rest" ? "border-gold bg-gold-soft/10 text-ink font-medium" : "border-navy-100 text-ink-muted"}`}
            onClick={() => setType("custom.rest")}>
            <Globe size={14} /> Inbound REST API
          </button>
        </div>

        <div className="grid sm:grid-cols-2 gap-3">
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
            <div className="grid sm:grid-cols-2 gap-3">
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
              <label className="label">Event filter (comma-separated, optional)</label>
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
          <label className="label">Field mappings (JSON, optional)</label>
          <textarea className="input font-mono text-xs" rows={2}
            placeholder='{"source_field": "dest_field"}'
            value={form.field_mappings}
            onChange={(e) => setForm({ ...form, field_mappings: e.target.value })} />
        </div>

        {err && <div className="text-sm text-critical bg-critical/5 rounded-lg px-3 py-2">{err}</div>}
        <div className="flex gap-2">
          <button type="submit" className="btn-primary" disabled={busy}>{busy ? "Saving…" : "Register connector"}</button>
          <button type="button" className="btn-outline" onClick={() => onSaved()}>Cancel</button>
        </div>
      </form>
    </Card>
  );
}

function ConnectorCard({ c, schema, onChange }: { c: any; schema: any; onChange: () => void }) {
  const [cfg, setCfg] = useState<Record<string, any>>(c.config || {});
  const [busy, setBusy] = useState<string | null>(null);
  const [syncMsg, setSyncMsg] = useState<string | null>(null);

  async function save() {
    setBusy("save");
    await api(`/api/conduit/connectors/${c.id}`, { method: "PATCH", body: { config: cfg } });
    setBusy(null);
    onChange();
  }
  async function toggleLive() {
    setBusy("toggle");
    await api(`/api/conduit/connectors/${c.id}`, { method: "PATCH", body: { use_mock: !c.use_mock } });
    setBusy(null);
    onChange();
  }
  async function sync() {
    setBusy("sync");
    const r = await api(`/api/conduit/connectors/${c.id}/sync`, { body: {} });
    setSyncMsg(`${r.status} · ${r.records_ingested} records · ${r.detail?.mode || ""}`);
    setBusy(null);
    onChange();
  }

  const statusColor =
    c.status === "connected" ? "text-positive" : c.status === "error" ? "text-critical" : "text-ink-muted";

  const isCustom = c.provider_key?.startsWith("custom.");

  return (
    <Card>
      <div className="flex items-start justify-between gap-2">
        <div>
          <div className="font-medium text-ink flex items-center gap-2">
            {isCustom ? <Webhook size={15} className="text-gold" /> : <Plug size={15} className="text-navy-500" />}
            {c.display_name}
            {isCustom && <span className="chip bg-gold/10 text-gold text-[10px]">custom</span>}
          </div>
          <div className={`text-xs mt-0.5 flex items-center gap-1 ${statusColor}`}>
            {c.use_mock ? <><WifiOff size={12} /> Mock data</> : <><Wifi size={12} /> {titleCase(c.status)}</>}
            {c.last_synced_at && <span className="text-ink-muted">· synced {timeAgo(c.last_synced_at)}</span>}
          </div>
        </div>
        {c.supports_live && (
          <button className="btn-ghost text-xs px-2 py-1" onClick={toggleLive} disabled={busy === "toggle"}>
            {c.use_mock ? "Go live" : "Use mock"}
          </button>
        )}
      </div>

      {schema?.config_schema?.length > 0 && (
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

      <div className="flex items-center gap-2 mt-3">
        <button className="btn-outline text-xs" onClick={save} disabled={busy === "save"}>
          {busy === "save" ? "Saving…" : "Save config"}
        </button>
        <button className="btn-outline text-xs" onClick={sync} disabled={busy === "sync"}>
          <RefreshCw size={13} className={busy === "sync" ? "animate-spin" : ""} /> Sync now
        </button>
        {syncMsg && <span className="text-xs text-positive flex items-center gap-1"><CheckCircle2 size={12} /> {syncMsg}</span>}
      </div>
    </Card>
  );
}
