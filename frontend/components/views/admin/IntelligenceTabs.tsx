"use client";

import { useState } from "react";
import { Save, Power, Pause, Play, Plus, Clock, Pencil, X, CheckCircle2, Eye, SendHorizonal, RefreshCw, Trash2 } from "lucide-react";
import { Card, Spinner, InlineConfirmButton, Empty, ErrorState } from "@/components/ui";
import { useApi } from "@/lib/hooks";
import { api } from "@/lib/api";
import { money, titleCase } from "@/lib/format";
import { chip } from "../ViewHeader";
import { PageOnly, ReasonInput, Saved, because, q, tierWord, useAsk } from "./shared";

// ── Agents & autonomy ─────────────────────────────────────────────────────────

/** The fields set_agent_switches takes, as the catalogue names them. */
const AGENT_FIELDS: Record<string, string> = {
  default_tier: "tier", enabled: "enabled", paused: "paused",
  schedule_cron: "schedule_cron", schedule_enabled: "schedule_enabled",
};

function agentChange(a: any, patch: Record<string, any>): string | null {
  const parts: string[] = [];
  if (patch.default_tier !== undefined) parts.push(tierWord(patch.default_tier));
  if (patch.enabled !== undefined) parts.push(patch.enabled ? "enable it" : "disable it");
  if (patch.paused !== undefined) parts.push(patch.paused ? "pause it" : "resume it");
  if (patch.schedule_cron !== undefined) parts.push(patch.schedule_cron ? `schedule "${patch.schedule_cron}"` : "clear its schedule");
  if (patch.schedule_enabled !== undefined) parts.push(`turn its schedule ${patch.schedule_enabled ? "on" : "off"}`);
  return parts.length ? `Change the agent ${a.name} (${a.agent_key}): ${parts.join("; ")}` : null;
}

type AgentDraft = { patch: Record<string, any>; reason: string };

export function AgentsTab() {
  const ask = useAsk();
  const { data, loading, error, refetch } = useApi<any[]>("/api/agents/catalogue");
  const [cronEdit, setCronEdit] = useState<Record<string, string>>({});
  const [drafts, setDrafts] = useState<Record<string, AgentDraft>>({});
  const [refused, setRefused] = useState<string | null>(null);
  const [busy, setBusy] = useState<string | null>(null);
  const tiers = ["tier_1", "tier_2", "tier_3"];

  const current = (a: any, field: string) => a[AGENT_FIELDS[field]];
  const value = (a: any, field: string) => {
    const p = drafts[a.agent_key]?.patch || {};
    return field in p ? p[field] : current(a, field);
  };

  /** A change waits beside its reason; setting a field back to what it is drops it. */
  function stage(a: any, patch: Record<string, any>) {
    setDrafts((prev) => {
      const was = prev[a.agent_key] || { patch: {}, reason: "" };
      const next = { ...was.patch, ...patch };
      for (const k of Object.keys(patch)) if ((next[k] ?? null) === (current(a, k) ?? null)) delete next[k];
      const out = { ...prev };
      if (Object.keys(next).length) out[a.agent_key] = { patch: next, reason: was.reason };
      else delete out[a.agent_key];
      return out;
    });
  }
  const drop = (key: string) => setDrafts((prev) => { const n = { ...prev }; delete n[key]; return n; });

  // A change can be refused: a tier the licence does not allow, an agent surveillance paused while its flag is open.
  async function apply(a: any) {
    const d = drafts[a.agent_key];
    if (!d) return;
    setRefused(null);
    if (ask) {
      const s = agentChange(a, d.patch);
      if (s) ask(s + because(d.reason));
      return drop(a.agent_key);
    }
    const body: Record<string, any> = { ...d.patch, reason: d.reason.trim() };
    if ("schedule_cron" in body) body.schedule_cron = body.schedule_cron || null;
    if (body.paused === true) body.paused_reason = d.reason.trim();
    setBusy(a.agent_key);
    try {
      await api(`/api/admin/agents/${a.agent_key}`, { method: "PATCH", body });
      drop(a.agent_key);
    } catch (e: any) { setRefused(e?.message || "That change was refused."); }
    finally { setBusy(null); }
    refetch();
  }

  function stageCron(a: any) {
    stage(a, { schedule_cron: (cronEdit[a.agent_key] ?? "").trim() });
    setCronEdit((prev) => { const n = { ...prev }; delete n[a.agent_key]; return n; });
  }

  if (loading && !data) return <Spinner />;
  if (error && !data) return <Card><ErrorState what="the agents" message={error} onRetry={refetch} /></Card>;
  return (
    <div className="space-y-2" data-testid="admin-agents">
      {refused && <div className="chip bg-crit-bg text-crit" data-testid="admin-agent-refused">{refused}</div>}
      {data?.map((a) => {
        const d = drafts[a.agent_key];
        const enabled = !!value(a, "enabled");
        const paused = !!value(a, "paused");
        const cron = value(a, "schedule_cron");
        const schedOn = !!value(a, "schedule_enabled");
        return (
          <Card key={a.agent_key} className="space-y-2" data-testid="admin-agent-row">
            <div className="flex flex-wrap items-center gap-x-4 gap-y-2">
              <div className="min-w-[10rem] flex-1">
                <div className="font-medium text-ink">{a.name}</div>
                <div className="flex flex-wrap gap-1.5 text-xs text-ink-muted">
                  <span className={chip}>{a.stage}</span>
                  {a.paused_reason && <span className="chip bg-warn-bg text-warn" title={a.paused_reason}>{a.paused_reason}</span>}
                </div>
              </div>
              <select
                className="input max-w-[180px] py-1.5 text-sm"
                aria-label="Tier"
                value={value(a, "default_tier")}
                onChange={(e) => stage(a, { default_tier: e.target.value })}
              >
                {tiers.map((t) => <option key={t} value={t}>{titleCase(t)}</option>)}
              </select>
              <button
                className={`btn-outline ${enabled ? "" : "text-ink-muted"}`}
                onClick={() => stage(a, { enabled: !enabled })}
                aria-pressed={enabled}
                data-testid="admin-agent-enabled"
              >
                <Power size={15} /> {enabled ? "Enabled" : "Disabled"}
              </button>
              <button
                className={paused ? "btn-warn" : "btn-outline"}
                onClick={() => stage(a, { paused: !paused })}
                aria-pressed={paused}
                data-testid="admin-agent-pause"
              >
                {paused ? <><Play size={15} /> Resume</> : <><Pause size={15} /> Pause</>}
              </button>
            </div>
            <div className="flex flex-wrap items-center gap-x-3 gap-y-1 border-t border-border-soft pt-2">
              <Clock size={13} className="shrink-0 text-ink-muted" />
              <span className="tile-label">Schedule</span>
              {cronEdit[a.agent_key] !== undefined ? (
                <>
                  <input
                    className="input w-40 py-1 font-mono text-xs"
                    placeholder="0 7 * * 1"
                    aria-label="Cron schedule"
                    value={cronEdit[a.agent_key]}
                    onChange={(e) => setCronEdit((p) => ({ ...p, [a.agent_key]: e.target.value }))}
                  />
                  <button className="btn-outline px-2 py-1 text-xs" onClick={() => stageCron(a)}>Set</button>
                  <button className="btn-outline px-2 py-1 text-xs" onClick={() => setCronEdit((p) => { const n = { ...p }; delete n[a.agent_key]; return n; })} aria-label="Cancel"><X size={12} /></button>
                </>
              ) : (
                <>
                  <code className="font-mono text-xs text-ink">{cron || "—"}</code>
                  <button className="text-xs text-ink-muted underline hover:text-ink" onClick={() => setCronEdit((p) => ({ ...p, [a.agent_key]: cron || "" }))}>Edit</button>
                  {cron && (
                    <button
                      className={`chip ${schedOn ? "bg-ok-bg text-ok" : "bg-border-soft text-ink-muted"}`}
                      onClick={() => stage(a, { schedule_enabled: !schedOn })}
                      aria-pressed={schedOn}
                    >
                      {schedOn ? "Enabled" : "Disabled"}
                    </button>
                  )}
                </>
              )}
            </div>
            {d && (
              <div className="flex flex-wrap items-center gap-2 border-t border-border-soft pt-2" data-testid="admin-agent-pending">
                {Object.keys(d.patch).map((k) => <span key={k} className={`${chip} font-mono`}>{k}</span>)}
                <ReasonInput value={d.reason} onChange={(v) => setDrafts((p) => ({ ...p, [a.agent_key]: { ...d, reason: v } }))} />
                <button className="btn-primary px-3 py-1 text-xs" disabled={!d.reason.trim() || busy === a.agent_key} onClick={() => apply(a)} data-testid="admin-agent-apply">
                  {busy === a.agent_key ? "…" : ask ? "Ask Astra" : "Apply"}
                </button>
                <button className="btn-ghost px-2 py-1 text-xs" onClick={() => drop(a.agent_key)}>Cancel</button>
              </div>
            )}
          </Card>
        );
      })}
    </div>
  );
}

// ── Firm research ─────────────────────────────────────────────────────────────

const STATUS_CHIP: Record<string, string> = {
  draft: "bg-border-soft text-ink-muted",
  under_review: "bg-warn-bg text-warn",
  published: "bg-ok-bg text-ok",
};
const DOC_TYPES: [string, string][] = [["house_view", "House view"], ["research", "Research"], ["playbook", "Playbook"]];
const docWord = (t: string) => (DOC_TYPES.find(([k]) => k === t)?.[1] || titleCase(t)).toLowerCase();

export function ResearchTab() {
  const ask = useAsk();
  const { data, loading, refetch } = useApi<any[]>("/api/admin/research");
  const [form, setForm] = useState({ title: "", doc_type: "house_view", body: "", author: "" });
  const [busy, setBusy] = useState(false);
  const [editing, setEditing] = useState<any | null>(null);
  const [editBusy, setEditBusy] = useState(false);
  const [actionBusy, setActionBusy] = useState<string | null>(null);

  async function add() {
    if (!form.title || !form.body) return;
    if (ask) {
      // The body goes last: it may say anything.
      ask(`Add the research ${q(form.title)}, a ${docWord(form.doc_type)}${form.author.trim() ? ` by ${form.author.trim()}` : ""}, with the text: ${form.body.trim()}`);
      setForm({ title: "", doc_type: "house_view", body: "", author: "" });
      return;
    }
    setBusy(true);
    try {
      await api("/api/admin/research", { body: form });
      setForm({ title: "", doc_type: "house_view", body: "", author: "" });
      refetch();
    } finally {
      setBusy(false);
    }
  }

  async function saveEdit() {
    if (!editing) return;
    setEditBusy(true);
    try {
      await api(`/api/admin/research/${editing.id}`, { method: "PATCH", body: { title: editing.title, doc_type: editing.doc_type, author: editing.author } });
      setEditing(null);
      refetch();
    } catch (e: any) { alert(e.message); } finally { setEditBusy(false); }
  }

  async function deleteDoc(d: any) {
    if (ask) return ask(`Delete the research ${q(d.title)}`);
    await api(`/api/admin/research/${d.id}`, { method: "DELETE", body: {} });
    refetch();
  }

  async function docAction(d: any, action: "submit" | "publish" | "reject") {
    if (ask) {
      const verb = action === "submit" ? "Submit the research" : action === "publish" ? "Publish the research" : "Reject the research";
      return ask(`${verb} ${q(d.title)}${action === "submit" ? " for review" : ""}`);
    }
    setActionBusy(d.id + action);
    try { await api(`/api/admin/research/${d.id}/${action}`, { body: {} }); refetch(); }
    finally { setActionBusy(null); }
  }

  return (
    <div className="grid max-w-5xl gap-5 cq-md:grid-cols-2" data-testid="admin-research">
      <div>
        <div className="mb-3 flex items-center gap-2">
          <span className="font-semibold text-ink">Research & house views</span>
          <span className={`${chip} tabular-nums`}>{data?.length ?? 0}</span>
        </div>
        {loading && !data ? <Spinner /> : (
          <div className="space-y-2">
            {data?.map((d) => (
              <Card key={d.id} className="py-3" data-testid="admin-research-row">
                {editing?.id === d.id ? (
                  <div className="space-y-2">
                    <input className="input text-sm" value={editing.title} onChange={(e) => setEditing({ ...editing, title: e.target.value })} />
                    <div className="grid grid-cols-2 gap-2">
                      <select className="input text-xs" value={editing.doc_type} onChange={(e) => setEditing({ ...editing, doc_type: e.target.value })}>
                        {DOC_TYPES.map(([k, l]) => <option key={k} value={k}>{l}</option>)}
                      </select>
                      <input className="input text-xs" placeholder="Author" value={editing.author || ""} onChange={(e) => setEditing({ ...editing, author: e.target.value })} />
                    </div>
                    <div className="flex gap-2">
                      <button className="btn-primary px-2 py-1 text-xs" onClick={saveEdit} disabled={editBusy}>{editBusy ? "…" : "Save"}</button>
                      <button className="btn-outline px-2 py-1 text-xs" onClick={() => setEditing(null)}>Cancel</button>
                    </div>
                  </div>
                ) : (
                  <div>
                    <div className="flex items-start justify-between gap-2">
                      <div className="min-w-0">
                        <div className="text-sm font-medium text-ink">{d.title}</div>
                        <div className="mt-1 flex flex-wrap gap-1.5">
                          <span className={chip}>{titleCase(d.doc_type)}</span>
                          {d.author && <span className={chip}>{d.author}</span>}
                          {d.version > 1 && <span className={`${chip} font-mono`}>v{d.version}</span>}
                        </div>
                      </div>
                      <div className="flex shrink-0 items-center gap-1.5">
                        <span className={`chip text-[11px] ${STATUS_CHIP[d.status] || STATUS_CHIP.draft}`}>
                          {d.status === "published" ? <CheckCircle2 size={10} /> : d.status === "under_review" ? <Eye size={10} /> : null}
                          {titleCase(d.status?.replace("_", " ") || "draft")}
                        </span>
                        {ask ? (
                          <>
                            <button className="text-ink-muted opacity-50" disabled aria-label="Edit"><Pencil size={13} /></button>
                            <button className="text-ink-muted hover:text-crit" onClick={() => deleteDoc(d)} aria-label="Ask Astra to delete"><Trash2 size={13} /></button>
                          </>
                        ) : (
                          <>
                            <button onClick={() => setEditing({ ...d })} className="text-ink-muted hover:text-ink" aria-label="Edit">
                              <Pencil size={13} />
                            </button>
                            <InlineConfirmButton label={<X size={13} />} confirmLabel="Delete?" onConfirm={() => deleteDoc(d)} />
                          </>
                        )}
                      </div>
                    </div>
                    <div className="mt-2 flex flex-wrap items-center gap-1.5">
                      {(d.status === "draft" || !d.status) && (
                        <button className="btn-outline px-2 py-0.5 text-xs" disabled={actionBusy === d.id + "submit"} onClick={() => docAction(d, "submit")}>
                          <SendHorizonal size={11} /> Submit for review
                        </button>
                      )}
                      {d.status === "under_review" && (
                        <>
                          <button className="btn-primary px-2 py-0.5 text-xs" disabled={actionBusy === d.id + "publish"} onClick={() => docAction(d, "publish")}>
                            <CheckCircle2 size={11} /> Publish
                          </button>
                          <button className="btn-crit px-2 py-0.5 text-xs" disabled={actionBusy === d.id + "reject"} onClick={() => docAction(d, "reject")}>
                            <X size={11} /> Reject
                          </button>
                        </>
                      )}
                      {d.status === "published" && d.published_by && (
                        <span className={chip}>Published by {d.published_by}</span>
                      )}
                      {ask && <PageOnly label="edit on the page" />}
                    </div>
                  </div>
                )}
              </Card>
            ))}
            {!data?.length && <Card><Empty>No research</Empty></Card>}
          </div>
        )}
      </div>
      <Card>
        <div className="mb-3 flex items-center gap-2 font-semibold text-ink"><Plus size={16} /> Add research</div>
        <div className="space-y-3">
          <div><label className="label">Title</label><input className="input" value={form.title} onChange={(e) => setForm({ ...form, title: e.target.value })} /></div>
          <div className="grid grid-cols-2 gap-3">
            <div><label className="label">Type</label>
              <select className="input" value={form.doc_type} onChange={(e) => setForm({ ...form, doc_type: e.target.value })}>
                {DOC_TYPES.map(([k, l]) => <option key={k} value={k}>{l}</option>)}
              </select>
            </div>
            <div><label className="label">Author</label><input className="input" value={form.author} onChange={(e) => setForm({ ...form, author: e.target.value })} /></div>
          </div>
          <div><label className="label">Body</label><textarea className="input" rows={5} value={form.body} onChange={(e) => setForm({ ...form, body: e.target.value })} /></div>
          <button className="btn-primary" disabled={busy || !form.title || !form.body} onClick={add} data-testid="admin-research-add">
            {busy ? "Ingesting…" : ask ? "Ask Astra" : "Add & embed"}
          </button>
        </div>
      </Card>
    </div>
  );
}

// ── Models ────────────────────────────────────────────────────────────────────

export function ModelsTab() {
  const ask = useAsk();
  const { data, loading, error, refetch } = useApi<any>("/api/admin/firm");
  const [form, setForm] = useState<any>(null);
  const [saved, setSaved] = useState(false);
  const [reason, setReason] = useState("");
  const [apiKey, setApiKey] = useState("");
  const [keyBusy, setKeyBusy] = useState<string | null>(null);
  const [test, setTest] = useState<any>(null);
  if (loading && !data) return <Spinner />;
  // `useApi` returns data:null with loading:false on failure — without this guard the
  // `data.llm` below throws and white-screens the whole /admin route.
  if (error || !data) return <Card><ErrorState what="model settings" message={error} onRetry={refetch} /></Card>;
  const llm = data.llm || {};
  const stored = data.model_config || {};
  const cfg = form || { ...stored };
  const tasks = [
    { k: "advice", label: "Advice & reasoning", def: llm.model_defaults?.advice },
    { k: "narrative", label: "Client narrative", def: llm.model_defaults?.narrative },
    { k: "classify", label: "Classification", def: llm.model_defaults?.classify },
  ];
  const moved = tasks.filter((t) => (cfg[t.k] || "").trim() !== (stored[t.k] || "").trim());
  const routing = moved.length
    ? `Change the model routing: ${moved.map((t) => `${t.k} to ${(cfg[t.k] || "").trim() || "the platform default"}`).join("; ")}`
    : null;

  async function saveModels() {
    if (ask) {
      if (routing) ask(routing + because(reason));
      setForm(null); setReason("");
      return;
    }
    await api("/api/admin/firm", { method: "PATCH", body: { model_config_json: cfg } });
    setSaved(true); setTimeout(() => setSaved(false), 1500); refetch();
  }
  async function saveKey() {
    setKeyBusy("save"); setTest(null);
    try { await api("/api/admin/llm", { method: "PUT", body: { anthropic_api_key: apiKey } }); setApiKey(""); refetch(); }
    finally { setKeyBusy(null); }
  }
  async function clearKey() {
    setKeyBusy("clear"); setTest(null);
    try { await api("/api/admin/llm", { method: "PUT", body: { anthropic_api_key: "" } }); refetch(); }
    finally { setKeyBusy(null); }
  }
  async function runTest() {
    setKeyBusy("test"); setTest(null);
    try { setTest(await api("/api/admin/llm/test", { body: {} })); } finally { setKeyBusy(null); }
  }

  return (
    <div className="max-w-2xl space-y-5" data-testid="admin-models">
      <Card>
        <div className="mb-2 font-semibold text-ink">LLM provider</div>
        <div className="mb-3 flex flex-wrap items-center gap-1.5" data-testid="admin-llm-status">
          <span className={`chip ${llm.enabled ? "bg-ok-bg text-ok" : "bg-warn-bg text-warn"}`}>
            {llm.enabled ? "Connected" : "Not connected · rule-based text"}
          </span>
          {llm.anthropic_configured && <span className="chip bg-ok-bg text-ok">Anthropic key set</span>}
          {llm.anthropic_from_env && <span className={chip}>from environment</span>}
          {ask && <PageOnly label="key on the page" />}
        </div>
        {!ask && (
          <>
            <label className="label">Anthropic API key</label>
            <div className="flex flex-wrap gap-2">
              <input className="input min-w-0 flex-1 font-mono" type="password" placeholder={llm.anthropic_configured ? "•••••••• (set)" : "sk-ant-..."}
                     value={apiKey} onChange={(e) => setApiKey(e.target.value)} aria-label="Anthropic API key" />
              <button className="btn-primary" disabled={!apiKey.trim() || keyBusy === "save"} onClick={saveKey}>
                {keyBusy === "save" ? "Saving…" : "Save key"}
              </button>
            </div>
            <div className="mt-3 flex flex-wrap items-center gap-2">
              <button className="btn-outline text-sm" disabled={!llm.enabled || keyBusy === "test"} onClick={runTest}>
                {keyBusy === "test" ? "Testing…" : "Test connection"}
              </button>
              {llm.anthropic_configured && (
                <button className="btn-ghost text-sm text-crit" disabled={keyBusy === "clear"} onClick={clearKey}>Remove key</button>
              )}
              {test && (
                <span className={`chip ${test.ok ? "bg-ok-bg text-ok" : "bg-crit-bg text-crit"}`}>
                  {test.ok ? `${test.provider} · ${test.model} · “${test.reply}”` : test.message}
                </span>
              )}
            </div>
            <div className="mt-3 flex flex-wrap gap-1.5">
              <span className={chip}>stored against the firm</span>
              <span className={chip}>never returned by the API</span>
            </div>
          </>
        )}
      </Card>

      <Card>
        <div className="mb-3 font-semibold text-ink">Model per task</div>
        <div className="space-y-3">
          {tasks.map((t) => (
            <div key={t.k}>
              <label className="label">{t.label}</label>
              <input className="input font-mono text-sm" placeholder={t.def} value={cfg[t.k] || ""} data-testid={`admin-model-${t.k}`}
                     onChange={(e) => setForm({ ...cfg, [t.k]: e.target.value })} />
            </div>
          ))}
        </div>
        <div className="mt-4 flex flex-wrap items-center gap-2">
          {ask && <ReasonInput value={reason} onChange={setReason} />}
          <button className="btn-primary" onClick={saveModels} disabled={!!ask && (!routing || !reason.trim())} data-testid="admin-models-save">
            <Save size={16} /> {ask ? "Ask Astra" : "Save models"}
          </button>
          <span className={chip}>blank · platform default</span>
          {saved && <Saved />}
        </div>
      </Card>
    </div>
  );
}

// ── AI usage (a read) ─────────────────────────────────────────────────────────

export function UsageTab() {
  const { data: usage, loading, error, refetch } = useApi<any>("/api/admin/usage");

  if (loading && !usage) return <Spinner />;
  if (error && !usage) return <Card><ErrorState what="usage" message={error} onRetry={refetch} /></Card>;
  if (!usage) return <Card><Empty>No usage</Empty></Card>;

  const byAgent = (Object.entries(usage.by_agent || {}) as [string, number][]).sort((a, b) => b[1] - a[1]);
  const byModel = (Object.entries(usage.by_model || {}) as [string, number][]).sort((a, b) => b[1] - a[1]);
  const maxAgent = byAgent[0]?.[1] || 1;
  const maxModel = byModel[0]?.[1] || 1;

  return (
    <div className="max-w-4xl space-y-5" data-testid="admin-usage">
      <div className="grid grid-cols-2 gap-3 cq-sm:grid-cols-3 cq-lg:grid-cols-5">
        {[
          { label: "API calls", value: (usage.calls ?? 0).toLocaleString() },
          { label: "Total tokens", value: ((usage.total_tokens ?? 0) / 1000).toFixed(1) + "k" },
          { label: "Est. cost", value: money(usage.est_cost ?? 0, "USD", 2) },
          { label: "Fallback rate", value: `${((usage.fallback_rate ?? 0) * 100).toFixed(1)}%` },
          { label: "PII redacted", value: (usage.redacted_entities ?? 0).toLocaleString() },
        ].map(({ label, value }) => (
          <Card key={label} className="p-4">
            <div className="tile-label">{label}</div>
            <div className="mt-1 font-mono text-xl tabular-nums text-ink">{value}</div>
          </Card>
        ))}
      </div>
      <div className="grid gap-5 cq-sm:grid-cols-2">
        {([["Calls by agent", byAgent.slice(0, 10), maxAgent, false], ["Calls by model", byModel, maxModel, true]] as [string, [string, number][], number, boolean][]).map(([title, rows, max, mono]) => (
          <Card key={title}>
            <div className="mb-3 text-sm font-semibold text-ink">{title}</div>
            {rows.length === 0 ? <Empty>None</Empty> : (
              <div className="space-y-2.5">
                {rows.map(([name, count]) => (
                  <div key={name}>
                    <div className="mb-1 flex justify-between text-xs text-ink">
                      <span className={`truncate ${mono ? "font-mono text-[11px]" : "capitalize"}`}>{mono ? name : name.replace(/_/g, " ")}</span>
                      <span className="ml-2 shrink-0 font-mono tabular-nums">{count}</span>
                    </div>
                    <div className="h-1.5 rounded-full bg-border-soft">
                      <div className="h-1.5 rounded-full bg-ink-muted transition-all" style={{ width: `${(count / max) * 100}%` }} />
                    </div>
                  </div>
                ))}
              </div>
            )}
          </Card>
        ))}
      </div>
      <div className="flex flex-wrap items-center justify-between gap-2">
        <span className={chip}>cost estimated from published pricing</span>
        <button className="btn-outline px-2 py-1 text-xs" onClick={() => refetch()}><RefreshCw size={11} /> Refresh</button>
      </div>
    </div>
  );
}
