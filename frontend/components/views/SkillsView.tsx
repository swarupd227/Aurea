"use client";

import { useState } from "react";
import {
  Wand2, Play, FlaskConical, Plus, Trash2, Loader2,
  Pencil, Copy, Users, Globe, Lock, CheckCircle2, XCircle, Zap, AlertTriangle,
} from "lucide-react";
import { Card, SkeletonList, Empty, InlineConfirmButton } from "@/components/ui";
import RecommendationCard from "@/components/RecommendationCard";
import { useApi } from "@/lib/hooks";
import { api } from "@/lib/api";
import { streamSkill } from "@/lib/stream";
import { withRef } from "@/lib/refs";
import { useViewNav } from "./ViewNav";
import { ViewHeader, chip } from "./ViewHeader";

const BLANK = {
  name: "", description: "", instruction: "", scope: "book",
  output_kind: "insight", default_tier: "tier_1",
  visibility: "private", shared_with: [] as string[],
};
type Form = typeof BLANK;
const FILTERS = [["mine", "Mine"], ["shared", "Shared with me"], ["public", "Firm library"], ["all", "All"]];
const VISIBILITY: [string, string, any][] = [["private", "Just me", Lock], ["shared", "Colleagues", Users], ["public", "Whole firm", Globe]];
const tierNo = (t: string) => t.replace("tier_", "");

const ref = (sentence: string, id: string) => withRef(sentence, "skill", id);

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

/** "Ann Lee and Bo Chan", from colleague ids. */
function names(ids: string[], colleagues: any[] | null | undefined): string {
  const list = ids.map((id) => colleagues?.find((c) => c.id === id)?.name).filter(Boolean) as string[];
  if (list.length <= 1) return list.join("");
  return `${list.slice(0, -1).join(", ")} and ${list[list.length - 1]}`;
}

/** Every field the create tool needs, in words. */
function createSentence(f: Form, colleagues: any[] | null | undefined): string {
  const shared = f.visibility === "shared" && f.shared_with.length ? `, shared with ${names(f.shared_with, colleagues)}` : "";
  const desc = f.description.trim() ? `, described as "${f.description.trim()}"` : "";
  return `Create a ${f.visibility} skill named "${f.name.trim()}", scope ${f.scope}, output ${f.output_kind}, `
    + `tier ${tierNo(f.default_tier)}${desc}${shared}, with the instruction: ${f.instruction.trim()}`;
}

/** Only what changed, in words; null when nothing did. The instruction goes last: it may say anything. */
function changeSentence(s: any, f: Form, colleagues: any[] | null | undefined): string | null {
  const parts: string[] = [];
  if (f.name.trim() !== s.name) parts.push(`rename it to "${f.name.trim()}"`);
  if (f.description.trim() !== (s.description || "")) {
    parts.push(f.description.trim() ? `set the description to "${f.description.trim()}"` : "clear the description");
  }
  if (f.scope !== s.scope) parts.push(`scope ${f.scope}`);
  if (f.output_kind !== s.output_kind) parts.push(`output ${f.output_kind}`);
  if (f.default_tier !== s.default_tier) parts.push(`tier ${tierNo(f.default_tier)}`);
  if (f.visibility !== s.visibility) parts.push(`make it ${f.visibility}`);
  const before = [...(s.shared_with || [])].sort().join(",");
  const after = f.visibility === "shared" ? [...f.shared_with].sort().join(",") : before;
  if (f.visibility === "shared" && after !== before) {
    parts.push(f.shared_with.length ? `share it with ${names(f.shared_with, colleagues)}` : "share it with no one");
  }
  if (f.instruction.trim() !== s.instruction) parts.push(`set the instruction to: ${f.instruction.trim()}`);
  return parts.length ? `Change the skill "${s.name}": ${parts.join("; ")}` : null;
}

function SkillLog({ log, streaming, scanned, total }: { log: LogLine[]; streaming: boolean; scanned: number; total: number }) {
  return (
    <div>
      {(streaming || scanned > 0) && total > 0 && (
        <div className="mb-2">
          <div className="mb-1 flex items-center justify-between font-mono text-[11px] tabular-nums text-ink-muted">
            <span>{scanned} / {total}</span>
            <span>{Math.round((scanned / total) * 100)}%</span>
          </div>
          <div className="h-1.5 overflow-hidden rounded-full bg-border-soft">
            <div
              className={`h-full rounded-full transition-all duration-300 ${streaming ? "bg-accent" : "bg-ok"}`}
              style={{ width: `${Math.round((scanned / total) * 100)}%` }}
            />
          </div>
        </div>
      )}
      <div className="max-h-52 space-y-1 overflow-y-auto rounded-lg border border-border-soft bg-paper p-3 font-mono text-[12px]">
        {log.map((line, i) => {
          if (line.kind === "start")
            return <div key={i} className="text-ink">{line.test ? "Test · " : ""}{line.skill} · {line.total}</div>;
          if (line.kind === "scan")
            return <div key={i} className="text-ink-muted">[{line.index}/{line.total}] <span className="text-ink-soft">{line.household}</span></div>;
          if (line.kind === "result")
            return (
              <div key={i} className={`break-words ${line.applies ? "text-ok" : "text-ink-muted"}`}>
                {line.applies
                  ? <span className="inline-flex items-center gap-1"><CheckCircle2 size={11} /> {line.household} · {line.title}</span>
                  : <span className="inline-flex items-center gap-1"><XCircle size={11} /> {line.household} · no match</span>}
              </div>
            );
          if (line.kind === "done")
            return <div key={i} className="mt-1 text-ok">Done · {line.scanned} scanned · {line.surfaced} surfaced</div>;
          if (line.kind === "error")
            return <div key={i} className="flex items-center gap-1 text-crit"><AlertTriangle size={11} /> {line.message}</div>;
          return null;
        })}
        {streaming && <div className="animate-pulse text-accent">…</div>}
      </div>
    </div>
  );
}

/** Skills: agents described in plain words, private, shared or published. In the work pane creating,
 * changing, copying and deleting a skill are the conversation's tools behind their Confirm card; on a
 * page the buttons act directly. */
export function SkillsView() {
  const nav = useViewNav();
  const { data: skills, loading, refetch } = useApi<any[]>("/api/skills");
  const { data: colleagues } = useApi<any[]>("/api/skills/colleagues");
  const [filter, setFilter] = useState("mine");
  const [editing, setEditing] = useState<null | string>(null);
  const [form, setForm] = useState<Form>(BLANK);
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
  function close() { setEditing(null); setForm(BLANK); }

  async function save() {
    if (nav.say) {
      if (editing === "new") {
        nav.say(createSentence(form, colleagues));
      } else {
        const s = skills?.find((x) => x.id === editing);
        const sentence = s ? changeSentence(s, form, colleagues) : null;
        if (s && sentence) nav.say(ref(sentence, s.id));
      }
      return close();
    }
    setBusy("save");
    try {
      if (editing === "new") await api("/api/skills", { body: form });
      else await api(`/api/skills/${editing}`, { method: "PATCH", body: form });
      close(); refetch();
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

  async function toggle(s: any) {
    if (nav.say) return nav.say(ref(`Change the skill "${s.name}": ${s.enabled ? "disable" : "enable"} it`, s.id));
    await api(`/api/skills/${s.id}`, { method: "PATCH", body: { enabled: !s.enabled } }); refetch();
  }
  async function del(s: any) {
    if (nav.say) return nav.say(ref(`Delete the skill "${s.name}"`, s.id));
    await api(`/api/skills/${s.id}`, { method: "DELETE" }); refetch();
  }
  async function clone(s: any) {
    if (nav.say) return nav.say(ref(`Copy the skill "${s.name}" into my skills`, s.id));
    setBusy("clone:" + s.id);
    try { await api(`/api/skills/${s.id}/clone`, { body: {} }); setFilter("mine"); refetch(); } finally { setBusy(null); }
  }
  function toggleShare(uid: string) {
    const has = form.shared_with.includes(uid);
    setForm({ ...form, shared_with: has ? form.shared_with.filter((x) => x !== uid) : [...form.shared_with, uid] });
  }

  if (loading) return <div className="view-container"><SkeletonList count={3} /></div>;

  const all = skills || [];
  // The firm library is every public skill, your own published ones included.
  const filtered = all.filter((s) =>
    filter === "all" ? true
    : filter === "mine" ? s.mine
    : filter === "shared" ? (!s.mine && s.visibility === "shared")
    : s.visibility === "public"
  );
  const count = (k: string) => all.filter((s) =>
    k === "all" ? true : k === "mine" ? s.mine : k === "shared" ? (!s.mine && s.visibility === "shared") : s.visibility === "public").length;
  const editingSkill = editing && editing !== "new" ? all.find((s) => s.id === editing) : null;
  const unchanged = !!editingSkill && !changeSentence(editingSkill, form, colleagues);

  return (
    <div className="view-container" data-testid="skills-view">
      <ViewHeader
        title="Skills"
        meta={<span className={`${chip} tabular-nums`}>{all.length} skills</span>}
        actions={<button className="btn-primary" onClick={openNew} data-testid="skills-new"><Plus size={16} /> New skill</button>}
      />

      <div className="mb-4 flex max-w-full flex-wrap overflow-hidden rounded border border-border text-sm w-fit">
        {FILTERS.map(([k, label]) => (
          <button key={k} data-testid={`skills-filter-${k}`} aria-pressed={filter === k} onClick={() => setFilter(k)}
            className={`px-3 py-1.5 ${filter === k ? "bg-border-soft text-ink" : "text-ink-muted hover:text-ink"}`}>
            {label} <span className="font-mono text-xs tabular-nums text-ink-muted">{count(k)}</span>
          </button>
        ))}
      </div>

      {editing && (
        <Card className="mb-5" data-testid="skills-form">
          <div className="mb-3 flex items-center gap-2 font-semibold text-ink"><Wand2 size={16} /> {editing === "new" ? "New skill" : "Edit skill"}</div>
          <div className="space-y-3">
            <div>
              <label className="label">Name</label>
              <input className="input" value={form.name} onChange={(e) => setForm({ ...form, name: e.target.value })}
                placeholder="Cash drag finder" data-testid="skills-name" />
            </div>
            <div>
              <label className="label">Description</label>
              <input className="input" value={form.description} onChange={(e) => setForm({ ...form, description: e.target.value })} />
            </div>
            <div>
              <label className="label">Instruction</label>
              <textarea className="input" rows={3} value={form.instruction} onChange={(e) => setForm({ ...form, instruction: e.target.value })}
                placeholder="Find clients holding more than 10% in cash" data-testid="skills-instruction" />
              <div className="mt-0.5 flex justify-end font-mono text-[11px] tabular-nums text-ink-muted">{form.instruction.length}</div>
            </div>
            <div className="grid gap-3 cq-sm:grid-cols-3">
              <div>
                <label className="label">Scope</label>
                <select className="input" value={form.scope} onChange={(e) => setForm({ ...form, scope: e.target.value })}>
                  <option value="book">Whole book</option>
                  <option value="household">Per household</option>
                </select>
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
                  <option value="tier_1">Tier 1</option>
                  <option value="tier_2">Tier 2</option>
                </select>
              </div>
            </div>
            <div>
              <label className="label">Who can use it</label>
              <div className="flex flex-wrap gap-2">
                {VISIBILITY.map(([v, l, Icon]) => (
                  <button key={v} aria-pressed={form.visibility === v} onClick={() => setForm({ ...form, visibility: v })}
                    className={`chip inline-flex items-center gap-1 ${form.visibility === v ? "bg-border text-ink" : "bg-border-soft text-ink-muted"}`}>
                    <Icon size={12} /> {l}
                  </button>
                ))}
              </div>
              {form.visibility === "shared" && (
                <div className="mt-2 max-h-36 overflow-y-auto rounded-lg border border-border-soft p-2">
                  {(colleagues || []).map((c) => (
                    <label key={c.id} className="flex cursor-pointer flex-wrap items-center gap-2 py-0.5 text-sm">
                      <input type="checkbox" checked={form.shared_with.includes(c.id)} onChange={() => toggleShare(c.id)} />
                      <span className="text-ink-soft">{c.name}</span>
                      <span className="text-xs text-ink-muted">{c.title || c.role}</span>
                    </label>
                  ))}
                  {!colleagues?.length && <div className="text-xs text-ink-muted">No colleagues</div>}
                </div>
              )}
            </div>
            <div className="flex flex-wrap justify-end gap-2">
              <button className="btn-ghost" onClick={close}>Cancel</button>
              <button className="btn-primary" data-testid="skills-save"
                disabled={busy === "save" || !form.name.trim() || !form.instruction.trim() || unchanged} onClick={save}>
                {busy === "save" ? "Saving…" : nav.say ? "Ask Astra" : editing === "new" ? "Save skill" : "Save changes"}
              </button>
            </div>
          </div>
        </Card>
      )}

      {!filtered.length ? (
        <Card className="p-8"><Empty>No skills</Empty></Card>
      ) : (
        <div className="space-y-4">
          {filtered.map((s) => {
            const r = res[s.id];
            const isStreaming = !!r?.streaming;
            return (
              <Card key={s.id} data-testid="skill-row">
                <div className="flex flex-wrap items-start gap-3">
                  <span className="flex h-9 w-9 shrink-0 items-center justify-center rounded-lg border border-border text-ink-soft">
                    <Wand2 size={17} />
                  </span>
                  <div className="min-w-[12rem] flex-1">
                    <div className="flex flex-wrap items-center gap-2">
                      <span className="font-semibold text-ink">{s.name}</span>
                      {!s.owner_id
                        ? <span className={chip}>Firm library</span>
                        : s.mine
                        ? <span className="chip bg-ok-bg text-ok">Yours</span>
                        : <span className={chip}>{s.owner_name}</span>}
                      <span className={`${chip} inline-flex items-center gap-1`}>
                        {s.visibility === "public" ? <><Globe size={11} /> Public</>
                          : s.visibility === "shared" ? <><Users size={11} /> Shared</>
                          : <><Lock size={11} /> Private</>}
                      </span>
                      <span className={chip}>{s.scope === "household" ? "Per household" : "Whole book"}</span>
                      <span className={chip}>{s.output_kind}</span>
                      <span className={`${chip} font-mono`}>T{tierNo(String(s.default_tier))}</span>
                      {!s.enabled && <span className="chip bg-crit-bg text-crit">Disabled</span>}
                    </div>
                    <p className="mt-1 line-clamp-2 break-words text-sm text-ink-soft" title={s.instruction}>{s.instruction}</p>
                  </div>
                  <div className="flex flex-wrap items-center justify-end gap-1.5">
                    <button className="btn-outline text-xs" disabled={!!busy} onClick={() => runStream(s.id, true)} data-testid="skill-test">
                      {busy === "test:" + s.id ? <Loader2 size={13} className="animate-spin" /> : <FlaskConical size={13} />} Test
                    </button>
                    {/* A run raises proposals, so beside the conversation it goes through run_skill and its Confirm card. */}
                    <button className="btn-primary text-xs" disabled={!!busy || !s.enabled}
                      onClick={() => (nav.say ? nav.say(ref(`Run the skill "${s.name}" over every client`, s.id)) : runStream(s.id, false))}
                      data-testid="skill-run">
                      {busy === "run:" + s.id ? <Loader2 size={13} className="animate-spin" /> : <Play size={13} />} {nav.say ? "Ask Astra to run" : "Run"}
                    </button>
                    {s.can_edit ? (
                      <>
                        <button className="btn-ghost text-xs" onClick={() => openEdit(s)} data-testid="skill-edit"><Pencil size={13} /> Edit</button>
                        <button className="btn-ghost text-xs" onClick={() => toggle(s)} data-testid="skill-toggle">
                          {nav.say ? `Ask Astra to ${s.enabled ? "disable" : "enable"}` : s.enabled ? "Disable" : "Enable"}
                        </button>
                        {nav.say ? (
                          <button className="btn-ghost text-xs" onClick={() => del(s)} data-testid="skill-delete" aria-label="Ask Astra to delete">
                            <Trash2 size={13} /> Ask Astra
                          </button>
                        ) : (
                          <InlineConfirmButton label={<Trash2 size={13} />} confirmLabel="Delete?" onConfirm={() => del(s)} />
                        )}
                      </>
                    ) : (
                      <button className="btn-ghost text-xs" disabled={busy === "clone:" + s.id} onClick={() => clone(s)} data-testid="skill-clone">
                        <Copy size={13} /> {nav.say ? "Ask Astra to copy" : "Copy to my skills"}
                      </button>
                    )}
                  </div>
                </div>

                {r && (r.log.length > 0 || isStreaming) && (
                  <div className="mt-4 border-t border-border-soft pt-3">
                    <div className="mb-2 flex flex-wrap items-center gap-1.5 text-xs text-ink-muted">
                      <Zap size={12} className={isStreaming ? "text-accent" : "text-ink-muted"} />
                      {isStreaming
                        ? <span className="animate-pulse text-accent">Scanning</span>
                        : <>
                            <span className={chip}>{r.mode === "test" ? "Test · nothing saved" : "Run"}</span>
                            <span className={`${chip} tabular-nums`}>Scanned {r.scanned}</span>
                            <span className={`${chip} tabular-nums`}>{r.mode === "test" ? "Would surface" : "Surfaced"} {r.surfaced}</span>
                          </>}
                    </div>
                    <SkillLog log={r.log} streaming={isStreaming} scanned={r.scanned} total={r.total} />

                    {!isStreaming && r.mode === "test" && !!r.proposals?.length && (
                      <table className="mt-3 w-full text-sm">
                        <tbody className="divide-y divide-border-soft">
                          {r.proposals.map((p: any, i: number) => (
                            <tr key={i}>
                              <td className="py-1.5 pr-3 font-medium text-ink">{p.household}</td>
                              <td className="py-1.5 text-ink-soft">{p.title}</td>
                            </tr>
                          ))}
                        </tbody>
                      </table>
                    )}
                    {!isStreaming && r.mode === "run" && !!r.recommendations?.length && (
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
