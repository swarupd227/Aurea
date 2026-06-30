"use client";
import { useEffect, useState } from "react";
import { ShieldCheck, BookOpen, CheckCircle2, Network, Lock, BarChart3, AlertTriangle, Cpu, Save, Settings2 } from "lucide-react";
import PageHeader from "@/components/PageHeader";
import { Card, Spinner } from "@/components/ui";
import { useApi } from "@/lib/hooks";
import { api } from "@/lib/api";

const ICONS: Record<string, any> = {
  governance: ShieldCheck, grounding: BookOpen, eval: CheckCircle2,
  model_gateway: Network, security: Lock, telemetry: BarChart3,
};
const CATS = ["names", "accounts", "emails", "ids"];

export default function Foundation() {
  const { data, loading, refetch } = useApi<any>("/api/admin/foundation");
  const [cfg, setCfg] = useState<any>(null);
  const [saving, setSaving] = useState(false);
  const [saved, setSaved] = useState(false);

  useEffect(() => { if (data?.policy && !cfg) setCfg({ ...data.policy }); }, [data, cfg]);
  if (loading || !data || !cfg) return <Spinner />;

  const usage = data.usage || {};
  const ev = data.eval || {};
  const set = (k: string, v: any) => setCfg({ ...cfg, [k]: v });
  const toggleCat = (c: string) => {
    const has = (cfg.pii_categories || []).includes(c);
    set("pii_categories", has ? cfg.pii_categories.filter((x: string) => x !== c) : [...(cfg.pii_categories || []), c]);
  };

  async function save() {
    setSaving(true);
    try {
      await api("/api/admin/foundation", { method: "PUT", body: { policy: cfg } });
      setSaved(true); setTimeout(() => setSaved(false), 1500); refetch();
    } finally { setSaving(false); }
  }

  return (
    <div>
      <PageHeader title="The common foundation"
        sub="The scaffold every agent inherits — and every knob below changes real behaviour."
        actions={<button className="btn-primary" onClick={save} disabled={saving}><Save size={16} /> {saving ? "Saving…" : saved ? "Saved" : "Save configuration"}</button>} />

      {/* Six pillars */}
      <div className="grid md:grid-cols-2 gap-4 mb-6">
        {(data.pillars || []).map((p: any) => {
          const Icon = ICONS[p.key] || Cpu;
          const strong = p.status === "strong";
          return (
            <Card key={p.key}>
              <div className="flex items-start gap-3">
                <span className={`h-10 w-10 rounded-xl flex items-center justify-center shrink-0 ${strong ? "bg-positive/10 text-positive" : "bg-caution/10 text-caution"}`}><Icon size={20} /></span>
                <div className="flex-1 min-w-0">
                  <div className="flex items-center gap-2">
                    <h3 className="font-semibold text-ink">{p.title}</h3>
                    <span className={`chip ${strong ? "bg-positive/10 text-positive" : "bg-caution/10 text-caution"}`}>
                      {strong ? <><CheckCircle2 size={11} /> Inherited</> : <><AlertTriangle size={11} /> {p.status === "attention" ? "Attention" : "Partial"}</>}
                    </span>
                  </div>
                  <p className="text-sm text-ink-soft mt-0.5">{p.summary}</p>
                  <p className="text-xs text-ink-muted mt-1.5">{p.detail}</p>
                </div>
              </div>
            </Card>
          );
        })}
      </div>

      {/* Configuration — wired controls */}
      <Card className="mb-6">
        <div className="font-semibold text-ink mb-1 flex items-center gap-2"><Settings2 size={17} /> Policy configuration</div>
        <p className="text-xs text-ink-muted mb-4">Stored per firm and read by the engines on every run — these are live controls, not labels.</p>
        <div className="grid md:grid-cols-2 lg:grid-cols-3 gap-x-6 gap-y-5">

          <Group title="Model gateway" icon={Network}>
            <Toggle label="Fallback to secondary provider" v={cfg.fallback_enabled} on={(b) => set("fallback_enabled", b)} />
            <Num label="Monthly cost cap (USD, 0 = none)" v={cfg.monthly_cost_cap_usd} on={(n) => set("monthly_cost_cap_usd", n)} step={5} />
            <Num label="Max tokens per call" v={cfg.max_tokens_default} on={(n) => set("max_tokens_default", n)} step={100} />
            <div className="text-[11px] text-ink-muted">Per-task model selection is in the Models tab.</div>
          </Group>

          <Group title="Security & compliance" icon={Lock}>
            <Toggle label="PII redaction before model calls" v={cfg.pii_redaction} on={(b) => set("pii_redaction", b)} />
            <div>
              <div className="label">Mask categories</div>
              <div className="flex flex-wrap gap-2 mt-1">
                {CATS.map((c) => (
                  <button key={c} onClick={() => toggleCat(c)}
                    className={`chip ${(cfg.pii_categories || []).includes(c) ? "bg-navy-800 text-white" : "bg-navy-50 text-ink-muted"}`}>{c}</button>
                ))}
              </div>
            </div>
            <Num label="Block actions below confidence (%)" v={Math.round((cfg.min_confidence || 0) * 100)} on={(n) => set("min_confidence", n / 100)} step={5} />
          </Group>

          <Group title="Governance & guardrails" icon={ShieldCheck}>
            <Toggle label="Require human approval everywhere" v={cfg.require_approval_everywhere} on={(b) => set("require_approval_everywhere", b)} />
            <Num label="Default CGT budget (USD)" v={cfg.default_cgt_budget} on={(n) => set("default_cgt_budget", n)} step={1000} />
            <Num label="Max turnover (%, 0 = none)" v={Math.round((cfg.max_turnover_pct || 0) * 100)} on={(n) => set("max_turnover_pct", n / 100)} step={5} />
          </Group>

          <Group title="Eval & quality gates" icon={CheckCircle2}>
            <Toggle label="Block model/config change unless gates green" v={cfg.enforce_eval_gate} on={(b) => set("enforce_eval_gate", b)} />
            <div className="text-xs text-ink-muted">Gates now: <b className={ev.all_green ? "text-positive" : "text-caution"}>{ev.passed}/{ev.total} green</b></div>
          </Group>

          <Group title="Grounding & context" icon={BookOpen}>
            <Toggle label="Require firm-research citations" v={cfg.require_grounding} on={(b) => set("require_grounding", b)} />
            <Num label="RAG retrieval depth (top-k)" v={cfg.rag_top_k} on={(n) => set("rag_top_k", n)} step={1} />
          </Group>

        </div>
      </Card>

      {/* Per-agent overrides */}
      <PerAgentOverrides firmPolicy={data.policy} />

      {/* Telemetry + eval status */}
      <div className="grid lg:grid-cols-2 gap-5">
        <Card>
          <div className="font-semibold text-ink mb-3 flex items-center gap-2"><BarChart3 size={17} /> AI usage & cost</div>
          <div className="grid grid-cols-3 gap-3 mb-4">
            <Stat label="Model calls" value={usage.calls ?? 0} />
            <Stat label="Tokens" value={(usage.total_tokens ?? 0).toLocaleString()} />
            <Stat label="Est. cost" value={`$${(usage.est_cost ?? 0).toFixed(2)}`} />
          </div>
          <Mix title="By model" data={usage.by_model} total={usage.calls} />
          <Mix title="By agent" data={usage.by_agent} total={usage.calls} />
          <div className="mt-3 text-xs text-ink-muted">Fallback {(((usage.fallback_rate ?? 0) * 100)).toFixed(0)}% · {usage.redacted_entities ?? 0} PII entities masked. Prices indicative.</div>
        </Card>
        <Card>
          <div className="flex items-center justify-between mb-3">
            <div className="font-semibold text-ink flex items-center gap-2"><CheckCircle2 size={17} /> Eval gates · {ev.agent}</div>
            <span className={`chip ${ev.all_green ? "bg-positive/10 text-positive" : "bg-caution/10 text-caution"}`}>{ev.passed}/{ev.total} green</span>
          </div>
          <div className="space-y-1.5">
            {(ev.cases || []).map((c: any) => (
              <div key={c.key} className="flex items-center gap-2 text-sm">
                {c.passed ? <CheckCircle2 size={14} className="text-positive shrink-0" /> : <AlertTriangle size={14} className="text-critical shrink-0" />}
                <span className="text-ink-soft">{c.name}</span>
                <span className="chip bg-navy-50 text-ink-muted ml-auto">{c.category}</span>
              </div>
            ))}
          </div>
        </Card>
      </div>
    </div>
  );
}

function PerAgentOverrides({ firmPolicy }: { firmPolicy: any }) {
  const { data: cat } = useApi<any[]>("/api/agents/catalogue");
  const { data: cfgs, refetch } = useApi<any[]>("/api/admin/agents");
  const [open, setOpen] = useState<string | null>(null);
  const [draft, setDraft] = useState<any>({});
  const [busy, setBusy] = useState(false);
  if (!cat) return null;
  const byKey: Record<string, any> = {};
  (cfgs || []).forEach((c) => (byKey[c.agent_key] = c.config?.foundation || {}));

  function edit(key: string) {
    const f = byKey[key] || {};
    setDraft({
      monthly_cost_cap_usd: f.monthly_cost_cap_usd ?? "",
      min_confidence: f.min_confidence != null ? Math.round(f.min_confidence * 100) : "",
      max_tokens_default: f.max_tokens_default ?? "",
      rag_top_k: f.rag_top_k ?? "",
      require_approval_everywhere: f.require_approval_everywhere === undefined ? "" : String(f.require_approval_everywhere),
      require_grounding: f.require_grounding === undefined ? "" : String(f.require_grounding),
    });
    setOpen(open === key ? null : key);
  }
  async function save(key: string) {
    setBusy(true);
    const f: any = {};
    if (draft.monthly_cost_cap_usd !== "") f.monthly_cost_cap_usd = Number(draft.monthly_cost_cap_usd);
    if (draft.min_confidence !== "") f.min_confidence = Number(draft.min_confidence) / 100;
    if (draft.max_tokens_default !== "") f.max_tokens_default = Number(draft.max_tokens_default);
    if (draft.rag_top_k !== "") f.rag_top_k = Number(draft.rag_top_k);
    if (draft.require_approval_everywhere !== "") f.require_approval_everywhere = draft.require_approval_everywhere === "true";
    if (draft.require_grounding !== "") f.require_grounding = draft.require_grounding === "true";
    try { await api(`/api/admin/agents/${key}`, { method: "PATCH", body: { config: { foundation: f } } }); setOpen(null); refetch(); }
    finally { setBusy(false); }
  }

  return (
    <Card className="mb-6">
      <div className="font-semibold text-ink mb-1 flex items-center gap-2"><Cpu size={17} /> Per-agent overrides</div>
      <p className="text-xs text-ink-muted mb-3">Tune any agent independently — blank fields inherit the firm policy above.</p>
      <div className="divide-y divide-navy-50">
        {cat.map((a) => {
          const ov = byKey[a.agent_key] || {};
          const n = Object.keys(ov).length;
          const isOpen = open === a.agent_key;
          return (
            <div key={a.agent_key} className="py-2">
              <button onClick={() => edit(a.agent_key)} className="w-full flex items-center gap-2 text-left">
                <span className="text-sm font-medium text-ink flex-1">{a.name}</span>
                {n > 0 ? <span className="chip bg-gold-soft/40 text-gold-dark">{n} override{n > 1 ? "s" : ""}</span>
                       : <span className="chip bg-navy-50 text-ink-muted">inherits firm</span>}
                <span className="text-xs text-ink-muted">{isOpen ? "Close" : "Edit"}</span>
              </button>
              {isOpen && (
                <div className="mt-3 grid sm:grid-cols-3 gap-3">
                  <OvNum label={`Cost cap $ (firm ${firmPolicy.monthly_cost_cap_usd || 0})`} v={draft.monthly_cost_cap_usd} on={(x: any) => setDraft({ ...draft, monthly_cost_cap_usd: x })} />
                  <OvNum label={`Min confidence % (firm ${Math.round((firmPolicy.min_confidence || 0) * 100)})`} v={draft.min_confidence} on={(x: any) => setDraft({ ...draft, min_confidence: x })} />
                  <OvNum label={`Max tokens (firm ${firmPolicy.max_tokens_default})`} v={draft.max_tokens_default} on={(x: any) => setDraft({ ...draft, max_tokens_default: x })} />
                  <OvNum label={`RAG top-k (firm ${firmPolicy.rag_top_k})`} v={draft.rag_top_k} on={(x: any) => setDraft({ ...draft, rag_top_k: x })} />
                  <OvSel label="Require approval" v={draft.require_approval_everywhere} on={(x: any) => setDraft({ ...draft, require_approval_everywhere: x })} />
                  <OvSel label="Require grounding" v={draft.require_grounding} on={(x: any) => setDraft({ ...draft, require_grounding: x })} />
                  <div className="sm:col-span-3 flex justify-end">
                    <button className="btn-primary text-sm" disabled={busy} onClick={() => save(a.agent_key)}><Save size={14} /> {busy ? "Saving…" : "Save override"}</button>
                  </div>
                </div>
              )}
            </div>
          );
        })}
      </div>
    </Card>
  );
}
function OvNum({ label, v, on }: any) {
  return (
    <div>
      <label className="label">{label}</label>
      <input type="number" className="input" placeholder="inherit" value={v} onChange={(e) => on(e.target.value)} />
    </div>
  );
}
function OvSel({ label, v, on }: any) {
  return (
    <div>
      <label className="label">{label}</label>
      <select className="input" value={v} onChange={(e) => on(e.target.value)}>
        <option value="">Inherit</option><option value="true">On</option><option value="false">Off</option>
      </select>
    </div>
  );
}

function Group({ title, icon: Icon, children }: any) {
  return (
    <div className="space-y-2.5">
      <div className="tile-label flex items-center gap-1.5"><Icon size={13} /> {title}</div>
      {children}
    </div>
  );
}
function Toggle({ label, v, on }: { label: string; v: boolean; on: (b: boolean) => void }) {
  return (
    <div className="flex items-center justify-between gap-2">
      <span className="text-sm text-ink-soft">{label}</span>
      <button onClick={() => on(!v)} className={`h-6 w-11 rounded-full transition-colors relative shrink-0 ${v ? "bg-positive" : "bg-navy-200"}`}>
        <span className={`absolute top-1 h-4 w-4 rounded-full bg-white transition-all ${v ? "left-6" : "left-1"}`} />
      </button>
    </div>
  );
}
function Num({ label, v, on, step = 1 }: { label: string; v: number; on: (n: number) => void; step?: number }) {
  return (
    <div>
      <label className="label">{label}</label>
      <input type="number" className="input" step={step} value={v ?? 0} onChange={(e) => on(Number(e.target.value))} />
    </div>
  );
}
function Stat({ label, value }: { label: string; value: any }) {
  return (
    <div className="rounded-lg border border-navy-100 bg-surface p-3">
      <div className="text-[11px] uppercase tracking-wide text-ink-muted">{label}</div>
      <div className="text-lg font-semibold text-ink tabular-nums">{value}</div>
    </div>
  );
}
function Mix({ title, data, total }: { title: string; data: Record<string, number>; total: number }) {
  const entries = Object.entries(data || {}).slice(0, 5);
  if (entries.length === 0) return null;
  return (
    <div className="mb-3">
      <div className="tile-label mb-1">{title}</div>
      <div className="space-y-1">
        {entries.map(([k, n]) => (
          <div key={k} className="flex items-center gap-2 text-xs">
            <span className="text-ink-soft w-40 truncate">{k.replace(/_/g, " ")}</span>
            <div className="flex-1 h-2 rounded-full bg-navy-50 overflow-hidden"><div className="h-full bg-navy-400" style={{ width: `${total ? (n / total) * 100 : 0}%` }} /></div>
            <span className="text-ink-muted tabular-nums w-8 text-right">{n}</span>
          </div>
        ))}
      </div>
    </div>
  );
}
