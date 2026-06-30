"use client";
import { useState } from "react";
import { Save, Power, Pause, Play, Plus, BookOpen, Bot, Building, Cpu, Users, Copy, Check, UserPlus, KeyRound, UserX, UserCheck, X, ChevronDown, ClipboardList, UserSquare2, PlugZap, RefreshCw, AlertCircle, CheckCircle2, CircleDot, Wifi, WifiOff, BarChart2, ShieldAlert, FileText, ChevronRight, Pencil, Database, Clock, Layers, Link2, Tag, Trash2, Bell, Target, SendHorizonal, Eye, Paintbrush, Search } from "lucide-react";
import PageHeader from "@/components/PageHeader";
import { Card, Spinner, TierBadge, InlineConfirmButton, Empty } from "@/components/ui";
import { useApi } from "@/lib/hooks";
import { api } from "@/lib/api";
import { titleCase } from "@/lib/format";

const TABS = [
  { id: "firm", label: "Firm & branding", icon: Building },
  { id: "users", label: "Users & access", icon: Users },
  { id: "clients", label: "Clients", icon: UserSquare2 },
  { id: "assignments", label: "Assignments", icon: Link2 },
  { id: "segments", label: "Segments", icon: Layers },
  { id: "mandates", label: "Mandates", icon: FileText },
  { id: "model-portfolios", label: "Model Portfolios", icon: Target },
  { id: "connectors", label: "Connectors", icon: PlugZap },
  { id: "compliance", label: "Compliance", icon: ShieldAlert },
  { id: "rule-impact", label: "Rule Impact", icon: Search },
  { id: "branding", label: "Branding", icon: Paintbrush },
  { id: "data-quality", label: "Data Quality", icon: Database },
  { id: "schedules", label: "Schedules", icon: Clock },
  { id: "notifications", label: "Notifications", icon: Bell },
  { id: "audit", label: "Audit trail", icon: ClipboardList },
  { id: "usage", label: "AI Usage", icon: BarChart2 },
  { id: "agents", label: "Agents & autonomy", icon: Bot },
  { id: "research", label: "Firm research", icon: BookOpen },
  { id: "models", label: "Models", icon: Cpu },
];

export default function Admin() {
  const [tab, setTab] = useState("firm");
  return (
    <div>
      <PageHeader
        title="Configuration"
        sub="Firm details, branding, agent autonomy, models and research."
      />
      <div className="flex gap-1 mb-6 border-b border-navy-100">
        {TABS.map((t) => {
          const Icon = t.icon;
          return (
            <button
              key={t.id}
              onClick={() => setTab(t.id)}
              className={`flex items-center gap-2 px-4 py-2.5 text-sm font-medium border-b-2 -mb-px transition ${
                tab === t.id ? "border-navy-800 text-navy-800" : "border-transparent text-ink-muted hover:text-ink"
              }`}
            >
              <Icon size={16} /> {t.label}
            </button>
          );
        })}
      </div>
      {tab === "firm" && <FirmTab />}
      {tab === "users" && <UsersTab />}
      {tab === "clients" && <ClientsTab />}
      {tab === "assignments" && <AssignmentsTab />}
      {tab === "segments" && <SegmentsTab />}
      {tab === "mandates" && <MandatesTab />}
      {tab === "model-portfolios" && <ModelPortfoliosTab />}
      {tab === "connectors" && <ConnectorsTab />}
      {tab === "compliance" && <ComplianceTab />}
      {tab === "rule-impact" && <RuleImpactTab />}
      {tab === "branding" && <BrandingTab />}
      {tab === "data-quality" && <DataQualityTab />}
      {tab === "schedules" && <SchedulesTab />}
      {tab === "notifications" && <NotificationsTab />}
      {tab === "audit" && <AuditTab />}
      {tab === "usage" && <UsageTab />}
      {tab === "agents" && <AgentsTab />}
      {tab === "research" && <ResearchTab />}
      {tab === "models" && <ModelsTab />}
    </div>
  );
}

function FirmTab() {
  const { data, loading, refetch } = useApi<any>("/api/admin/firm");
  const [form, setForm] = useState<any>(null);
  const [saved, setSaved] = useState(false);
  if (loading || !data) return <Spinner />;
  const f = form || {
    name: data.name, jurisdiction: data.jurisdiction, regulator: data.regulator,
    branding: { ...data.branding }, settings: { ...data.settings },
  };
  const set = (patch: any) => setForm({ ...f, ...patch });
  const setBrand = (patch: any) => setForm({ ...f, branding: { ...f.branding, ...patch } });

  async function save() {
    await api("/api/admin/firm", { method: "PATCH", body: f });
    setSaved(true);
    setTimeout(() => setSaved(false), 1500);
    refetch();
  }

  return (
    <div className="grid md:grid-cols-2 gap-5 max-w-4xl">
      <Card>
        <div className="font-semibold text-ink mb-3">Firm</div>
        <div className="space-y-3">
          <div><label className="label">Name</label><input className="input" value={f.name} onChange={(e) => set({ name: e.target.value })} /></div>
          <div className="grid grid-cols-2 gap-3">
            <div><label className="label">Jurisdiction</label><input className="input" value={f.jurisdiction} onChange={(e) => set({ jurisdiction: e.target.value })} /></div>
            <div><label className="label">Regulator</label><input className="input" value={f.regulator || ""} onChange={(e) => set({ regulator: e.target.value })} /></div>
          </div>
          <div><label className="label">AI usage policy</label>
            <textarea className="input" rows={3} value={f.settings?.ai_usage_policy || ""} onChange={(e) => setForm({ ...f, settings: { ...f.settings, ai_usage_policy: e.target.value } })} />
          </div>
        </div>
      </Card>
      <Card>
        <div className="font-semibold text-ink mb-3">Branding (Studio & Canvas)</div>
        <div className="space-y-3">
          <div><label className="label">Logo text</label><input className="input" value={f.branding?.logo_text || ""} onChange={(e) => setBrand({ logo_text: e.target.value })} /></div>
          <div><label className="label">Tagline</label><input className="input" value={f.branding?.tagline || ""} onChange={(e) => setBrand({ tagline: e.target.value })} /></div>
          <div className="grid grid-cols-2 gap-3">
            <div><label className="label">Primary</label><input className="input" type="color" value={f.branding?.primary || "#163a52"} onChange={(e) => setBrand({ primary: e.target.value })} /></div>
            <div><label className="label">Accent</label><input className="input" type="color" value={f.branding?.accent || "#c8a35e"} onChange={(e) => setBrand({ accent: e.target.value })} /></div>
          </div>
        </div>
      </Card>
      <div className="md:col-span-2 flex items-center gap-3">
        <button className="btn-primary" onClick={save}><Save size={16} /> Save changes</button>
        {saved && <span className="text-sm text-positive">Saved · refresh to see branding update.</span>}
      </div>
    </div>
  );
}

function AgentsTab() {
  const { data, loading, refetch } = useApi<any[]>("/api/agents/catalogue");
  const [cronEdit, setCronEdit] = useState<Record<string, string>>({});
  const tiers = ["tier_1", "tier_2", "tier_3"];

  async function update(key: string, patch: any) {
    await api(`/api/admin/agents/${key}`, { method: "PATCH", body: patch });
    refetch();
  }

  async function saveCron(key: string) {
    const cron = cronEdit[key] ?? "";
    await api(`/api/admin/agents/${key}`, { method: "PATCH", body: { schedule_cron: cron || null } });
    setCronEdit((prev) => { const n = { ...prev }; delete n[key]; return n; });
    refetch();
  }

  if (loading) return <Spinner />;
  return (
    <div className="space-y-2 max-w-5xl">
      {data?.map((a) => (
        <Card key={a.agent_key} className="space-y-2">
          <div className="flex items-center gap-4">
            <div className="flex-1">
              <div className="font-medium text-ink">{a.name}</div>
              <div className="text-xs text-ink-muted">{a.stage}{a.paused_reason ? ` · ${a.paused_reason}` : ""}</div>
            </div>
            <select
              className="input max-w-[180px] py-1.5 text-sm"
              value={a.tier}
              onChange={(e) => update(a.agent_key, { default_tier: e.target.value })}
            >
              {tiers.map((t) => <option key={t} value={t}>{titleCase(t)}</option>)}
            </select>
            <button
              className={`btn ${a.enabled ? "btn-outline" : "bg-navy-100 text-ink-muted"}`}
              onClick={() => update(a.agent_key, { enabled: !a.enabled })}
              title="Enable / disable"
            >
              <Power size={15} /> {a.enabled ? "Enabled" : "Disabled"}
            </button>
            <button
              className={a.paused ? "btn-gold" : "btn-outline"}
              onClick={() => update(a.agent_key, { paused: !a.paused, paused_reason: a.paused ? null : "Paused by admin (kill-switch)." })}
              title="Kill-switch"
            >
              {a.paused ? <><Play size={15} /> Resume</> : <><Pause size={15} /> Pause</>}
            </button>
          </div>
          {/* Cron scheduler row */}
          <div className="flex items-center gap-3 pt-1 border-t border-navy-50">
            <Clock size={13} className="text-ink-muted shrink-0" />
            <span className="text-xs text-ink-muted">Cron schedule:</span>
            {cronEdit[a.agent_key] !== undefined ? (
              <>
                <input
                  className="input py-1 text-xs font-mono w-40"
                  placeholder="0 7 * * 1"
                  value={cronEdit[a.agent_key]}
                  onChange={(e) => setCronEdit((p) => ({ ...p, [a.agent_key]: e.target.value }))}
                />
                <button className="btn-primary text-xs py-1 px-2" onClick={() => saveCron(a.agent_key)}>Save</button>
                <button className="btn-outline text-xs py-1 px-2" onClick={() => setCronEdit((p) => { const n = { ...p }; delete n[a.agent_key]; return n; })}>Cancel</button>
              </>
            ) : (
              <>
                <code className="text-xs font-mono text-ink">{a.schedule_cron || "—"}</code>
                <button className="text-xs text-ink-muted hover:text-ink underline" onClick={() => setCronEdit((p) => ({ ...p, [a.agent_key]: a.schedule_cron || "" }))}>Edit</button>
                {a.schedule_cron && (
                  <button
                    className={`text-xs px-2 py-0.5 rounded ${a.schedule_enabled ? "bg-positive/10 text-positive" : "bg-navy-100 text-ink-muted"}`}
                    onClick={() => update(a.agent_key, { schedule_enabled: !a.schedule_enabled })}
                  >
                    {a.schedule_enabled ? "Enabled" : "Disabled"}
                  </button>
                )}
              </>
            )}
          </div>
        </Card>
      ))}
    </div>
  );
}

const STATUS_CHIP: Record<string, string> = {
  draft: "bg-navy-50 text-ink-muted",
  under_review: "bg-caution/10 text-caution",
  published: "bg-positive/10 text-positive",
};

function ResearchTab() {
  const { data, loading, refetch } = useApi<any[]>("/api/admin/research");
  const [form, setForm] = useState({ title: "", doc_type: "house_view", body: "", author: "" });
  const [busy, setBusy] = useState(false);
  const [editing, setEditing] = useState<any | null>(null);
  const [editBusy, setEditBusy] = useState(false);
  const [actionBusy, setActionBusy] = useState<string | null>(null);

  async function add() {
    if (!form.title || !form.body) return;
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

  async function deleteDoc(id: string) {
    await api(`/api/admin/research/${id}`, { method: "DELETE", body: {} });
    refetch();
  }

  async function docAction(id: string, action: "submit" | "publish" | "reject") {
    setActionBusy(id + action);
    try { await api(`/api/admin/research/${id}/${action}`, { body: {} }); refetch(); }
    finally { setActionBusy(null); }
  }

  return (
    <div className="grid md:grid-cols-2 gap-5 max-w-4xl">
      <div>
        <div className="font-semibold text-ink mb-3">Firm research & house views</div>
        {loading ? <Spinner /> : (
          <div className="space-y-2">
            {data?.map((d) => (
              <Card key={d.id} className="py-3">
                {editing?.id === d.id ? (
                  <div className="space-y-2">
                    <input className="input text-sm" value={editing.title} onChange={(e) => setEditing({ ...editing, title: e.target.value })} />
                    <div className="grid grid-cols-2 gap-2">
                      <select className="input text-xs" value={editing.doc_type} onChange={(e) => setEditing({ ...editing, doc_type: e.target.value })}>
                        <option value="house_view">House view</option>
                        <option value="research">Research</option>
                        <option value="playbook">Playbook</option>
                      </select>
                      <input className="input text-xs" placeholder="Author" value={editing.author || ""} onChange={(e) => setEditing({ ...editing, author: e.target.value })} />
                    </div>
                    <div className="flex gap-2">
                      <button className="btn-primary text-xs py-1 px-2" onClick={saveEdit} disabled={editBusy}>{editBusy ? "…" : "Save"}</button>
                      <button className="btn-outline text-xs py-1 px-2" onClick={() => setEditing(null)}>Cancel</button>
                    </div>
                  </div>
                ) : (
                  <div>
                    <div className="flex items-start justify-between gap-2">
                      <div>
                        <div className="font-medium text-ink text-sm">{d.title}</div>
                        <div className="text-xs text-ink-muted mt-0.5">
                          {titleCase(d.doc_type)}{d.author ? ` · ${d.author}` : ""}
                          {d.version > 1 ? ` · v${d.version}` : ""}
                        </div>
                      </div>
                      <div className="flex items-center gap-1.5 shrink-0">
                        <span className={`chip text-[11px] ${STATUS_CHIP[d.status] || STATUS_CHIP.draft}`}>
                          {d.status === "published" ? <CheckCircle2 size={10} /> : d.status === "under_review" ? <Eye size={10} /> : null}
                          {titleCase(d.status?.replace("_", " ") || "draft")}
                        </span>
                        <button onClick={() => setEditing({ ...d })} className="text-navy-400 hover:text-navy-600" title="Edit">
                          <Pencil size={13} />
                        </button>
                        <InlineConfirmButton
                          label={<X size={13} />}
                          confirmLabel="Delete?"
                          onConfirm={() => deleteDoc(d.id)}
                          className="btn-outline py-0.5 px-1 text-xs text-red-500"
                        />
                      </div>
                    </div>
                    {/* Publishing workflow actions */}
                    <div className="mt-2 flex items-center gap-1.5">
                      {(d.status === "draft" || !d.status) && (
                        <button className="btn-outline text-xs py-0.5 px-2" disabled={actionBusy === d.id + "submit"} onClick={() => docAction(d.id, "submit")}>
                          <SendHorizonal size={11} /> Submit for review
                        </button>
                      )}
                      {d.status === "under_review" && (
                        <>
                          <button className="btn-primary text-xs py-0.5 px-2" disabled={actionBusy === d.id + "publish"} onClick={() => docAction(d.id, "publish")}>
                            <CheckCircle2 size={11} /> Publish
                          </button>
                          <button className="btn-outline text-xs py-0.5 px-2 text-critical border-critical/30" disabled={actionBusy === d.id + "reject"} onClick={() => docAction(d.id, "reject")}>
                            <X size={11} /> Reject
                          </button>
                        </>
                      )}
                      {d.status === "published" && d.published_by && (
                        <span className="text-[11px] text-ink-muted">Published by {d.published_by}</span>
                      )}
                    </div>
                  </div>
                )}
              </Card>
            ))}
            {!data?.length && <div className="text-sm text-ink-muted">No research yet.</div>}
          </div>
        )}
      </div>
      <Card>
        <div className="font-semibold text-ink mb-3 flex items-center gap-2"><Plus size={16} /> Add research</div>
        <div className="space-y-3">
          <div><label className="label">Title</label><input className="input" value={form.title} onChange={(e) => setForm({ ...form, title: e.target.value })} /></div>
          <div className="grid grid-cols-2 gap-3">
            <div><label className="label">Type</label>
              <select className="input" value={form.doc_type} onChange={(e) => setForm({ ...form, doc_type: e.target.value })}>
                <option value="house_view">House view</option>
                <option value="research">Research</option>
                <option value="playbook">Playbook</option>
              </select>
            </div>
            <div><label className="label">Author</label><input className="input" value={form.author} onChange={(e) => setForm({ ...form, author: e.target.value })} /></div>
          </div>
          <div><label className="label">Body</label><textarea className="input" rows={5} value={form.body} onChange={(e) => setForm({ ...form, body: e.target.value })} placeholder="The agents will reason over — and cite — this content." /></div>
          <button className="btn-primary" disabled={busy} onClick={add}>{busy ? "Ingesting…" : "Add & embed"}</button>
        </div>
      </Card>
    </div>
  );
}

const ASSET_CLASSES = ["equity", "fixed_income", "alternatives", "cash", "property", "infrastructure", "commodities"];

function ModelPortfoliosTab() {
  const { data, loading, refetch } = useApi<any[]>("/api/admin/model-portfolios");
  const [creating, setCreating] = useState(false);
  const [newForm, setNewForm] = useState({ name: "", description: "", drift_band: "5" });
  const [editing, setEditing] = useState<any | null>(null);
  const [weights, setWeights] = useState<Record<string, string>>({});
  const [busy, setBusy] = useState(false);

  async function create() {
    setBusy(true);
    try {
      await api("/api/admin/model-portfolios", { body: { name: newForm.name, description: newForm.description, drift_band: Number(newForm.drift_band) / 100 } });
      setNewForm({ name: "", description: "", drift_band: "5" }); setCreating(false); refetch();
    } finally { setBusy(false); }
  }

  async function saveTargets(id: string) {
    const targets = Object.entries(weights)
      .filter(([, v]) => v && Number(v) > 0)
      .map(([asset_class, v]) => ({ asset_class, target_weight: Number(v) / 100 }));
    setBusy(true);
    try {
      await api(`/api/admin/model-portfolios/${id}/targets`, { method: "PUT", body: targets });
      setEditing(null); setWeights({}); refetch();
    } catch (e: any) { alert(e.message); } finally { setBusy(false); }
  }

  async function deleteModel(id: string) {
    await api(`/api/admin/model-portfolios/${id}`, { method: "DELETE", body: {} });
    refetch();
  }

  const total = Object.values(weights).reduce((s, v) => s + (Number(v) || 0), 0);

  return (
    <div className="max-w-4xl space-y-4">
      <div className="flex items-center justify-between">
        <div className="font-semibold text-ink">Model portfolios</div>
        {!creating && (
          <button className="btn-outline text-sm" onClick={() => setCreating(true)}><Plus size={15} /> New model</button>
        )}
      </div>

      {creating && (
        <Card>
          <div className="font-semibold text-ink mb-3">New model portfolio</div>
          <div className="space-y-3">
            <div><label className="label">Name</label><input className="input" value={newForm.name} onChange={(e) => setNewForm({ ...newForm, name: e.target.value })} placeholder="e.g. Conservative Growth" /></div>
            <div><label className="label">Description</label><input className="input" value={newForm.description} onChange={(e) => setNewForm({ ...newForm, description: e.target.value })} placeholder="Optional" /></div>
            <div><label className="label">Drift band %</label><input className="input w-32" type="number" value={newForm.drift_band} onChange={(e) => setNewForm({ ...newForm, drift_band: e.target.value })} /></div>
            <div className="flex gap-2">
              <button className="btn-primary" disabled={busy || !newForm.name.trim()} onClick={create}>{busy ? "Creating…" : "Create"}</button>
              <button className="btn-ghost" onClick={() => setCreating(false)}>Cancel</button>
            </div>
          </div>
        </Card>
      )}

      {loading ? <Spinner /> : !data?.length ? (
        <Card><div className="text-sm text-ink-muted">No model portfolios yet. Create one to define target asset class weights for rebalancing.</div></Card>
      ) : (
        <div className="space-y-3">
          {data.map((mp) => (
            <Card key={mp.id}>
              <div className="flex items-center justify-between mb-2">
                <div>
                  <div className="font-medium text-ink">{mp.name}</div>
                  {mp.description && <div className="text-xs text-ink-muted mt-0.5">{mp.description}</div>}
                  <div className="text-xs text-ink-muted mt-0.5">Drift band: {(mp.drift_band * 100).toFixed(1)}%</div>
                </div>
                <div className="flex items-center gap-2">
                  <button className="btn-outline text-xs py-0.5 px-2" onClick={() => { setEditing(mp.id); const w: Record<string, string> = {}; (mp.targets || []).forEach((t: any) => { w[t.asset_class] = String(Math.round(t.target_weight * 100)); }); setWeights(w); }}>
                    <Pencil size={12} /> Edit weights
                  </button>
                  <InlineConfirmButton label={<X size={13} />} confirmLabel="Delete?" onConfirm={() => deleteModel(mp.id)} className="btn-outline py-0.5 px-1 text-xs text-red-500" />
                </div>
              </div>

              {/* Target weights display */}
              {editing !== mp.id && mp.targets?.length > 0 && (
                <div className="grid grid-cols-4 gap-1.5 mt-2">
                  {mp.targets.map((t: any) => (
                    <div key={t.asset_class} className="rounded-lg border border-navy-100 p-2 text-center">
                      <div className="text-[11px] text-ink-muted">{titleCase(t.asset_class)}</div>
                      <div className="text-sm font-semibold text-ink">{(t.target_weight * 100).toFixed(0)}%</div>
                    </div>
                  ))}
                </div>
              )}

              {/* Weight editor */}
              {editing === mp.id && (
                <div className="mt-3 border-t border-navy-100 pt-3 space-y-3">
                  <div className="grid grid-cols-4 gap-2">
                    {ASSET_CLASSES.map((ac) => (
                      <div key={ac}>
                        <label className="label text-[11px]">{titleCase(ac)} %</label>
                        <input className="input text-sm" type="number" min="0" max="100" value={weights[ac] || ""} onChange={(e) => setWeights({ ...weights, [ac]: e.target.value })} placeholder="0" />
                      </div>
                    ))}
                  </div>
                  <div className={`text-sm ${Math.abs(total - 100) < 0.5 ? "text-positive" : "text-caution"}`}>
                    Total: {total.toFixed(0)}% {Math.abs(total - 100) < 0.5 ? "✓" : "(must sum to 100%)"}
                  </div>
                  <div className="flex gap-2">
                    <button className="btn-primary text-sm" disabled={busy || Math.abs(total - 100) > 0.5} onClick={() => saveTargets(mp.id)}>{busy ? "Saving…" : "Save weights"}</button>
                    <button className="btn-ghost text-sm" onClick={() => { setEditing(null); setWeights({}); }}>Cancel</button>
                  </div>
                </div>
              )}
            </Card>
          ))}
        </div>
      )}
    </div>
  );
}

function ModelsTab() {
  const { data, loading, refetch } = useApi<any>("/api/admin/firm");
  const [form, setForm] = useState<any>(null);
  const [saved, setSaved] = useState(false);
  const [apiKey, setApiKey] = useState("");
  const [keyBusy, setKeyBusy] = useState<string | null>(null);
  const [test, setTest] = useState<any>(null);
  if (loading) return <Spinner />;
  const llm = data.llm || {};
  const cfg = form || { ...(data.model_config || {}) };
  const tasks = [
    { k: "advice", label: "Advice & reasoning", def: llm.model_defaults?.advice },
    { k: "narrative", label: "Client narrative", def: llm.model_defaults?.narrative },
    { k: "classify", label: "Classification (cheap)", def: llm.model_defaults?.classify },
  ];
  async function saveModels() {
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
    <div className="max-w-2xl space-y-5">
      {/* LLM provider */}
      <Card>
        <div className="font-semibold text-ink mb-1">LLM provider</div>
        <div className="text-sm mb-3 flex items-center gap-2">
          <span className={`h-2 w-2 rounded-full ${llm.enabled ? "bg-positive" : "bg-caution"}`} />
          <span className={llm.enabled ? "text-positive" : "text-caution"}>
            {llm.enabled ? "Connected — agents use live Claude reasoning" : "Not connected — running deterministic fallbacks"}
          </span>
          {llm.anthropic_configured && <span className="chip bg-positive/10 text-positive">Anthropic key set</span>}
          {llm.anthropic_from_env && <span className="chip bg-navy-50 text-ink-muted">from environment</span>}
        </div>
        <label className="label">Anthropic API key</label>
        <div className="flex gap-2">
          <input className="input font-mono" type="password" placeholder={llm.anthropic_configured ? "•••••••• (set — enter a new key to replace)" : "sk-ant-..."}
                 value={apiKey} onChange={(e) => setApiKey(e.target.value)} />
          <button className="btn-primary" disabled={!apiKey.trim() || keyBusy === "save"} onClick={saveKey}>
            {keyBusy === "save" ? "Saving…" : "Save key"}
          </button>
        </div>
        <div className="flex items-center gap-2 mt-3">
          <button className="btn-outline text-sm" disabled={!llm.enabled || keyBusy === "test"} onClick={runTest}>
            {keyBusy === "test" ? "Testing…" : "Test connection"}
          </button>
          {llm.anthropic_configured && (
            <button className="btn-ghost text-sm text-critical" disabled={keyBusy === "clear"} onClick={clearKey}>Remove key</button>
          )}
          {test && (
            <span className={`text-sm flex items-center gap-1 ${test.ok ? "text-positive" : "text-critical"}`}>
              {test.ok ? `✓ ${test.provider} · ${test.model} replied “${test.reply}”` : `✗ ${test.message}`}
            </span>
          )}
        </div>
        <p className="text-xs text-ink-muted mt-3">
          The key is stored against your firm and never returned by the API. It powers every rationale,
          report, and the client assistant. Anthropic-first; an OpenAI key can be added the same way.
        </p>
      </Card>

      {/* Model selection */}
      <Card>
        <div className="font-semibold text-ink mb-1">Model selection per task</div>
        <p className="text-sm text-ink-muted mb-4">Leave blank to use the platform default.</p>
        <div className="space-y-3">
          {tasks.map((t) => (
            <div key={t.k}>
              <label className="label">{t.label}</label>
              <input className="input" placeholder={t.def} value={cfg[t.k] || ""}
                     onChange={(e) => setForm({ ...cfg, [t.k]: e.target.value })} />
            </div>
          ))}
        </div>
        <div className="flex items-center gap-3 mt-4">
          <button className="btn-primary" onClick={saveModels}><Save size={16} /> Save models</button>
          {saved && <span className="text-sm text-positive">Saved.</span>}
        </div>
      </Card>
    </div>
  );
}

// ── Users & access tab ────────────────────────────────────────────────────────

const ROLE_LABELS: Record<string, string> = {
  admin: "Admin", adviser: "Adviser", paraplanner: "Paraplanner",
  portfolio_team: "Portfolio", research_cio: "Research/CIO",
  compliance: "Compliance", operations: "Operations", branch_leader: "Branch Leader",
};
const ROLE_COLORS: Record<string, string> = {
  admin: "bg-amber-100 text-amber-800",
  compliance: "bg-blue-100 text-blue-800",
  operations: "bg-purple-100 text-purple-800",
};
function RoleBadge({ role }: { role: string }) {
  const cls = ROLE_COLORS[role] || "bg-navy-100 text-navy-700";
  return <span className={`inline-flex px-2 py-0.5 rounded-full text-[11px] font-medium ${cls}`}>{ROLE_LABELS[role] || titleCase(role)}</span>;
}

function CopyButton({ value }: { value: string }) {
  const [copied, setCopied] = useState(false);
  function copy() {
    navigator.clipboard.writeText(value).then(() => { setCopied(true); setTimeout(() => setCopied(false), 2000); });
  }
  return (
    <button onClick={copy} className="btn-outline py-1 px-2 text-xs flex items-center gap-1">
      {copied ? <><Check size={13} className="text-positive" /> Copied</> : <><Copy size={13} /> Copy link</>}
    </button>
  );
}

function UsersTab() {
  const { data: users, loading, refetch } = useApi<any[]>("/api/admin/users");
  const [showForm, setShowForm] = useState(false);
  const [editTarget, setEditTarget] = useState<any>(null);
  const [tokenResult, setTokenResult] = useState<{ name: string; token: string; type: "invite" | "reset" } | null>(null);
  const [form, setForm] = useState({ email: "", full_name: "", role: "adviser", title: "" });
  const [saving, setSaving] = useState(false);
  const [err, setErr] = useState("");

  const origin = typeof window !== "undefined" ? window.location.origin : "";
  const ROLES = Object.keys(ROLE_LABELS).filter((r) => r !== "client");

  async function invite() {
    setErr("");
    if (!form.email || !form.full_name) { setErr("Email and name are required."); return; }
    setSaving(true);
    try {
      const res = await api("/api/admin/users", { method: "POST", body: form });
      setTokenResult({ name: res.full_name, token: res.invite_token, type: "invite" });
      setShowForm(false);
      setForm({ email: "", full_name: "", role: "adviser", title: "" });
      refetch();
    } catch (e: any) {
      setErr(e.message || "Error creating user.");
    } finally {
      setSaving(false);
    }
  }

  async function saveEdit() {
    if (!editTarget) return;
    setSaving(true);
    try {
      await api(`/api/admin/users/${editTarget.id}`, {
        method: "PATCH",
        body: { full_name: editTarget.full_name, role: editTarget.role, title: editTarget.title },
      });
      setEditTarget(null);
      refetch();
    } finally {
      setSaving(false);
    }
  }

  async function deactivateUser(u: any) {
    await api(`/api/admin/users/${u.id}`, { method: "DELETE" });
    refetch();
  }

  async function activateUser(u: any) {
    await api(`/api/admin/users/${u.id}`, { method: "PATCH", body: { is_active: true } });
    refetch();
  }

  async function resendInvite(u: any) {
    const res = await api(`/api/admin/users/${u.id}/resend-invite`, { method: "POST", body: {} });
    setTokenResult({ name: u.full_name, token: res.invite_token, type: "invite" });
  }

  async function resetPwd(u: any) {
    const res = await api(`/api/admin/users/${u.id}/reset-password`, { method: "POST", body: {} });
    setTokenResult({ name: u.full_name, token: res.reset_token, type: "reset" });
  }

  return (
    <div className="max-w-5xl space-y-4">
      {tokenResult && (
        <div className="card p-4 border-positive bg-positive/5 flex items-start gap-3">
          <Check size={16} className="text-positive mt-0.5 shrink-0" />
          <div className="flex-1 min-w-0">
            <p className="text-sm font-medium text-ink">
              {tokenResult.type === "invite" ? `Invite link for ${tokenResult.name}` : `Password reset link for ${tokenResult.name}`}
              <span className="text-ink-muted font-normal ml-1">· expires in {tokenResult.type === "invite" ? "7 days" : "24 hours"}</span>
            </p>
            <p className="text-xs text-ink-muted mt-1 break-all font-mono">{origin}/accept-invite?token={tokenResult.token}</p>
            <div className="mt-2 flex gap-2">
              <CopyButton value={`${origin}/accept-invite?token=${tokenResult.token}`} />
              <button onClick={() => setTokenResult(null)} className="btn-outline py-1 px-2 text-xs"><X size={13} /></button>
            </div>
          </div>
        </div>
      )}

      {showForm ? (
        <Card>
          <div className="font-semibold text-ink mb-3 flex items-center gap-2"><UserPlus size={15} /> Invite new user</div>
          {err && <p className="text-sm text-red-600 mb-2">{err}</p>}
          <div className="grid sm:grid-cols-2 gap-3 mb-3">
            <div><label className="label">Full name</label>
              <input className="input" placeholder="Jane Smith" value={form.full_name}
                onChange={(e) => setForm({ ...form, full_name: e.target.value })} />
            </div>
            <div><label className="label">Email</label>
              <input className="input" type="email" placeholder="jane@firm.com" value={form.email}
                onChange={(e) => setForm({ ...form, email: e.target.value })} />
            </div>
            <div><label className="label">Role</label>
              <select className="input" value={form.role} onChange={(e) => setForm({ ...form, role: e.target.value })}>
                {ROLES.map((r) => <option key={r} value={r}>{ROLE_LABELS[r]}</option>)}
              </select>
            </div>
            <div><label className="label">Title <span className="text-ink-muted font-normal">(optional)</span></label>
              <input className="input" placeholder="Senior Adviser" value={form.title}
                onChange={(e) => setForm({ ...form, title: e.target.value })} />
            </div>
          </div>
          <div className="flex gap-2">
            <button className="btn-primary" onClick={invite} disabled={saving}>
              {saving ? "Creating…" : <><UserPlus size={15} /> Create & copy invite link</>}
            </button>
            <button className="btn-outline" onClick={() => { setShowForm(false); setErr(""); }}>Cancel</button>
          </div>
        </Card>
      ) : (
        <div className="flex justify-end">
          <button className="btn-primary" onClick={() => setShowForm(true)}><UserPlus size={15} /> Invite user</button>
        </div>
      )}

      {editTarget && (
        <Card>
          <div className="font-semibold text-ink mb-3">Edit — {editTarget.email}</div>
          <div className="grid sm:grid-cols-3 gap-3 mb-3">
            <div><label className="label">Full name</label>
              <input className="input" value={editTarget.full_name}
                onChange={(e) => setEditTarget({ ...editTarget, full_name: e.target.value })} />
            </div>
            <div><label className="label">Role</label>
              <select className="input" value={editTarget.role}
                onChange={(e) => setEditTarget({ ...editTarget, role: e.target.value })}>
                {ROLES.map((r) => <option key={r} value={r}>{ROLE_LABELS[r]}</option>)}
              </select>
            </div>
            <div><label className="label">Title</label>
              <input className="input" value={editTarget.title || ""}
                onChange={(e) => setEditTarget({ ...editTarget, title: e.target.value })} />
            </div>
          </div>
          <div className="flex gap-2">
            <button className="btn-primary" onClick={saveEdit} disabled={saving}>
              {saving ? "Saving…" : <><Save size={15} /> Save</>}
            </button>
            <button className="btn-outline" onClick={() => setEditTarget(null)}>Cancel</button>
          </div>
        </Card>
      )}

      <Card className="overflow-x-auto p-0">
        {loading ? (
          <div className="p-6"><Spinner /></div>
        ) : !users?.length ? (
          <div className="p-8 text-center text-ink-muted text-sm">No users yet.</div>
        ) : (
          <table className="w-full text-sm">
            <thead>
              <tr className="border-b border-navy-100 text-left text-ink-muted text-xs font-medium">
                <th className="px-4 py-3">Name</th>
                <th className="px-4 py-3">Email</th>
                <th className="px-4 py-3">Role</th>
                <th className="px-4 py-3">Status</th>
                <th className="px-4 py-3 text-right">Actions</th>
              </tr>
            </thead>
            <tbody>
              {users.map((u) => (
                <tr key={u.id} className="border-b border-navy-50 last:border-0 hover:bg-navy-50/40 transition">
                  <td className="px-4 py-3">
                    <div className="font-medium text-ink">{u.full_name}</div>
                    {u.title && <div className="text-xs text-ink-muted">{u.title}</div>}
                  </td>
                  <td className="px-4 py-3 text-ink-soft">{u.email}</td>
                  <td className="px-4 py-3"><RoleBadge role={u.role} /></td>
                  <td className="px-4 py-3">
                    {u.is_active
                      ? <span className="text-xs text-positive font-medium">Active</span>
                      : <span className="text-xs text-ink-muted">Inactive</span>}
                  </td>
                  <td className="px-4 py-3">
                    <div className="flex items-center justify-end gap-1.5 flex-wrap">
                      <button className="btn-outline py-1 px-2 text-xs" onClick={() => setEditTarget({ ...u })}>Edit</button>
                      {u.is_active ? (
                        <button className="btn-outline py-1 px-2 text-xs flex items-center gap-1" onClick={() => resetPwd(u)}>
                          <KeyRound size={11} /> Reset pwd
                        </button>
                      ) : (
                        <button className="btn-outline py-1 px-2 text-xs flex items-center gap-1" onClick={() => resendInvite(u)}>
                          <UserPlus size={11} /> Resend invite
                        </button>
                      )}
                      {u.is_active && (
                        <InlineConfirmButton
                          label="Force logout"
                          confirmLabel="Log out all sessions?"
                          onConfirm={async () => { await api(`/api/admin/users/${u.id}/force-logout`, { method: "POST", body: {} }); }}
                          className="py-1 px-2 text-xs btn-outline text-amber-600"
                        />
                      )}
                      <InlineConfirmButton
                        label={u.is_active ? "Deactivate" : "Activate"}
                        confirmLabel={u.is_active ? "Deactivate & end all sessions?" : "Reactivate account?"}
                        onConfirm={u.is_active ? () => deactivateUser(u) : () => activateUser(u)}
                        className={`py-1 px-2 text-xs ${u.is_active ? "btn-outline text-red-600" : "btn-outline text-positive"}`}
                      />
                    </div>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
      </Card>
    </div>
  );
}

// ── Clients tab ───────────────────────────────────────────────────────────────

const SEGMENTS: Record<string, string> = {
  private_wealth: "Private wealth", mass_affluent: "Mass affluent",
  for_purpose: "For purpose", institutional: "Institutional", next_gen: "Next gen",
};
const MANDATE_TYPES = ["advisory", "discretionary", "execution_only"];
const RISK_PROFILES = ["conservative", "balanced", "growth", "aggressive"];

function ClientsTab() {
  const { data: clients, loading, refetch } = useApi<any[]>("/api/admin/clients");
  const { data: advisers } = useApi<any[]>("/api/admin/users");
  const [showForm, setShowForm] = useState(false);
  const [form, setForm] = useState({ full_name: "", email: "", date_of_birth: "", segment: "private_wealth", mandate_type: "advisory", risk_profile: "balanced", adviser_id: "" });
  const [saving, setSaving] = useState(false);
  const [err, setErr] = useState("");
  const [csvImporting, setCsvImporting] = useState(false);
  const [csvResult, setCsvResult] = useState<string | null>(null);

  const staffAdvisers = advisers?.filter((u) => u.is_active && ["adviser", "paraplanner", "portfolio_team"].includes(u.role)) || [];

  async function importCsv(e: React.ChangeEvent<HTMLInputElement>) {
    const file = e.target.files?.[0];
    if (!file) return;
    const text = await file.text();
    setCsvImporting(true);
    setCsvResult(null);
    try {
      const res = await api<any>("/api/admin/clients/import", { method: "POST", body: { csv_data: text } });
      setCsvResult(`Imported ${res.imported} prospect(s) into Onboarding pipeline.`);
    } catch (ex: any) {
      setCsvResult(`Error: ${ex.message}`);
    } finally {
      setCsvImporting(false);
      e.target.value = "";
    }
  }
  const [assigningId, setAssigningId] = useState<string | null>(null);
  const [assignAdviser, setAssignAdviser] = useState("");

  async function saveAdviser(householdId: string) {
    try {
      await api(`/api/admin/clients/${householdId}/adviser?adviser_id=${assignAdviser}`, { method: "PATCH", body: {} });
      setAssigningId(null);
      refetch();
    } catch (e: any) { alert(e.message); }
  }

  async function create() {
    setErr("");
    if (!form.full_name) { setErr("Name is required."); return; }
    setSaving(true);
    try {
      await api("/api/admin/clients", { method: "POST", body: form });
      setShowForm(false);
      setForm({ full_name: "", email: "", date_of_birth: "", segment: "private_wealth", mandate_type: "advisory", risk_profile: "balanced", adviser_id: "" });
      refetch();
    } catch (e: any) {
      setErr(e.message || "Error creating client.");
    } finally {
      setSaving(false);
    }
  }

  return (
    <div className="max-w-5xl space-y-4">
      {showForm ? (
        <Card>
          <div className="font-semibold text-ink mb-3 flex items-center gap-2"><UserPlus size={15} /> Add new client</div>
          {err && <p className="text-sm text-red-600 mb-2">{err}</p>}
          <div className="grid sm:grid-cols-2 gap-3 mb-3">
            <div><label className="label">Full name</label>
              <input className="input" placeholder="Jane Doe" value={form.full_name} onChange={(e) => setForm({ ...form, full_name: e.target.value })} /></div>
            <div><label className="label">Email <span className="text-ink-muted font-normal">(optional)</span></label>
              <input className="input" type="email" placeholder="jane@example.com" value={form.email} onChange={(e) => setForm({ ...form, email: e.target.value })} /></div>
            <div><label className="label">Date of birth <span className="text-ink-muted font-normal">(optional)</span></label>
              <input className="input" type="date" value={form.date_of_birth} onChange={(e) => setForm({ ...form, date_of_birth: e.target.value })} /></div>
            <div><label className="label">Segment</label>
              <select className="input" value={form.segment} onChange={(e) => setForm({ ...form, segment: e.target.value })}>
                {Object.entries(SEGMENTS).map(([k, v]) => <option key={k} value={k}>{v}</option>)}
              </select></div>
            <div><label className="label">Mandate type</label>
              <select className="input" value={form.mandate_type} onChange={(e) => setForm({ ...form, mandate_type: e.target.value })}>
                {MANDATE_TYPES.map((t) => <option key={t} value={t}>{titleCase(t)}</option>)}
              </select></div>
            <div><label className="label">Risk profile</label>
              <select className="input" value={form.risk_profile} onChange={(e) => setForm({ ...form, risk_profile: e.target.value })}>
                {RISK_PROFILES.map((r) => <option key={r} value={r}>{titleCase(r)}</option>)}
              </select></div>
            <div className="sm:col-span-2"><label className="label">Assign adviser <span className="text-ink-muted font-normal">(optional)</span></label>
              <select className="input" value={form.adviser_id} onChange={(e) => setForm({ ...form, adviser_id: e.target.value })}>
                <option value="">— None —</option>
                {staffAdvisers.map((u: any) => <option key={u.id} value={u.id}>{u.full_name} ({titleCase(u.role)})</option>)}
              </select></div>
          </div>
          <div className="flex gap-2">
            <button className="btn-primary" onClick={create} disabled={saving}>{saving ? "Creating…" : <><Plus size={14} /> Create client</>}</button>
            <button className="btn-outline" onClick={() => { setShowForm(false); setErr(""); }}>Cancel</button>
          </div>
        </Card>
      ) : (
        <div className="flex items-center justify-end gap-2">
          {csvResult && <span className="text-xs text-ink-muted">{csvResult}</span>}
          <label className={`btn-outline cursor-pointer text-sm ${csvImporting ? "opacity-50" : ""}`}>
            <FileText size={14} /> {csvImporting ? "Importing…" : "Import CSV"}
            <input type="file" accept=".csv,text/csv" className="hidden" onChange={importCsv} disabled={csvImporting} />
          </label>
          <button className="btn-primary" onClick={() => setShowForm(true)}><Plus size={15} /> Add client</button>
        </div>
      )}

      <Card className="overflow-x-auto p-0">
        {loading ? <div className="p-6"><Spinner /></div> : !clients?.length ? (
          <div className="p-8"><Empty>No clients yet. Add the first one above.</Empty></div>
        ) : (
          <table className="w-full text-sm">
            <thead>
              <tr className="border-b border-navy-100 text-left text-xs text-ink-muted font-medium">
                <th className="px-4 py-3">Name</th>
                <th className="px-4 py-3">Email</th>
                <th className="px-4 py-3">Segment</th>
                <th className="px-4 py-3">Adviser</th>
                <th className="px-4 py-3">Added</th>
              </tr>
            </thead>
            <tbody>
              {clients.map((c) => (
                <tr key={c.id} className="border-b border-navy-50 last:border-0 hover:bg-navy-50/40">
                  <td className="px-4 py-3 font-medium text-ink">{c.name}</td>
                  <td className="px-4 py-3 text-ink-soft">{c.primary_email || "—"}</td>
                  <td className="px-4 py-3 text-xs"><span className="px-2 py-0.5 rounded-full bg-navy-100 text-navy-700">{SEGMENTS[c.segment] || c.segment}</span></td>
                  <td className="px-4 py-3">
                    {assigningId === c.id ? (
                      <div className="flex items-center gap-1">
                        <select className="input py-1 text-xs" value={assignAdviser} onChange={(e) => setAssignAdviser(e.target.value)}>
                          <option value="">— Unassign —</option>
                          {staffAdvisers.map((u: any) => <option key={u.id} value={u.id}>{u.full_name}</option>)}
                        </select>
                        <button className="btn-primary py-1 px-2 text-xs" onClick={() => saveAdviser(c.id)} disabled={!assignAdviser}>Save</button>
                        <button className="btn-outline py-1 px-2 text-xs" onClick={() => setAssigningId(null)}>×</button>
                      </div>
                    ) : (
                      <span className="flex items-center gap-1.5">
                        <span className="text-ink-soft">{c.adviser || <span className="text-ink-muted italic">Unassigned</span>}</span>
                        <button onClick={() => { setAssigningId(c.id); setAssignAdviser(""); }} className="text-navy-400 hover:text-navy-600">
                          <Pencil size={11} />
                        </button>
                      </span>
                    )}
                  </td>
                  <td className="px-4 py-3 text-xs text-ink-muted">{c.created_at ? new Date(c.created_at).toLocaleDateString() : "—"}</td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
      </Card>
    </div>
  );
}

// ── Assignments tab ───────────────────────────────────────────────────────────

function AssignmentsTab() {
  const { data: assignments, loading, refetch } = useApi<any[]>("/api/admin/assignments");
  const { data: users } = useApi<any[]>("/api/admin/users");
  const { data: clients } = useApi<any[]>("/api/admin/clients");
  const [showForm, setShowForm] = useState(false);
  const [form, setForm] = useState({ household_id: "", adviser_id: "" });
  const [saving, setSaving] = useState(false);
  const [err, setErr] = useState("");

  const staffAdvisers = users?.filter((u: any) => u.is_active && ["adviser", "paraplanner", "portfolio_team"].includes(u.role)) || [];

  async function create() {
    setErr("");
    if (!form.household_id || !form.adviser_id) { setErr("Both client and adviser are required."); return; }
    setSaving(true);
    try {
      await api("/api/admin/assignments", { method: "POST", body: form });
      setShowForm(false);
      setForm({ household_id: "", adviser_id: "" });
      refetch();
    } catch (e: any) { setErr(e.message || "Error creating assignment."); }
    finally { setSaving(false); }
  }

  return (
    <div className="max-w-4xl space-y-4">
      {showForm ? (
        <Card>
          <div className="font-semibold text-ink mb-3 flex items-center gap-2"><Link2 size={15} /> Assign adviser to client</div>
          {err && <p className="text-sm text-red-600 mb-2">{err}</p>}
          <div className="grid sm:grid-cols-2 gap-3 mb-3">
            <div>
              <label className="label">Client household</label>
              <select className="input" value={form.household_id} onChange={(e) => setForm({ ...form, household_id: e.target.value })}>
                <option value="">— Select client —</option>
                {clients?.map((c: any) => <option key={c.id} value={c.id}>{c.name}</option>)}
              </select>
            </div>
            <div>
              <label className="label">Adviser</label>
              <select className="input" value={form.adviser_id} onChange={(e) => setForm({ ...form, adviser_id: e.target.value })}>
                <option value="">— Select adviser —</option>
                {staffAdvisers.map((u: any) => <option key={u.id} value={u.id}>{u.full_name} ({titleCase(u.role)})</option>)}
              </select>
            </div>
          </div>
          <div className="flex gap-2">
            <button className="btn-primary" onClick={create} disabled={saving}>{saving ? "Saving…" : <><Link2 size={14} /> Assign</>}</button>
            <button className="btn-outline" onClick={() => { setShowForm(false); setErr(""); }}>Cancel</button>
          </div>
        </Card>
      ) : (
        <div className="flex justify-end">
          <button className="btn-primary" onClick={() => setShowForm(true)}><Plus size={15} /> Assign adviser</button>
        </div>
      )}
      <Card className="overflow-x-auto p-0">
        {loading ? <div className="p-6"><Spinner /></div> : !assignments?.length ? (
          <div className="p-8"><Empty>No assignments yet.</Empty></div>
        ) : (
          <table className="w-full text-sm">
            <thead>
              <tr className="border-b border-navy-100 text-left text-xs text-ink-muted font-medium">
                <th className="px-4 py-3">Client</th>
                <th className="px-4 py-3">Adviser</th>
                <th className="px-4 py-3">Email</th>
                <th className="px-4 py-3">Assigned</th>
                <th className="px-4 py-3 text-right">Actions</th>
              </tr>
            </thead>
            <tbody>
              {assignments.map((a: any) => (
                <tr key={a.id} className="border-b border-navy-50 last:border-0 hover:bg-navy-50/40">
                  <td className="px-4 py-3 font-medium text-ink">{a.household_name}</td>
                  <td className="px-4 py-3 text-ink-soft">{a.adviser_name}</td>
                  <td className="px-4 py-3 text-xs text-ink-muted">{a.adviser_email || "—"}</td>
                  <td className="px-4 py-3 text-xs text-ink-muted">{a.created_at ? new Date(a.created_at).toLocaleDateString() : "—"}</td>
                  <td className="px-4 py-3 text-right">
                    <InlineConfirmButton
                      label="Remove"
                      confirmLabel="Remove assignment?"
                      onConfirm={async () => { await api(`/api/admin/assignments/${a.id}`, { method: "DELETE" }); refetch(); }}
                      className="py-1 px-2 text-xs btn-outline text-red-600"
                    />
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
      </Card>
    </div>
  );
}

// ── Segments tab ──────────────────────────────────────────────────────────────

function SegmentsTab() {
  const { data: segments, loading, refetch } = useApi<any[]>("/api/admin/segments");
  const [editing, setEditing] = useState<any>(null);
  const [showForm, setShowForm] = useState(false);
  const [form, setForm] = useState({ slug: "", label: "", fee_tier_bps: "", min_aum_usd: "", description: "" });
  const [saving, setSaving] = useState(false);
  const [err, setErr] = useState("");

  async function create() {
    setErr("");
    if (!form.slug || !form.label) { setErr("Slug and label are required."); return; }
    setSaving(true);
    try {
      await api("/api/admin/segments", {
        method: "POST",
        body: {
          slug: form.slug.toLowerCase().replace(/\s+/g, "_"),
          label: form.label,
          fee_tier_bps: form.fee_tier_bps ? parseInt(form.fee_tier_bps) : null,
          min_aum_usd: form.min_aum_usd ? parseFloat(form.min_aum_usd) : null,
          description: form.description || null,
        },
      });
      setShowForm(false);
      setForm({ slug: "", label: "", fee_tier_bps: "", min_aum_usd: "", description: "" });
      refetch();
    } catch (e: any) { setErr(e.message || "Error creating segment."); }
    finally { setSaving(false); }
  }

  async function saveEdit() {
    if (!editing) return;
    setSaving(true);
    try {
      await api(`/api/admin/segments/${editing.id}`, {
        method: "PATCH",
        body: {
          label: editing.label,
          fee_tier_bps: editing.fee_tier_bps ?? null,
          min_aum_usd: editing.min_aum_usd ?? null,
          description: editing.description || null,
          is_active: editing.is_active,
        },
      });
      setEditing(null);
      refetch();
    } finally { setSaving(false); }
  }

  return (
    <div className="max-w-4xl space-y-4">
      {editing && (
        <Card>
          <div className="font-semibold text-ink mb-3 flex items-center gap-2"><Pencil size={14} /> Edit segment — {editing.slug}</div>
          <div className="grid sm:grid-cols-2 gap-3 mb-3">
            <div><label className="label">Label</label>
              <input className="input" value={editing.label} onChange={(e) => setEditing({ ...editing, label: e.target.value })} /></div>
            <div><label className="label">Fee tier (bps)</label>
              <input className="input" type="number" min="0" value={editing.fee_tier_bps ?? ""}
                onChange={(e) => setEditing({ ...editing, fee_tier_bps: e.target.value ? parseInt(e.target.value) : null })} /></div>
            <div><label className="label">Min AUM (USD)</label>
              <input className="input" type="number" min="0" value={editing.min_aum_usd ?? ""}
                onChange={(e) => setEditing({ ...editing, min_aum_usd: e.target.value ? parseFloat(e.target.value) : null })} /></div>
            <div><label className="label">Description</label>
              <input className="input" value={editing.description || ""}
                onChange={(e) => setEditing({ ...editing, description: e.target.value })} /></div>
            <div className="flex items-end pb-1">
              <label className="flex items-center gap-2 cursor-pointer">
                <input type="checkbox" className="w-4 h-4 rounded" checked={editing.is_active}
                  onChange={(e) => setEditing({ ...editing, is_active: e.target.checked })} />
                <span className="text-sm text-ink">Active</span>
              </label>
            </div>
          </div>
          <div className="flex gap-2">
            <button className="btn-primary" onClick={saveEdit} disabled={saving}>{saving ? "Saving…" : <><Save size={14} /> Save</>}</button>
            <button className="btn-outline" onClick={() => setEditing(null)}>Cancel</button>
          </div>
        </Card>
      )}
      {showForm && (
        <Card>
          <div className="font-semibold text-ink mb-3 flex items-center gap-2"><Plus size={14} /> New segment</div>
          {err && <p className="text-sm text-red-600 mb-2">{err}</p>}
          <div className="grid sm:grid-cols-2 gap-3 mb-3">
            <div><label className="label">Slug</label>
              <input className="input" placeholder="e.g. family_office" value={form.slug}
                onChange={(e) => setForm({ ...form, slug: e.target.value })} /></div>
            <div><label className="label">Label</label>
              <input className="input" placeholder="e.g. Family Office" value={form.label}
                onChange={(e) => setForm({ ...form, label: e.target.value })} /></div>
            <div><label className="label">Fee tier (bps)</label>
              <input className="input" type="number" min="0" placeholder="50" value={form.fee_tier_bps}
                onChange={(e) => setForm({ ...form, fee_tier_bps: e.target.value })} /></div>
            <div><label className="label">Min AUM (USD)</label>
              <input className="input" type="number" min="0" placeholder="1000000" value={form.min_aum_usd}
                onChange={(e) => setForm({ ...form, min_aum_usd: e.target.value })} /></div>
            <div className="sm:col-span-2"><label className="label">Description</label>
              <input className="input" value={form.description}
                onChange={(e) => setForm({ ...form, description: e.target.value })} /></div>
          </div>
          <div className="flex gap-2">
            <button className="btn-primary" onClick={create} disabled={saving}>{saving ? "Creating…" : "Create segment"}</button>
            <button className="btn-outline" onClick={() => { setShowForm(false); setErr(""); }}>Cancel</button>
          </div>
        </Card>
      )}
      {!editing && !showForm && (
        <div className="flex justify-end">
          <button className="btn-primary" onClick={() => setShowForm(true)}><Plus size={15} /> Add segment</button>
        </div>
      )}
      <Card className="overflow-x-auto p-0">
        {loading ? <div className="p-6"><Spinner /></div> : !segments?.length ? (
          <div className="p-8"><Empty>No segments configured.</Empty></div>
        ) : (
          <table className="w-full text-sm">
            <thead>
              <tr className="border-b border-navy-100 text-left text-xs text-ink-muted font-medium">
                <th className="px-4 py-3">Slug</th>
                <th className="px-4 py-3">Label</th>
                <th className="px-4 py-3">Fee (bps)</th>
                <th className="px-4 py-3">Min AUM</th>
                <th className="px-4 py-3">Status</th>
                <th className="px-4 py-3 text-right">Actions</th>
              </tr>
            </thead>
            <tbody>
              {segments.map((s: any) => (
                <tr key={s.id} className={`border-b border-navy-50 last:border-0 hover:bg-navy-50/40 ${!s.is_active ? "opacity-50" : ""}`}>
                  <td className="px-4 py-3 font-mono text-xs text-ink-muted">{s.slug}</td>
                  <td className="px-4 py-3 font-medium text-ink">{s.label}</td>
                  <td className="px-4 py-3 text-ink-soft">{s.fee_tier_bps != null ? `${s.fee_tier_bps} bps` : "—"}</td>
                  <td className="px-4 py-3 text-ink-soft">{s.min_aum_usd != null ? `$${s.min_aum_usd.toLocaleString()}` : "—"}</td>
                  <td className="px-4 py-3">
                    {s.is_active
                      ? <span className="text-xs text-positive font-medium">Active</span>
                      : <span className="text-xs text-ink-muted">Inactive</span>}
                  </td>
                  <td className="px-4 py-3 text-right flex items-center justify-end gap-1.5">
                    <button className="btn-outline py-1 px-2 text-xs" onClick={() => setEditing({ ...s })}>Edit</button>
                    {s.is_active && (
                      <InlineConfirmButton
                        label="Deactivate"
                        confirmLabel="Deactivate segment?"
                        onConfirm={async () => { await api(`/api/admin/segments/${s.id}`, { method: "DELETE" }); refetch(); }}
                        className="py-1 px-2 text-xs btn-outline text-red-600"
                      />
                    )}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
      </Card>
    </div>
  );
}

// ── Connectors tab ────────────────────────────────────────────────────────────

const STATUS_COLORS: Record<string, string> = {
  connected: "text-positive", mock: "text-amber-600", error: "text-red-600",
  configured: "text-blue-600", disabled: "text-ink-muted",
};
const STATUS_ICONS: Record<string, any> = {
  connected: CheckCircle2, mock: CircleDot, error: AlertCircle,
  configured: Wifi, disabled: WifiOff,
};

function ConnectorsTab() {
  const { data: connectors, loading, refetch } = useApi<any[]>("/api/conduit/connectors");
  const [syncing, setSyncing] = useState<string | null>(null);
  const [syncResults, setSyncResults] = useState<Record<string, any>>({});

  async function triggerSync(id: string) {
    setSyncing(id);
    try {
      const res = await api(`/api/conduit/connectors/${id}/sync`, { method: "POST", body: {} });
      setSyncResults((prev) => ({ ...prev, [id]: res }));
      refetch();
    } finally {
      setSyncing(null);
    }
  }

  async function toggleMock(connector: any) {
    await api(`/api/conduit/connectors/${connector.id}`, { method: "PATCH", body: { use_mock: !connector.use_mock } });
    refetch();
  }

  if (loading) return <Spinner />;
  if (!connectors?.length) return <Card><Empty>No connectors configured. They are provisioned during firm setup.</Empty></Card>;

  return (
    <div className="max-w-5xl space-y-3">
      {connectors.map((c) => {
        const StatusIcon = STATUS_ICONS[c.status] || CircleDot;
        const syncRes = syncResults[c.id];
        return (
          <Card key={c.id} className="flex flex-col gap-3">
            <div className="flex items-start gap-3">
              <div className="flex-1 min-w-0">
                <div className="flex items-center gap-2 flex-wrap">
                  <span className="font-medium text-ink">{c.display_name}</span>
                  <span className="text-xs px-2 py-0.5 rounded-full bg-navy-100 text-navy-600">{titleCase(c.domain)}</span>
                  <span className={`flex items-center gap-1 text-xs font-medium ${STATUS_COLORS[c.status] || "text-ink-muted"}`}>
                    <StatusIcon size={12} /> {titleCase(c.status)}
                  </span>
                  {c.use_mock && <span className="text-[10px] px-1.5 py-0.5 rounded bg-amber-50 text-amber-700 border border-amber-200">mock data</span>}
                </div>
                <div className="text-xs text-ink-muted mt-1 flex items-center gap-3">
                  {c.last_synced_at && <span>Last sync: {new Date(c.last_synced_at).toLocaleString()}</span>}
                  {c.last_error && <span className="text-red-500 truncate max-w-xs">{c.last_error}</span>}
                  {!c.last_synced_at && !c.last_error && <span>Never synced</span>}
                </div>
                {syncRes && (
                  <div className={`text-xs mt-1 font-medium ${syncRes.status === "success" ? "text-positive" : "text-red-600"}`}>
                    Last triggered: {syncRes.status} · {syncRes.records_ingested} records
                  </div>
                )}
              </div>
              <div className="flex items-center gap-2 shrink-0">
                <button
                  className={`btn-outline py-1 px-2 text-xs flex items-center gap-1 ${c.use_mock ? "" : "bg-navy-50"}`}
                  onClick={() => toggleMock(c)}
                  title={c.use_mock ? "Switch to live" : "Switch to mock"}
                >
                  {c.use_mock ? <><WifiOff size={11} /> Mock</> : <><Wifi size={11} /> Live</>}
                </button>
                <button
                  className="btn-outline py-1 px-2 text-xs flex items-center gap-1"
                  onClick={() => triggerSync(c.id)}
                  disabled={syncing === c.id}
                >
                  <RefreshCw size={11} className={syncing === c.id ? "animate-spin" : ""} />
                  {syncing === c.id ? "Syncing…" : "Sync now"}
                </button>
              </div>
            </div>
          </Card>
        );
      })}
    </div>
  );
}

// ── Notifications tab ─────────────────────────────────────────────────────────

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

function NotificationsTab() {
  const { data: configs, loading, refetch } = useApi<any[]>("/api/admin/notifications");
  const [local, setLocal] = useState<Record<string, any>>({});
  const [saving, setSaving] = useState(false);
  const [saved, setSaved] = useState(false);

  const key = (ev: string, ch: string) => `${ev}::${ch}`;

  function getCell(ev: string, ch: string) {
    const k = key(ev, ch);
    if (local[k] !== undefined) return local[k];
    return configs?.find((c: any) => c.event_type === ev && c.channel === ch) || { enabled: false, recipients: [], config: {} };
  }

  function toggle(ev: string, ch: string) {
    const cell = getCell(ev, ch);
    setLocal((prev) => ({ ...prev, [key(ev, ch)]: { ...cell, enabled: !cell.enabled } }));
    setSaved(false);
  }

  function setRecipients(ev: string, ch: string, val: string) {
    const cell = getCell(ev, ch);
    setLocal((prev) => ({ ...prev, [key(ev, ch)]: { ...cell, recipients: val.split(",").map((s) => s.trim()).filter(Boolean) } }));
    setSaved(false);
  }

  async function save() {
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

  if (loading) return <Spinner />;

  return (
    <div className="max-w-3xl space-y-4">
      <Card>
        <div className="flex items-center justify-between mb-4">
          <div>
            <div className="font-semibold text-ink flex items-center gap-2"><Bell size={15} /> Alert & notification rules</div>
            <p className="text-xs text-ink-muted mt-0.5">Configure which events trigger notifications and through which channels.</p>
          </div>
          <button className="btn-primary py-1.5 px-3 text-sm" onClick={save} disabled={saving}>
            {saving ? "Saving…" : saved ? <><Check size={14} /> Saved</> : <><Save size={14} /> Save changes</>}
          </button>
        </div>

        <div className="overflow-x-auto">
          <table className="w-full text-sm">
            <thead>
              <tr className="border-b border-navy-100">
                <th className="text-left px-0 py-2 text-xs font-medium text-ink-muted">Event</th>
                {NOTIF_CHANNELS.map((ch) => (
                  <th key={ch.key} className="text-center px-4 py-2 text-xs font-medium text-ink-muted w-28">{ch.label}</th>
                ))}
                <th className="text-left px-4 py-2 text-xs font-medium text-ink-muted">Email recipients</th>
              </tr>
            </thead>
            <tbody>
              {NOTIF_EVENTS.map((ev) => (
                <tr key={ev.key} className="border-b border-navy-50 last:border-0">
                  <td className="py-3 pr-4">
                    <div className="font-medium text-ink text-sm">{ev.label}</div>
                    <div className="text-xs text-ink-muted mt-0.5">{ev.description}</div>
                  </td>
                  {NOTIF_CHANNELS.map((ch) => {
                    const cell = getCell(ev.key, ch.key);
                    return (
                      <td key={ch.key} className="text-center px-4 py-3">
                        <button
                          onClick={() => toggle(ev.key, ch.key)}
                          className={`w-9 h-5 rounded-full transition-colors relative ${cell.enabled ? "bg-navy-700" : "bg-navy-200"}`}
                        >
                          <span className={`absolute top-0.5 w-4 h-4 rounded-full bg-white shadow transition-all ${cell.enabled ? "left-4" : "left-0.5"}`} />
                        </button>
                      </td>
                    );
                  })}
                  <td className="px-4 py-3">
                    {getCell(ev.key, "email").enabled ? (
                      <input
                        className="input py-1 text-xs"
                        placeholder="admin@firm.com, compliance@firm.com"
                        value={(getCell(ev.key, "email").recipients || []).join(", ")}
                        onChange={(e) => setRecipients(ev.key, "email", e.target.value)}
                      />
                    ) : (
                      <span className="text-xs text-ink-muted italic">Email off</span>
                    )}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </Card>
      <Card className="bg-amber-50/50 border-amber-100">
        <p className="text-xs text-amber-700">
          <strong>Note:</strong> Email delivery requires an SMTP connector configured under Connectors → Productivity & BI.
          In-app notifications appear in the bell icon in the sidebar.
        </p>
      </Card>
    </div>
  );
}

// ── Audit trail tab ───────────────────────────────────────────────────────────

const AUDIT_CATEGORY_COLORS: Record<string, string> = {
  access: "bg-blue-100 text-blue-700",
  recommendation: "bg-navy-100 text-navy-700",
  autonomy: "bg-purple-100 text-purple-700",
  client: "bg-green-100 text-green-700",
};
const AUDIT_EVENT_LABELS: Record<string, string> = {
  "auth.login": "Sign in", "auth.invite_accepted": "Account activated",
  "auth.reset_accepted": "Password reset", "auth.password_changed": "Password changed",
  "user.created": "User created", "user.activated": "User activated",
  "user.deactivated": "User deactivated", "user.invite_resent": "Invite resent",
  "user.password_reset_issued": "Password reset issued",
  "client.created": "Client added", "client.adviser_assigned": "Adviser assigned",
  "recommendation.decision": "Recommendation decided", "recommendation.recommendation": "Recommendation created",
  "autonomy.tier_changed": "Autonomy tier changed",
};

function AuditTab() {
  const [filter, setFilter] = useState("all");
  const { data: events, loading, refetch } = useApi<any[]>("/api/admin/audit?limit=150");

  const categories = ["all", "access", "recommendation", "autonomy", "client"];
  const filtered = filter === "all" ? events : events?.filter((e) => e.category === filter);

  return (
    <div className="max-w-4xl space-y-3">
      <div className="flex items-center gap-2 flex-wrap">
        {categories.map((c) => (
          <button key={c} onClick={() => setFilter(c)}
            className={`px-3 py-1.5 rounded-lg text-sm font-medium transition ${filter === c ? "bg-navy-800 text-white" : "bg-navy-50 text-ink-soft hover:bg-navy-100"}`}>
            {titleCase(c)}
          </button>
        ))}
        <button className="ml-auto btn-outline py-1 px-2 text-xs flex items-center gap-1" onClick={() => refetch()}>
          <RefreshCw size={12} /> Refresh
        </button>
      </div>

      <Card className="overflow-x-auto p-0">
        {loading ? <div className="p-6"><Spinner /></div> : !filtered?.length ? (
          <div className="p-8"><Empty>No audit events yet.</Empty></div>
        ) : (
          <table className="w-full text-sm">
            <thead>
              <tr className="border-b border-navy-100 text-left text-xs text-ink-muted font-medium">
                <th className="px-4 py-3">Time</th>
                <th className="px-4 py-3">Event</th>
                <th className="px-4 py-3">Actor</th>
                <th className="px-4 py-3">Subject</th>
                <th className="px-4 py-3">Detail</th>
              </tr>
            </thead>
            <tbody>
              {filtered.map((e, i) => (
                <tr key={i} className="border-b border-navy-50 last:border-0 hover:bg-navy-50/40 text-xs">
                  <td className="px-4 py-2.5 text-ink-muted whitespace-nowrap">{new Date(e.ts).toLocaleString()}</td>
                  <td className="px-4 py-2.5">
                    <div className="flex items-center gap-1.5">
                      <span className={`px-1.5 py-0.5 rounded text-[10px] font-medium ${AUDIT_CATEGORY_COLORS[e.category] || "bg-navy-100 text-navy-700"}`}>{titleCase(e.category)}</span>
                      <span className="text-ink">{AUDIT_EVENT_LABELS[e.event_type] || titleCase(e.event_type)}</span>
                    </div>
                  </td>
                  <td className="px-4 py-2.5 text-ink-soft">{e.actor || "—"}</td>
                  <td className="px-4 py-2.5 text-ink-soft">{e.subject || "—"}</td>
                  <td className="px-4 py-2.5 text-ink-muted font-mono text-[10px]">
                    {e.detail ? Object.entries(e.detail).map(([k, v]) => `${k}:${v}`).join(" · ") : "—"}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
      </Card>
    </div>
  );
}

// ── Mandates tab ──────────────────────────────────────────────────────────────

const HORIZON_LABELS: Record<string, string> = { short: "Short-term", medium: "Medium-term", long: "Long-term" };
const TOLERANCE_COLORS: Record<string, string> = {
  conservative: "bg-blue-100 text-blue-700", balanced: "bg-navy-100 text-navy-700",
  growth: "bg-amber-100 text-amber-700", aggressive: "bg-red-100 text-red-700",
};

function MandateTypesConfig() {
  const { data: types, refetch } = useApi<any[]>("/api/admin/mandate-types");
  const [editingId, setEditingId] = useState<string | null>(null);
  const [editForm, setEditForm] = useState<any>({});
  const [showNew, setShowNew] = useState(false);
  const [newForm, setNewForm] = useState({ slug: "", label: "", default_autonomy_tier: "tier_2", description: "" });
  const [saving, setSaving] = useState(false);

  const TIERS = [{ v: "tier_1", l: "Tier 1 — Assistive" }, { v: "tier_2", l: "Tier 2 — Supervised" }, { v: "tier_3", l: "Tier 3 — Autonomous" }];

  async function saveEdit(id: string) {
    setSaving(true);
    try {
      await api(`/api/admin/mandate-types/${id}`, { method: "PATCH", body: editForm });
      setEditingId(null);
      refetch();
    } finally { setSaving(false); }
  }

  async function createType() {
    setSaving(true);
    try {
      await api("/api/admin/mandate-types", { method: "POST", body: { ...newForm, slug: newForm.slug.toLowerCase().replace(/\s+/g, "_") } });
      setShowNew(false);
      setNewForm({ slug: "", label: "", default_autonomy_tier: "tier_2", description: "" });
      refetch();
    } finally { setSaving(false); }
  }

  return (
    <Card className="mb-4">
      <div className="flex items-center justify-between mb-3">
        <div className="font-semibold text-ink flex items-center gap-2"><Tag size={14} /> Mandate type configuration</div>
        <button className="btn-outline py-1 px-2 text-xs" onClick={() => setShowNew((v) => !v)}><Plus size={12} /> Add type</button>
      </div>
      {showNew && (
        <div className="border border-navy-100 rounded-lg p-3 mb-3 bg-navy-50/30 grid sm:grid-cols-2 gap-3">
          <div><label className="label">Slug</label><input className="input" placeholder="bespoke" value={newForm.slug} onChange={(e) => setNewForm({ ...newForm, slug: e.target.value })} /></div>
          <div><label className="label">Label</label><input className="input" placeholder="Bespoke" value={newForm.label} onChange={(e) => setNewForm({ ...newForm, label: e.target.value })} /></div>
          <div><label className="label">Default tier</label>
            <select className="input" value={newForm.default_autonomy_tier} onChange={(e) => setNewForm({ ...newForm, default_autonomy_tier: e.target.value })}>
              {TIERS.map((t) => <option key={t.v} value={t.v}>{t.l}</option>)}
            </select></div>
          <div><label className="label">Description</label><input className="input" value={newForm.description} onChange={(e) => setNewForm({ ...newForm, description: e.target.value })} /></div>
          <div className="flex gap-2 items-end">
            <button className="btn-primary py-1 px-3 text-xs" onClick={createType} disabled={saving}>Create</button>
            <button className="btn-outline py-1 px-3 text-xs" onClick={() => setShowNew(false)}>Cancel</button>
          </div>
        </div>
      )}
      <div className="divide-y divide-navy-50">
        {(types || []).map((t: any) => (
          <div key={t.id} className="py-2.5 flex items-start gap-3">
            {editingId === t.id ? (
              <div className="flex-1 grid sm:grid-cols-3 gap-2 items-end">
                <div><label className="label">Label</label><input className="input py-1" value={editForm.label ?? t.label} onChange={(e) => setEditForm({ ...editForm, label: e.target.value })} /></div>
                <div><label className="label">Default tier</label>
                  <select className="input py-1" value={editForm.default_autonomy_tier ?? t.default_autonomy_tier} onChange={(e) => setEditForm({ ...editForm, default_autonomy_tier: e.target.value })}>
                    {TIERS.map((ti) => <option key={ti.v} value={ti.v}>{ti.l}</option>)}
                  </select></div>
                <div className="flex gap-1">
                  <button className="btn-primary py-1 px-2 text-xs" onClick={() => saveEdit(t.id)} disabled={saving}><Save size={12} /></button>
                  <button className="btn-outline py-1 px-2 text-xs" onClick={() => setEditingId(null)}>×</button>
                </div>
              </div>
            ) : (
              <>
                <div className="flex-1 min-w-0">
                  <div className="flex items-center gap-2">
                    <span className="font-medium text-ink text-sm">{t.label}</span>
                    <span className="text-[10px] font-mono text-ink-muted bg-navy-50 px-1.5 py-0.5 rounded">{t.slug}</span>
                    <TierBadge tier={t.default_autonomy_tier} />
                  </div>
                  {t.description && <div className="text-xs text-ink-muted mt-0.5">{t.description}</div>}
                </div>
                <button className="text-navy-400 hover:text-navy-700 shrink-0" onClick={() => { setEditingId(t.id); setEditForm({}); }}><Pencil size={13} /></button>
              </>
            )}
          </div>
        ))}
      </div>
    </Card>
  );
}

function MandatesTab() {
  const { data: mandates, loading, refetch } = useApi<any[]>("/api/admin/mandates");
  const [editing, setEditing] = useState<any>(null);
  const [saving, setSaving] = useState(false);

  async function saveMandate() {
    if (!editing) return;
    setSaving(true);
    try {
      await api(`/api/admin/mandates/${editing.id}`, {
        method: "PATCH",
        body: {
          mandate_type: editing.mandate_type,
          risk_tolerance: editing.suitability?.risk_tolerance,
          investment_horizon: editing.suitability?.investment_horizon,
          max_equity: editing.suitability?.max_equity != null ? parseFloat(editing.suitability.max_equity) : undefined,
          is_active: editing.is_active,
        },
      });
      setEditing(null);
      refetch();
    } finally {
      setSaving(false);
    }
  }

  function setSuit(field: string, val: any) {
    setEditing((prev: any) => ({ ...prev, suitability: { ...prev.suitability, [field]: val } }));
  }

  return (
    <div className="max-w-5xl space-y-4">
      <MandateTypesConfig />
      {editing && (
        <Card>
          <div className="font-semibold text-ink mb-3 flex items-center gap-2"><Pencil size={14} /> Edit — {editing.client_name || editing.name}</div>
          <div className="grid sm:grid-cols-3 gap-3 mb-3">
            <div><label className="label">Mandate type</label>
              <select className="input" value={editing.mandate_type}
                onChange={(e) => setEditing({ ...editing, mandate_type: e.target.value })}>
                {["advisory", "discretionary", "execution_only"].map((t) => <option key={t} value={t}>{titleCase(t)}</option>)}
              </select></div>
            <div><label className="label">Risk tolerance</label>
              <select className="input" value={editing.suitability?.risk_tolerance || "balanced"}
                onChange={(e) => setSuit("risk_tolerance", e.target.value)}>
                {["conservative", "balanced", "growth", "aggressive"].map((r) => <option key={r} value={r}>{titleCase(r)}</option>)}
              </select></div>
            <div><label className="label">Investment horizon</label>
              <select className="input" value={editing.suitability?.investment_horizon || "medium"}
                onChange={(e) => setSuit("investment_horizon", e.target.value)}>
                {["short", "medium", "long"].map((h) => <option key={h} value={h}>{HORIZON_LABELS[h]}</option>)}
              </select></div>
            <div><label className="label">Max equity %</label>
              <input className="input" type="number" min="0" max="100" step="5"
                value={Math.round((editing.suitability?.max_equity ?? 0.6) * 100)}
                onChange={(e) => setSuit("max_equity", parseFloat(e.target.value) / 100)} /></div>
            <div className="flex items-end pb-1">
              <label className="flex items-center gap-2 cursor-pointer">
                <input type="checkbox" className="w-4 h-4 rounded" checked={editing.is_active}
                  onChange={(e) => setEditing({ ...editing, is_active: e.target.checked })} />
                <span className="text-sm text-ink">Active mandate</span>
              </label>
            </div>
          </div>
          <div className="flex gap-2">
            <button className="btn-primary" onClick={saveMandate} disabled={saving}>{saving ? "Saving..." : <><Save size={14} /> Save</>}</button>
            <button className="btn-outline" onClick={() => setEditing(null)}>Cancel</button>
          </div>
        </Card>
      )}
      <Card className="overflow-x-auto p-0">
        {loading ? <div className="p-6"><Spinner /></div> : !mandates?.length ? (
          <div className="p-8"><Empty>No mandates found.</Empty></div>
        ) : (
          <table className="w-full text-sm">
            <thead>
              <tr className="border-b border-navy-100 text-left text-xs text-ink-muted font-medium">
                <th className="px-4 py-3">Client</th>
                <th className="px-4 py-3">Mandate name</th>
                <th className="px-4 py-3">Type</th>
                <th className="px-4 py-3">Risk</th>
                <th className="px-4 py-3">Max equity</th>
                <th className="px-4 py-3">Horizon</th>
                <th className="px-4 py-3">Status</th>
                <th className="px-4 py-3 text-right">Edit</th>
              </tr>
            </thead>
            <tbody>
              {mandates.map((m) => (
                <tr key={m.id} className="border-b border-navy-50 last:border-0 hover:bg-navy-50/40">
                  <td className="px-4 py-3 text-ink-soft text-xs">{m.client_name || "—"}</td>
                  <td className="px-4 py-3 font-medium text-ink text-xs">{m.name}</td>
                  <td className="px-4 py-3 text-xs capitalize">{m.mandate_type?.replace(/_/g, " ")}</td>
                  <td className="px-4 py-3">
                    <span className={`px-2 py-0.5 rounded-full text-[11px] font-medium ${TOLERANCE_COLORS[m.suitability?.risk_tolerance] || "bg-navy-100 text-navy-700"}`}>
                      {titleCase(m.suitability?.risk_tolerance || "—")}
                    </span>
                  </td>
                  <td className="px-4 py-3 text-xs">{m.suitability?.max_equity != null ? `${Math.round(m.suitability.max_equity * 100)}%` : "—"}</td>
                  <td className="px-4 py-3 text-xs">{HORIZON_LABELS[m.suitability?.investment_horizon] || "—"}</td>
                  <td className="px-4 py-3">{m.is_active ? <span className="text-xs text-positive font-medium">Active</span> : <span className="text-xs text-ink-muted">Inactive</span>}</td>
                  <td className="px-4 py-3 text-right">
                    <button className="btn-outline py-1 px-2 text-xs flex items-center gap-1 ml-auto" onClick={() => setEditing({ ...m })}>
                      <Pencil size={11} /> Edit
                    </button>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
      </Card>
    </div>
  );
}

// ── Compliance tab ────────────────────────────────────────────────────────────

const SEVERITY_COLORS: Record<string, string> = {
  high: "bg-red-100 text-red-700 border-red-200",
  medium: "bg-amber-100 text-amber-700 border-amber-200",
  low: "bg-yellow-100 text-yellow-700 border-yellow-200",
  info: "bg-blue-100 text-blue-700 border-blue-200",
};

function ComplianceTab() {
  const { data: flags, loading, refetch } = useApi<any[]>("/api/provenance/surveillance");
  const [filter, setFilter] = useState<"all" | "open" | "resolved">("open");

  async function resolve(id: string, val: boolean) {
    await api(`/api/provenance/surveillance/${id}/resolve?resolved=${val}`, { method: "PATCH", body: {} });
    refetch();
  }

  const filtered = flags?.filter((f) =>
    filter === "all" ? true : filter === "open" ? !f.resolved : f.resolved
  );
  const openCount = flags?.filter((f) => !f.resolved).length ?? 0;

  return (
    <div className="max-w-5xl space-y-4">
      <div className="flex items-center gap-2 flex-wrap">
        {(["open", "all", "resolved"] as const).map((f) => (
          <button key={f} onClick={() => setFilter(f)}
            className={`px-3 py-1.5 rounded-lg text-sm font-medium transition ${filter === f ? "bg-navy-800 text-white" : "bg-navy-50 text-ink-soft hover:bg-navy-100"}`}>
            {titleCase(f)}{f === "open" && openCount > 0 && <span className="ml-1.5 bg-red-500 text-white text-[10px] rounded-full px-1.5 py-0.5">{openCount}</span>}
          </button>
        ))}
        <button className="ml-auto btn-outline py-1 px-2 text-xs flex items-center gap-1" onClick={() => refetch()}>
          <RefreshCw size={12} /> Refresh
        </button>
      </div>
      <Card className="overflow-x-auto p-0">
        {loading ? <div className="p-6"><Spinner /></div> : !filtered?.length ? (
          <div className="p-8"><Empty>{filter === "open" ? "No open compliance flags." : "No flags found."}</Empty></div>
        ) : (
          <table className="w-full text-sm">
            <thead>
              <tr className="border-b border-navy-100 text-left text-xs text-ink-muted font-medium">
                <th className="px-4 py-3">Severity</th>
                <th className="px-4 py-3">Category</th>
                <th className="px-4 py-3">Finding</th>
                <th className="px-4 py-3">Agent</th>
                <th className="px-4 py-3">Date</th>
                <th className="px-4 py-3 text-right">Action</th>
              </tr>
            </thead>
            <tbody>
              {filtered.map((f) => (
                <tr key={f.id} className={`border-b border-navy-50 last:border-0 hover:bg-navy-50/40 ${f.resolved ? "opacity-60" : ""}`}>
                  <td className="px-4 py-3">
                    <span className={`inline-flex px-2 py-0.5 rounded border text-[11px] font-semibold uppercase tracking-wide ${SEVERITY_COLORS[f.severity?.toLowerCase()] || SEVERITY_COLORS.info}`}>
                      {f.severity}
                    </span>
                  </td>
                  <td className="px-4 py-3 text-xs capitalize">{f.category?.replace(/_/g, " ")}</td>
                  <td className="px-4 py-3 text-ink max-w-xs">
                    <p className="line-clamp-2 text-xs">{f.finding}</p>
                    {f.auto_paused_agent && <span className="text-[10px] text-red-600 font-medium block mt-0.5">Agent auto-paused</span>}
                  </td>
                  <td className="px-4 py-3 text-xs text-ink-muted">{f.target_agent_key?.replace(/_/g, " ") || "—"}</td>
                  <td className="px-4 py-3 text-xs text-ink-muted whitespace-nowrap">{f.created_at ? new Date(f.created_at).toLocaleDateString() : "—"}</td>
                  <td className="px-4 py-3 text-right">
                    {f.resolved
                      ? <button className="btn-outline py-1 px-2 text-xs" onClick={() => resolve(f.id, false)}>Reopen</button>
                      : <button className="btn-outline py-1 px-2 text-xs text-positive flex items-center gap-1 ml-auto" onClick={() => resolve(f.id, true)}><Check size={11} /> Resolve</button>}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
      </Card>
    </div>
  );
}

// ── AI Usage tab ──────────────────────────────────────────────────────────────

function UsageTab() {
  const { data: usage, loading, refetch } = useApi<any>("/api/admin/usage");

  if (loading) return <Spinner />;
  if (!usage) return <Card><Empty>No usage data yet.</Empty></Card>;

  const byAgent: [string, number][] = Object.entries(usage.by_agent || {}).sort((a: any, b: any) => b[1] - a[1]);
  const byModel: [string, number][] = Object.entries(usage.by_model || {}).sort((a: any, b: any) => b[1] - a[1]);
  const maxAgent = byAgent[0]?.[1] || 1;
  const maxModel = byModel[0]?.[1] || 1;

  return (
    <div className="max-w-4xl space-y-5">
      <div className="grid grid-cols-2 sm:grid-cols-3 lg:grid-cols-5 gap-3">
        {[
          { label: "API calls", value: (usage.calls ?? 0).toLocaleString() },
          { label: "Total tokens", value: ((usage.total_tokens ?? 0) / 1000).toFixed(1) + "k" },
          { label: "Est. cost", value: `$${(usage.est_cost ?? 0).toFixed(2)}` },
          { label: "Fallback rate", value: `${((usage.fallback_rate ?? 0) * 100).toFixed(1)}%` },
          { label: "PII redacted", value: (usage.redacted_entities ?? 0).toLocaleString() },
        ].map(({ label, value }) => (
          <Card key={label} className="p-4 text-center">
            <div className="text-xl font-semibold text-navy-800">{value}</div>
            <div className="text-xs text-ink-muted mt-1">{label}</div>
          </Card>
        ))}
      </div>
      <div className="grid sm:grid-cols-2 gap-5">
        <Card>
          <div className="font-semibold text-ink mb-3 text-sm">Calls by agent</div>
          {byAgent.length === 0 ? <Empty>No agent calls yet.</Empty> : (
            <div className="space-y-2.5">
              {byAgent.slice(0, 10).map(([agent, count]) => (
                <div key={agent}>
                  <div className="flex justify-between text-xs text-ink mb-1">
                    <span className="truncate capitalize">{agent.replace(/_/g, " ")}</span>
                    <span className="font-medium ml-2 shrink-0">{count}</span>
                  </div>
                  <div className="h-1.5 rounded-full bg-navy-100">
                    <div className="h-1.5 rounded-full bg-navy-700 transition-all" style={{ width: `${(count / maxAgent) * 100}%` }} />
                  </div>
                </div>
              ))}
            </div>
          )}
        </Card>
        <Card>
          <div className="font-semibold text-ink mb-3 text-sm">Calls by model</div>
          {byModel.length === 0 ? <Empty>No model calls yet.</Empty> : (
            <div className="space-y-2.5">
              {byModel.map(([model, count]) => (
                <div key={model}>
                  <div className="flex justify-between text-xs text-ink mb-1">
                    <span className="truncate font-mono text-[11px]">{model}</span>
                    <span className="font-medium ml-2 shrink-0">{count}</span>
                  </div>
                  <div className="h-1.5 rounded-full bg-navy-100">
                    <div className="h-1.5 rounded-full bg-gold/80 transition-all" style={{ width: `${(count / maxModel) * 100}%` }} />
                  </div>
                </div>
              ))}
            </div>
          )}
        </Card>
      </div>
      <div className="text-xs text-ink-muted flex items-center justify-between">
        <span>Token costs are estimated based on published model pricing.</span>
        <button className="btn-outline py-1 px-2 text-xs flex items-center gap-1" onClick={() => refetch()}><RefreshCw size={11} /> Refresh</button>
      </div>
    </div>
  );
}

// ── Data Quality tab ──────────────────────────────────────────────────────────

const HEALTH_COLORS: Record<string, string> = {
  green: "bg-emerald-100 text-emerald-700 border-emerald-200",
  amber: "bg-amber-100 text-amber-700 border-amber-200",
  red:   "bg-red-100 text-red-700 border-red-200",
};

function DataQualityTab() {
  const { data, loading, error } = useApi<{
    summary: { total: number; green: number; amber: number; red: number };
    households: any[];
    days_since_sync: number | null;
  }>("/api/admin/data-quality");

  if (loading) return <Spinner />;
  if (error || !data) return <div className="text-sm text-red-600 p-4">{error || "Failed to load"}</div>;

  const { summary, households } = data;

  return (
    <div className="space-y-5">
      {/* Summary KPIs */}
      <div className="grid grid-cols-4 gap-4">
        {[
          { label: "Total households", value: summary.total, cls: "text-ink" },
          { label: "Healthy", value: summary.green, cls: "text-emerald-600" },
          { label: "Needs attention", value: summary.amber, cls: "text-amber-600" },
          { label: "Critical", value: summary.red, cls: "text-red-600" },
        ].map(({ label, value, cls }) => (
          <Card key={label} className="p-4 text-center">
            <div className={`text-2xl font-bold ${cls}`}>{value}</div>
            <div className="text-xs text-ink-muted mt-1">{label}</div>
          </Card>
        ))}
      </div>

      {data.days_since_sync !== null && (
        <div className="text-xs text-ink-muted">
          Last connector sync: <span className="font-medium text-ink">{data.days_since_sync}d ago</span>
        </div>
      )}

      {/* Household table */}
      <Card className="overflow-hidden">
        <div className="overflow-x-auto">
          <table className="w-full text-sm">
            <thead>
              <tr className="border-b border-border bg-surface-2 text-xs text-ink-muted uppercase tracking-wide">
                <th className="text-left px-4 py-2.5">Household</th>
                <th className="text-left px-4 py-2.5">Health</th>
                <th className="text-left px-4 py-2.5">Completeness</th>
                <th className="text-left px-4 py-2.5">Missing fields</th>
                <th className="text-left px-4 py-2.5">Mandates</th>
                <th className="text-left px-4 py-2.5">Members</th>
              </tr>
            </thead>
            <tbody className="divide-y divide-border">
              {households.map((hh: any) => (
                <tr key={hh.household_id} className="hover:bg-surface-2/50">
                  <td className="px-4 py-3 font-medium text-ink">{hh.name}</td>
                  <td className="px-4 py-3">
                    <span className={`inline-flex items-center px-2 py-0.5 rounded-full text-[11px] font-medium border ${HEALTH_COLORS[hh.health]}`}>
                      {hh.health === "green" ? "Healthy" : hh.health === "amber" ? "Attention" : "Critical"}
                    </span>
                  </td>
                  <td className="px-4 py-3">
                    <div className="flex items-center gap-2">
                      <div className="w-20 h-1.5 rounded-full bg-border overflow-hidden">
                        <div
                          className={`h-full rounded-full ${hh.completeness >= 0.9 ? "bg-emerald-500" : hh.completeness >= 0.6 ? "bg-amber-500" : "bg-red-500"}`}
                          style={{ width: `${Math.round(hh.completeness * 100)}%` }}
                        />
                      </div>
                      <span className="text-xs text-ink-muted">{Math.round(hh.completeness * 100)}%</span>
                    </div>
                  </td>
                  <td className="px-4 py-3">
                    {hh.missing_fields.length === 0 ? (
                      <span className="text-xs text-emerald-600">None</span>
                    ) : (
                      <span className="text-xs text-red-600">{hh.missing_fields.join(", ")}</span>
                    )}
                  </td>
                  <td className="px-4 py-3 text-xs text-ink-muted">{hh.mandate_count}</td>
                  <td className="px-4 py-3 text-xs text-ink-muted">{hh.person_count}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </Card>
    </div>
  );
}

// ── Schedules tab ─────────────────────────────────────────────────────────────

function SchedulesTab() {
  const { data, loading, error, refetch } = useApi<any[]>("/api/admin/schedules");
  const [editing, setEditing] = useState<{ key: string; hours: number } | null>(null);
  const [saving, setSaving] = useState(false);

  if (loading) return <Spinner />;
  if (error || !data) return <div className="text-sm text-red-600 p-4">{error || "Failed to load"}</div>;

  async function saveSchedule() {
    if (!editing) return;
    setSaving(true);
    try {
      await api(`/api/admin/schedules/${editing.key}`, { method: "PATCH", body: { interval_hours: editing.hours } });
      setEditing(null);
      refetch();
    } catch (e: any) {
      alert(e.message);
    } finally {
      setSaving(false);
    }
  }

  function fmtHours(h: number) {
    if (h < 1) return `${Math.round(h * 60)}m`;
    if (h === 1) return "1 hr";
    return `${h} hrs`;
  }

  function fmtDate(iso: string | null) {
    if (!iso) return "Never";
    const d = new Date(iso);
    return d.toLocaleString(undefined, { month: "short", day: "numeric", hour: "2-digit", minute: "2-digit" });
  }

  return (
    <div className="space-y-5">
      <p className="text-sm text-ink-muted">
        Configure how often each background agent job runs. Changes take effect on the next worker restart.
      </p>

      <Card className="overflow-hidden">
        <div className="overflow-x-auto">
          <table className="w-full text-sm">
            <thead>
              <tr className="border-b border-border bg-surface-2 text-xs text-ink-muted uppercase tracking-wide">
                <th className="text-left px-4 py-2.5">Agent job</th>
                <th className="text-left px-4 py-2.5">Current interval</th>
                <th className="text-left px-4 py-2.5">Default</th>
                <th className="text-left px-4 py-2.5">Last run</th>
                <th className="text-left px-4 py-2.5">Run count</th>
                <th className="text-left px-4 py-2.5">Status</th>
                <th className="px-4 py-2.5" />
              </tr>
            </thead>
            <tbody className="divide-y divide-border">
              {data.map((s: any) => (
                <tr key={s.agent_key} className="hover:bg-surface-2/50">
                  <td className="px-4 py-3 font-medium text-ink">{s.label}</td>
                  <td className="px-4 py-3">
                    {editing?.key === s.agent_key ? (
                      <div className="flex items-center gap-2">
                        <input
                          type="number" min={0.25} max={168} step={0.25}
                          value={editing.hours}
                          onChange={(e) => setEditing({ ...editing, hours: Number(e.target.value) })}
                          className="input w-20 py-1 text-sm"
                        />
                        <span className="text-xs text-ink-muted">hrs</span>
                        <button onClick={saveSchedule} disabled={saving} className="btn-primary text-xs py-1 px-2">
                          {saving ? "…" : "Save"}
                        </button>
                        <button onClick={() => setEditing(null)} className="btn-outline text-xs py-1 px-2">Cancel</button>
                      </div>
                    ) : (
                      <span className="font-mono text-ink">{fmtHours(s.interval_hours)}</span>
                    )}
                  </td>
                  <td className="px-4 py-3 text-xs text-ink-muted">{fmtHours(s.default_hours)}</td>
                  <td className="px-4 py-3 text-xs text-ink-muted">{fmtDate(s.last_run_at)}</td>
                  <td className="px-4 py-3 text-xs text-ink-muted">{s.run_count.toLocaleString()}</td>
                  <td className="px-4 py-3">
                    {s.paused ? (
                      <span className="text-xs text-amber-600 font-medium">Paused</span>
                    ) : s.enabled ? (
                      <span className="text-xs text-emerald-600 font-medium">Active</span>
                    ) : (
                      <span className="text-xs text-ink-muted">Disabled</span>
                    )}
                  </td>
                  <td className="px-4 py-3 text-right">
                    {editing?.key !== s.agent_key && (
                      <button
                        onClick={() => setEditing({ key: s.agent_key, hours: s.interval_hours })}
                        className="text-navy-600 hover:text-navy-800 text-xs font-medium"
                      >
                        Edit
                      </button>
                    )}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </Card>
    </div>
  );
}

// ── I4: Rule Impact Tab ───────────────────────────────────────────────────────

function RuleImpactTab() {
  const { data: complianceData, loading: compLoading } = useApi<any>("/api/admin/compliance");
  const [ruleCode, setRuleCode] = useState("");
  const [result, setResult] = useState<any>(null);
  const [busy, setBusy] = useState(false);

  async function check() {
    if (!ruleCode.trim()) return;
    setBusy(true);
    setResult(null);
    try {
      const data = await api<any>(`/api/admin/compliance/impact?rule_code=${encodeURIComponent(ruleCode.trim())}`);
      setResult(data);
    } catch (e: any) {
      setResult({ error: e.message });
    }
    setBusy(false);
  }

  return (
    <div className="space-y-4">
      <Card className="p-5">
        <div className="font-semibold text-ink mb-4 flex items-center gap-2">
          <Search size={16} className="text-navy-600" /> Rule Change Impact Analysis
        </div>
        <p className="text-sm text-ink-muted mb-4">
          Enter a rule code to see which recommendations in your firm's history triggered a finding for that rule.
          Use this when a regulatory rule changes to assess your exposure.
        </p>
        <div className="flex gap-3 mb-4">
          <div className="flex-1">
            <input
              className="input"
              placeholder="Rule code e.g. NZ-FMA-001"
              value={ruleCode}
              onChange={(e) => setRuleCode(e.target.value)}
              onKeyDown={(e) => e.key === "Enter" && check()}
            />
          </div>
          <button className="btn-primary" onClick={check} disabled={busy || !ruleCode.trim()}>
            {busy ? "Checking…" : "Check impact"}
          </button>
        </div>

        {!compLoading && complianceData?.rules && (
          <div className="flex flex-wrap gap-1.5">
            <span className="text-xs text-ink-muted mr-1">Quick select:</span>
            {complianceData.rules.slice(0, 8).map((r: any) => (
              <button key={r.code} onClick={() => setRuleCode(r.code)}
                      className="chip bg-navy-50 text-navy-700 hover:bg-navy-100 border border-navy-100 text-xs">
                {r.code}
              </button>
            ))}
          </div>
        )}
      </Card>

      {result && !result.error && (
        <Card className="p-5">
          <div className="flex items-center gap-2 mb-4">
            {result.affected_count > 0
              ? <AlertCircle size={16} className="text-caution" />
              : <CheckCircle2 size={16} className="text-positive" />}
            <span className="font-semibold text-ink">
              {result.affected_count > 0
                ? `${result.affected_count} recommendation(s) affected by rule ${result.rule_code}`
                : `No recommendations affected by rule ${result.rule_code}`}
            </span>
          </div>
          {result.affected_recommendations?.length > 0 && (
            <div className="overflow-x-auto">
              <table className="w-full text-sm">
                <thead className="bg-navy-50 border-b border-navy-100">
                  <tr>
                    <th className="text-left px-3 py-2 text-xs text-ink-muted">Title</th>
                    <th className="text-left px-3 py-2 text-xs text-ink-muted">Agent</th>
                    <th className="text-left px-3 py-2 text-xs text-ink-muted">Status</th>
                    <th className="text-left px-3 py-2 text-xs text-ink-muted">Subject</th>
                  </tr>
                </thead>
                <tbody className="divide-y divide-navy-50">
                  {result.affected_recommendations.map((r: any) => (
                    <tr key={r.id} className="hover:bg-navy-50/50">
                      <td className="px-3 py-2.5 text-ink">{r.title}</td>
                      <td className="px-3 py-2.5 text-ink-muted">{r.agent_key?.replace(/_/g, " ")}</td>
                      <td className="px-3 py-2.5"><span className="chip text-xs bg-caution/10 text-caution">{r.status}</span></td>
                      <td className="px-3 py-2.5 text-ink-muted">{r.subject_label || "—"}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}
        </Card>
      )}
      {result?.error && (
        <Card className="p-5 bg-critical/5 border-critical/20">
          <p className="text-sm text-critical">{result.error}</p>
        </Card>
      )}
    </div>
  );
}

// ── I7: Branding Editor Tab ───────────────────────────────────────────────────

function BrandingTab() {
  const { data, loading, refetch } = useApi<any>("/api/admin/firm/branding");
  const [form, setForm] = useState<any>(null);
  const [saving, setSaving] = useState(false);
  const [saved, setSaved] = useState(false);

  if (loading) return <Spinner />;
  const b = form || data?.branding || {};

  async function save() {
    setSaving(true);
    try {
      await api("/api/admin/firm/branding", { method: "PATCH", body: form || {} });
      setSaved(true);
      refetch();
      setTimeout(() => setSaved(false), 2500);
    } catch {}
    setSaving(false);
  }

  function set(key: string, val: string) {
    setForm((f: any) => ({ ...(f || b), [key]: val }));
  }

  const preview = form || b;

  return (
    <div className="grid md:grid-cols-2 gap-5">
      <div className="space-y-4">
        <Card className="p-5">
          <div className="font-semibold text-ink mb-4 flex items-center gap-2">
            <Paintbrush size={16} className="text-navy-600" /> Brand settings
          </div>
          <div className="space-y-4">
            <div>
              <label className="label">Logo / firm name displayed</label>
              <input className="input" value={preview.logo_text || ""} onChange={(e) => set("logo_text", e.target.value)} placeholder="e.g. Aurea Wealth" />
            </div>
            <div>
              <label className="label">Tagline</label>
              <input className="input" value={preview.tagline || ""} onChange={(e) => set("tagline", e.target.value)} placeholder="e.g. Truly personal advice, at scale." />
            </div>
            <div className="grid grid-cols-2 gap-3">
              <div>
                <label className="label">Primary colour</label>
                <div className="flex gap-2 items-center">
                  <input type="color" className="h-9 w-12 rounded border border-navy-200 cursor-pointer" value={preview.primary || "#163a52"} onChange={(e) => set("primary", e.target.value)} />
                  <input className="input flex-1" value={preview.primary || "#163a52"} onChange={(e) => set("primary", e.target.value)} placeholder="#163a52" />
                </div>
              </div>
              <div>
                <label className="label">Accent colour</label>
                <div className="flex gap-2 items-center">
                  <input type="color" className="h-9 w-12 rounded border border-navy-200 cursor-pointer" value={preview.accent || "#c8a35e"} onChange={(e) => set("accent", e.target.value)} />
                  <input className="input flex-1" value={preview.accent || "#c8a35e"} onChange={(e) => set("accent", e.target.value)} placeholder="#c8a35e" />
                </div>
              </div>
            </div>
          </div>
          <div className="flex gap-2 mt-5">
            <button className="btn-primary" onClick={save} disabled={saving || !form}>
              {saving ? "Saving…" : saved ? "Saved ✓" : <><Save size={14} /> Save branding</>}
            </button>
            {form && <button className="btn-outline" onClick={() => setForm(null)}>Reset</button>}
          </div>
        </Card>
      </div>

      {/* Live preview */}
      <div className="space-y-4">
        <Card className="p-5">
          <div className="font-semibold text-ink mb-3 text-sm">Canvas header preview</div>
          <div className="rounded-xl p-5 text-white" style={{ background: preview.primary || "#163a52" }}>
            <div className="text-sm opacity-60">{preview.logo_text || "Your Firm"}</div>
            <div className="font-serif text-xl mt-1">Welcome back, Client</div>
            <div className="text-sm opacity-70 mt-1">{preview.tagline || "Truly personal advice, at scale."}</div>
            <div className="mt-3 inline-block px-3 py-1.5 rounded-lg text-sm font-medium" style={{ background: preview.accent || "#c8a35e", color: "#fff" }}>
              View your wealth →
            </div>
          </div>
        </Card>
        <Card className="p-5">
          <div className="font-semibold text-ink mb-3 text-sm">Accent colour preview</div>
          <div className="flex gap-3 items-center">
            <div className="h-10 w-10 rounded-xl" style={{ background: preview.accent || "#c8a35e" }} />
            <div>
              <div className="font-medium" style={{ color: preview.accent || "#c8a35e" }}>{preview.logo_text || "Aurea"}</div>
              <div className="text-xs text-ink-muted">Used for buttons, highlights, and accents</div>
            </div>
          </div>
        </Card>
      </div>
    </div>
  );
}
