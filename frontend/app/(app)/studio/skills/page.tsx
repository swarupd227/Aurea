"use client";
import { useState } from "react";
import {
  Wand2, Play, FlaskConical, Plus, Trash2, ShieldCheck, Loader2,
  Pencil, Copy, Users, Globe, Lock, CheckCircle2, XCircle, Zap, AlertTriangle,
} from "lucide-react";
import PageHeader from "@/components/PageHeader";
import { Card, SkeletonList, Empty, InlineConfirmButton } from "@/components/ui";
import RecommendationCard from "@/components/RecommendationCard";
import { useApi } from "@/lib/hooks";
import { api } from "@/lib/api";
import { streamSkill } from "@/lib/stream";

const BLANK = {
  name: "", description: "", instruction: "", scope: "book",
  output_kind: "insight", default_tier: "tier_1",
  visibility: "private", shared_with: [] as string[],
};
const FILTERS = [["mine", "My skills"], ["shared", "Shared with me"], ["public", "Firm library"], ["all", "All"]];

const SCOPE_NOTES: Record<string, string> = {
  book: "Runs against every client in your book (~2 min). Best for book-wide sweeps.",
  household: "Run against a single client. Use 'Test' to trial before applying.",
};

type LogLine =
  | { kind: "start"; skill: string; total: number; test: boolean }
  | { kind: "scan"; index: number; total: number; household: string }
  | { kind: "result"; index: number; total: number; household: string; applies: boolean; title?: string }
  | { kind: "done"; scanned: number; surfaced: number }
  | { kind: "error"; message: string };

type SkillResult = {
  mode: "test" | "run";
  log: LogLine[];
  scanned: number;
  surfaced: number;
  total: number;
  proposals?: any[];
  recommendations?: any[];
  streaming: boolean;
};

function SkillLog({ log, streaming, scanned, total }: { log: LogLine[]; streaming: boolean; scanned: number; total: number }) {
  return (
    <div>
      {/* Progress bar */}
      {(streaming || scanned > 0) && total > 0 && (
        <div className="mb-2">
          <div className="flex items-center justify-between text-[11px] text-ink-muted mb-1">
            <span>{scanned} / {total} households</span>
            <span>{Math.round((scanned / total) * 100)}%</span>
          </div>
          <div className="h-1.5 rounded-full bg-navy-100 overflow-hidden">
            <div
              className={`h-full rounded-full transition-all duration-300 ${streaming ? "bg-gold" : "bg-positive"}`}
              style={{ width: `${Math.round((scanned / total) * 100)}%` }}
            />
          </div>
        </div>
      )}
      <div className="rounded-lg bg-navy-950 text-[12px] font-mono p-3 space-y-1 max-h-52 overflow-y-auto">
        {log.map((line, i) => {
          if (line.kind === "start")
            return <div key={i} className="text-gold">▶ {line.test ? "[TEST] " : ""}{line.skill} — scanning {line.total} household(s)…</div>;
          if (line.kind === "scan")
            return <div key={i} className="text-navy-300">  [{line.index}/{line.total}] Asking Claude about <span className="text-white">{line.household}</span>…</div>;
          if (line.kind === "result")
            return (
              <div key={i} className={line.applies ? "text-positive" : "text-navy-400"}>
                {line.applies
                  ? <span className="inline-flex items-center gap-1"><CheckCircle2 size={11} /> {line.household} — {line.title}</span>
                  : <span className="inline-flex items-center gap-1"><XCircle size={11} /> {line.household} — no match</span>}
              </div>
            );
          if (line.kind === "done")
            return <div key={i} className="text-gold mt-1">✓ Done · scanned {line.scanned} · {line.surfaced} surfaced</div>;
          if (line.kind === "error")
            return <div key={i} className="text-critical flex items-center gap-1"><AlertTriangle size={11} /> {line.message}</div>;
          return null;
        })}
        {streaming && <div className="text-navy-400 animate-pulse">…</div>}
      </div>
    </div>
  );
}

export default function Skills() {
  const { data: skills, loading, refetch } = useApi<any[]>("/api/skills");
  const { data: colleagues } = useApi<any[]>("/api/skills/colleagues");
  const [filter, setFilter] = useState("mine");
  const [editing, setEditing] = useState<null | string>(null);
  const [form, setForm] = useState<any>(BLANK);
  const [res, setRes] = useState<Record<string, SkillResult>>({});
  const [busy, setBusy] = useState<string | null>(null);

  function openNew() { setForm(BLANK); setEditing("new"); }
  function openEdit(s: any) {
    setForm({
      name: s.name, description: s.description || "", instruction: s.instruction,
      scope: s.scope, output_kind: s.output_kind, default_tier: s.default_tier,
      visibility: s.visibility, shared_with: s.shared_with || [],
    });
    setEditing(s.id);
  }
  async function save() {
    setBusy("save");
    try {
      if (editing === "new") await api("/api/skills", { body: form });
      else await api(`/api/skills/${editing}`, { method: "PATCH", body: form });
      setEditing(null); setForm(BLANK); refetch();
    } finally { setBusy(null); }
  }

  async function runStream(id: string, testMode: boolean) {
    const key = (testMode ? "test:" : "run:") + id;
    setBusy(key);
    setRes((r) => ({ ...r, [id]: { mode: testMode ? "test" : "run", log: [], scanned: 0, surfaced: 0, total: 0, streaming: true } }));
    try {
      await streamSkill({ skill_id: id, test: testMode }, (ev) => {
        setRes((r) => {
          const prev = r[id] || { mode: testMode ? "test" : "run", log: [], scanned: 0, surfaced: 0, total: 0, streaming: true };
          if (ev.phase === "start")
            return { ...r, [id]: { ...prev, total: ev.scanned, log: [...prev.log, { kind: "start", skill: ev.skill, total: ev.scanned, test: ev.test }] } };
          if (ev.phase === "scan")
            return { ...r, [id]: { ...prev, scanned: ev.index, log: [...prev.log, { kind: "scan", index: ev.index, total: ev.total, household: ev.household }] } };
          if (ev.phase === "result")
            return { ...r, [id]: { ...prev, log: [...prev.log, { kind: "result", index: ev.index, total: ev.total, household: ev.household, applies: ev.applies, title: ev.title }] } };
          if (ev.phase === "done")
            return {
              ...r, [id]: {
                ...prev, streaming: false,
                scanned: ev.scanned, surfaced: ev.surfaced,
                proposals: ev.proposals, recommendations: ev.recommendations,
                log: [...prev.log, { kind: "done", scanned: ev.scanned, surfaced: ev.surfaced }],
              },
            };
          if (ev.phase === "error")
            return { ...r, [id]: { ...prev, streaming: false, log: [...prev.log, { kind: "error", message: ev.message }] } };
          return r;
        });
      });
    } finally { setBusy(null); }
  }

  async function toggle(s: any) { await api(`/api/skills/${s.id}`, { method: "PATCH", body: { enabled: !s.enabled } }); refetch(); }
  async function del(id: string) { await api(`/api/skills/${id}`, { method: "DELETE" }); refetch(); }
  async function clone(id: string) { setBusy("clone:" + id); try { await api(`/api/skills/${id}/clone`, { body: {} }); setFilter("mine"); refetch(); } finally { setBusy(null); } }
  function toggleShare(uid: string) { const has = form.shared_with.includes(uid); setForm({ ...form, shared_with: has ? form.shared_with.filter((x: string) => x !== uid) : [...form.shared_with, uid] }); }

  if (loading) return <SkeletonList count={3} />;

  // Firm library shows all public skills (including your own published ones)
  const filtered = (skills || []).filter((s) =>
    filter === "all" ? true
    : filter === "mine" ? s.mine
    : filter === "shared" ? (!s.mine && s.visibility === "shared")
    : s.visibility === "public"  // firm library: any public skill regardless of ownership
  );

  return (
    <div>
      <PageHeader
        title="Skills"
        sub="Build your own agents in plain English. Save them to your workspace, share with colleagues, or publish to the firm — each runs with Claude under full governance."
        actions={<button className="btn-gold" onClick={openNew}><Plus size={16} /> New skill</button>}
      />

      <div className="flex gap-2 mb-4">
        {FILTERS.map(([k, label]) => (
          <button key={k} onClick={() => setFilter(k)}
            className={`chip ${filter === k ? "bg-navy-800 text-white" : "bg-navy-50 text-ink-muted"}`}>{label}</button>
        ))}
      </div>

      {editing && (
        <Card className="mb-5 border-gold/40">
          <div className="font-semibold text-ink mb-3 flex items-center gap-2"><Wand2 size={16} className="text-gold" /> {editing === "new" ? "Define a skill" : "Edit skill"}</div>
          <div className="space-y-3">
            <div>
              <label className="label">Name</label>
              <input className="input" value={form.name} onChange={(e) => setForm({ ...form, name: e.target.value })} placeholder="e.g. Cash drag finder" />
            </div>
            <div>
              <label className="label">What should it do? <span className="text-ink-muted font-normal">(plain English)</span></label>
              <textarea className="input" rows={3} value={form.instruction} onChange={(e) => setForm({ ...form, instruction: e.target.value })}
                placeholder="e.g. Find clients holding more than 10% in cash and draft a one-line note suggesting we put it to work toward their goals." />
              <div className="flex justify-end text-[11px] text-ink-muted mt-0.5">{form.instruction.length} chars</div>
            </div>
            <div className="grid sm:grid-cols-3 gap-3">
              <div>
                <label className="label">Scope</label>
                <select className="input" value={form.scope} onChange={(e) => setForm({ ...form, scope: e.target.value })}>
                  <option value="book">Whole book</option>
                  <option value="household">Per household</option>
                </select>
                <div className="text-[11px] text-ink-muted mt-1">{SCOPE_NOTES[form.scope]}</div>
              </div>
              <div>
                <label className="label">Produces</label>
                <select className="input" value={form.output_kind} onChange={(e) => setForm({ ...form, output_kind: e.target.value })}>
                  <option value="insight">Insight</option>
                  <option value="task">Task</option>
                  <option value="note">Client note</option>
                </select>
              </div>
              <div>
                <label className="label">Autonomy</label>
                <select className="input" value={form.default_tier} onChange={(e) => setForm({ ...form, default_tier: e.target.value })}>
                  <option value="tier_1">Tier 1 · Assistive</option>
                  <option value="tier_2">Tier 2 · Bounded</option>
                </select>
              </div>
            </div>
            <div>
              <label className="label">Who can use it</label>
              <div className="flex flex-wrap gap-2">
                {([["private", "Just me", Lock], ["shared", "Shared colleagues", Users], ["public", "Whole firm", Globe]] as any[]).map(([v, l, Icon]) => (
                  <button key={v} onClick={() => setForm({ ...form, visibility: v })}
                    className={`chip ${form.visibility === v ? "bg-navy-800 text-white" : "bg-navy-50 text-ink-muted"}`}>
                    <Icon size={12} /> {l}
                  </button>
                ))}
              </div>
              {form.visibility === "shared" && (
                <div className="mt-2 rounded-lg border border-navy-100 p-2 max-h-36 overflow-y-auto">
                  {(colleagues || []).map((c) => (
                    <label key={c.id} className="flex items-center gap-2 text-sm py-0.5 cursor-pointer">
                      <input type="checkbox" checked={form.shared_with.includes(c.id)} onChange={() => toggleShare(c.id)} />
                      <span className="text-ink-soft">{c.name}</span>
                      <span className="text-xs text-ink-muted">{c.title || c.role}</span>
                    </label>
                  ))}
                  {!colleagues?.length && <div className="text-xs text-ink-muted">No colleagues to share with.</div>}
                </div>
              )}
            </div>
            <div className="text-[11px] text-ink-muted flex items-center gap-1">
              <ShieldCheck size={12} /> Skills are assistive — they propose, never act without your approval, and every proposal is checked against your regulatory framework.
            </div>
            <div className="flex justify-end gap-2">
              <button className="btn-ghost" onClick={() => { setEditing(null); setForm(BLANK); }}>Cancel</button>
              <button className="btn-primary" disabled={busy === "save" || !form.name.trim() || !form.instruction.trim()} onClick={save}>
                {busy === "save" ? "Saving…" : editing === "new" ? "Save skill" : "Save changes"}
              </button>
            </div>
          </div>
        </Card>
      )}

      {!filtered.length ? (
        <Card className="p-8">
          <Empty>{filter === "mine" ? <>No skills in your workspace yet. Click <b>New skill</b> — or browse the <b>Firm library</b>.</> : "Nothing here yet."}</Empty>
        </Card>
      ) : (
        <div className="space-y-4">
          {filtered.map((s) => {
            const r = res[s.id];
            const isStreaming = r?.streaming;
            const isRunning = busy === "test:" + s.id || busy === "run:" + s.id;
            return (
              <Card key={s.id}>
                <div className="flex items-start gap-3">
                  <span className="h-9 w-9 rounded-lg bg-navy-800 text-white flex items-center justify-center shrink-0">
                    <Wand2 size={17} />
                  </span>
                  <div className="flex-1 min-w-0">
                    <div className="flex items-center gap-2 flex-wrap">
                      <span className="font-semibold text-ink">{s.name}</span>
                      {!s.owner_id
                        ? <span className="chip bg-gold-soft/40 text-gold-dark">Firm library</span>
                        : s.mine
                        ? <span className="chip bg-positive/10 text-positive">Yours</span>
                        : <span className="chip bg-navy-50 text-ink-muted">by {s.owner_name}</span>}
                      <span className="chip bg-navy-50 text-ink-muted">
                        {s.visibility === "public" ? <><Globe size={11} /> Public</>
                          : s.visibility === "shared" ? <><Users size={11} /> Shared</>
                          : <><Lock size={11} /> Private</>}
                      </span>
                      {!s.enabled && <span className="chip bg-critical/10 text-critical">Disabled</span>}
                    </div>
                    <p className="text-sm text-ink-soft mt-1 italic line-clamp-2" title={s.instruction}>"{s.instruction}"</p>
                  </div>
                  <div className="flex items-center gap-1.5 shrink-0 flex-wrap justify-end">
                    <button className="btn-outline text-xs" disabled={!!busy} onClick={() => runStream(s.id, true)}
                      title="Dry run — scans your book but nothing is saved">
                      {busy === "test:" + s.id ? <Loader2 size={13} className="animate-spin" /> : <FlaskConical size={13} />} Test
                    </button>
                    <button className="btn-gold text-xs" disabled={!!busy || !s.enabled} onClick={() => runStream(s.id, false)}
                      title="Run against your whole book and surface proposals for approval">
                      {busy === "run:" + s.id ? <Loader2 size={13} className="animate-spin" /> : <Play size={13} />} Run
                    </button>
                    {s.can_edit ? (
                      <>
                        <button className="btn-ghost text-xs" onClick={() => openEdit(s)}><Pencil size={13} /> Edit</button>
                        <button className="btn-ghost text-xs" onClick={() => toggle(s)}>{s.enabled ? "Disable" : "Enable"}</button>
                        <InlineConfirmButton label={<><Trash2 size={13} /></>} confirmLabel="Delete?" onConfirm={() => del(s.id)} />
                      </>
                    ) : (
                      <button className="btn-ghost text-xs" disabled={busy === "clone:" + s.id} onClick={() => clone(s.id)}
                        title="Copy this skill to your workspace to customise it">
                        <Copy size={13} /> Clone to my workspace
                      </button>
                    )}
                  </div>
                </div>

                {/* Live streaming log */}
                {r && (r.log.length > 0 || isStreaming) && (
                  <div className="mt-4 border-t border-navy-100 pt-3">
                    <div className="flex items-center gap-2 text-xs text-ink-muted mb-2">
                      <Zap size={12} className="text-gold" />
                      {isStreaming ? <span className="animate-pulse text-gold-dark font-medium">Claude is scanning your book…</span>
                        : r.mode === "test"
                        ? <span><b>Dry run complete</b> · scanned {r.scanned} · {r.surfaced} would be surfaced <span className="text-ink-muted">(nothing saved)</span></span>
                        : <span>Scanned {r.scanned} · <b>{r.surfaced} surfaced</b> — proposals are in your feed, awaiting your approval.</span>}
                    </div>
                    <SkillLog log={r.log} streaming={isStreaming} scanned={r.scanned} total={r.total} />

                    {/* Results after stream finishes */}
                    {!isStreaming && r.mode === "test" && r.proposals && r.proposals.length > 0 && (
                      <div className="mt-3 space-y-1.5">
                        {r.proposals.map((p: any, i: number) => (
                          <div key={i} className="text-sm flex gap-2"><span className="text-gold">•</span><span className="text-ink"><b>{p.household}</b> — {p.title}</span></div>
                        ))}
                      </div>
                    )}
                    {!isStreaming && r.mode === "run" && r.recommendations && r.recommendations.length > 0 && (
                      <div className="mt-3 space-y-3">
                        {r.recommendations.map((rec: any) => <RecommendationCard key={rec.id} rec={rec} />)}
                      </div>
                    )}
                  </div>
                )}
              </Card>
            );
          })}
        </div>
      )}
    </div>
  );
}
