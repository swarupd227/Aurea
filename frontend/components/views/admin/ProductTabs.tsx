"use client";

import { useState } from "react";
import { Save, Plus, Pencil, Tag, Trash2, X } from "lucide-react";
import { Card, Spinner, TierBadge, InlineConfirmButton, Empty } from "@/components/ui";
import { useApi } from "@/lib/hooks";
import { api } from "@/lib/api";
import { money, titleCase } from "@/lib/format";
import { withRef } from "@/lib/refs";
import { chip } from "../ViewHeader";
import { ActiveChip, FormError, ReasonInput, TableCard, TD, TH, because, q, tierWord, useAsk } from "./shared";

// ── Segments ──────────────────────────────────────────────────────────────────

const usd = (n: number | null | undefined) => (n == null ? null : money(n, "USD"));

function segmentChange(s: any, e: any): string | null {
  const parts: string[] = [];
  if ((e.label || "").trim() !== (s.label || "")) parts.push(`label ${q(e.label)}`);
  if ((e.fee_tier_bps ?? null) !== (s.fee_tier_bps ?? null)) parts.push(e.fee_tier_bps != null ? `fee ${e.fee_tier_bps} bps` : "clear the fee");
  if ((e.min_aum_usd ?? null) !== (s.min_aum_usd ?? null)) parts.push(e.min_aum_usd != null ? `minimum AUM ${usd(e.min_aum_usd)}` : "clear the minimum AUM");
  if ((e.description || "") !== (s.description || "")) parts.push((e.description || "").trim() ? `description ${q(e.description)}` : "clear the description");
  if (!!e.is_active !== !!s.is_active) parts.push(e.is_active ? "reactivate it" : "deactivate it");
  return parts.length ? `Change the segment ${q(s.label)} (${s.slug}): ${parts.join("; ")}` : null;
}

export function SegmentsTab() {
  const ask = useAsk();
  const { data: segments, loading, refetch } = useApi<any[]>("/api/admin/segments");
  const [editing, setEditing] = useState<any>(null);
  const [showForm, setShowForm] = useState(false);
  const [form, setForm] = useState({ slug: "", label: "", fee_tier_bps: "", min_aum_usd: "", description: "" });
  const [saving, setSaving] = useState(false);
  const [err, setErr] = useState("");

  async function create() {
    setErr("");
    if (!form.slug || !form.label) { setErr("Slug and label are required."); return; }
    const slug = form.slug.toLowerCase().replace(/\s+/g, "_");
    if (ask) {
      const bits = [
        form.fee_tier_bps ? `fee ${parseInt(form.fee_tier_bps)} bps` : null,
        form.min_aum_usd ? `minimum AUM ${usd(parseFloat(form.min_aum_usd))}` : null,
        form.description.trim() ? `description ${q(form.description)}` : null,
      ].filter(Boolean);
      ask(`Add the segment ${q(form.label)} (slug ${slug})${bits.length ? `: ${bits.join("; ")}` : ""}`);
      setShowForm(false);
      setForm({ slug: "", label: "", fee_tier_bps: "", min_aum_usd: "", description: "" });
      return;
    }
    setSaving(true);
    try {
      await api("/api/admin/segments", {
        method: "POST",
        body: {
          slug,
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

  const original = editing ? segments?.find((s) => s.id === editing.id) : null;
  const editSentence = ask && original ? segmentChange(original, editing) : null;

  async function saveEdit() {
    if (!editing) return;
    if (ask) {
      if (editSentence) ask(editSentence);
      setEditing(null);
      return;
    }
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
    <div className="max-w-4xl space-y-4" data-testid="admin-segments">
      {editing && (
        <Card>
          <div className="mb-3 flex items-center gap-2 font-semibold text-ink"><Pencil size={14} /> {editing.slug}</div>
          <div className="mb-3 grid gap-3 cq-sm:grid-cols-2">
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
            <label className="flex cursor-pointer items-center gap-2 text-sm text-ink">
              <input type="checkbox" className="h-4 w-4 rounded" checked={editing.is_active}
                onChange={(e) => setEditing({ ...editing, is_active: e.target.checked })} />
              Active
            </label>
          </div>
          <div className="flex flex-wrap gap-2">
            <button className="btn-primary" onClick={saveEdit} disabled={saving || (!!ask && !editSentence)} data-testid="admin-segment-save">
              {saving ? "Saving…" : <><Save size={14} /> {ask ? "Ask Astra" : "Save"}</>}
            </button>
            <button className="btn-outline" onClick={() => setEditing(null)}>Cancel</button>
          </div>
        </Card>
      )}
      {showForm && (
        <Card>
          <div className="mb-3 flex items-center gap-2 font-semibold text-ink"><Plus size={14} /> New segment</div>
          {err && <FormError>{err}</FormError>}
          <div className="mb-3 grid gap-3 cq-sm:grid-cols-2">
            <div><label className="label">Slug</label>
              <input className="input" placeholder="family_office" value={form.slug}
                onChange={(e) => setForm({ ...form, slug: e.target.value })} /></div>
            <div><label className="label">Label</label>
              <input className="input" placeholder="Family Office" value={form.label}
                onChange={(e) => setForm({ ...form, label: e.target.value })} /></div>
            <div><label className="label">Fee tier (bps)</label>
              <input className="input" type="number" min="0" placeholder="50" value={form.fee_tier_bps}
                onChange={(e) => setForm({ ...form, fee_tier_bps: e.target.value })} /></div>
            <div><label className="label">Min AUM (USD)</label>
              <input className="input" type="number" min="0" placeholder="1000000" value={form.min_aum_usd}
                onChange={(e) => setForm({ ...form, min_aum_usd: e.target.value })} /></div>
            <div className="cq-sm:col-span-2"><label className="label">Description</label>
              <input className="input" value={form.description}
                onChange={(e) => setForm({ ...form, description: e.target.value })} /></div>
          </div>
          <div className="flex flex-wrap gap-2">
            <button className="btn-primary" onClick={create} disabled={saving} data-testid="admin-segment-create">
              {saving ? "Creating…" : ask ? "Ask Astra" : "Create segment"}
            </button>
            <button className="btn-outline" onClick={() => { setShowForm(false); setErr(""); }}>Cancel</button>
          </div>
        </Card>
      )}
      {!editing && !showForm && (
        <div className="flex justify-end">
          <button className="btn-primary" onClick={() => setShowForm(true)}><Plus size={15} /> Add segment</button>
        </div>
      )}
      {loading && !segments ? <Card><Spinner /></Card> : !segments?.length ? (
        <Card><Empty>No segments</Empty></Card>
      ) : (
        <TableCard testid="admin-segments-table">
          <thead>
            <tr className="border-b border-border-soft">
              <th className={TH}>Slug</th>
              <th className={TH}>Label</th>
              <th className={`${TH} text-right`}>Fee</th>
              <th className={`${TH} text-right`}>Min AUM</th>
              <th className={TH}>Status</th>
              <th className={`${TH} text-right`}>Actions</th>
            </tr>
          </thead>
          <tbody className="divide-y divide-border-soft">
            {segments.map((s: any) => (
              <tr key={s.id} className={!s.is_active ? "opacity-50" : ""}>
                <td className={`${TD} font-mono text-xs text-ink-muted`}>{s.slug}</td>
                <td className={`${TD} font-medium text-ink`}>{s.label}</td>
                <td className={`${TD} text-right font-mono text-xs tabular-nums text-ink-soft`}>{s.fee_tier_bps != null ? `${s.fee_tier_bps} bps` : "—"}</td>
                <td className={`${TD} text-right font-mono text-xs tabular-nums text-ink-soft`}>{usd(s.min_aum_usd) ?? "—"}</td>
                <td className={TD}><ActiveChip active={!!s.is_active} /></td>
                <td className={`${TD} text-right`}>
                  <div className="flex items-center justify-end gap-1.5">
                    <button className="btn-outline px-2 py-1 text-xs" onClick={() => setEditing({ ...s })}>Edit</button>
                    {s.is_active && (ask ? (
                      <button className="btn-ghost text-xs text-crit" onClick={() => ask(`Deactivate the segment ${q(s.label)} (${s.slug})`)}>Deactivate</button>
                    ) : (
                      <InlineConfirmButton
                        label="Deactivate"
                        confirmLabel="Deactivate segment?"
                        onConfirm={async () => { await api(`/api/admin/segments/${s.id}`, { method: "DELETE" }); refetch(); }}
                      />
                    ))}
                  </div>
                </td>
              </tr>
            ))}
          </tbody>
        </TableCard>
      )}
    </div>
  );
}

// ── Mandate types and mandates ────────────────────────────────────────────────

const HORIZON_LABELS: Record<string, string> = { short: "Short-term", medium: "Medium-term", long: "Long-term" };
const TIERS = [{ v: "tier_1", l: "Tier 1 · Assistive" }, { v: "tier_2", l: "Tier 2 · Supervised" }, { v: "tier_3", l: "Tier 3 · Autonomous" }];

function MandateTypesConfig() {
  const ask = useAsk();
  const { data: types, refetch } = useApi<any[]>("/api/admin/mandate-types");
  const [editingId, setEditingId] = useState<string | null>(null);
  const [editForm, setEditForm] = useState<any>({});
  const [showNew, setShowNew] = useState(false);
  const [newForm, setNewForm] = useState({ slug: "", label: "", default_autonomy_tier: "tier_2", description: "" });
  const [saving, setSaving] = useState(false);

  async function saveEdit(t: any) {
    if (ask) {
      const parts: string[] = [];
      if (editForm.label !== undefined && editForm.label.trim() !== t.label) parts.push(`label ${q(editForm.label)}`);
      if (editForm.default_autonomy_tier && editForm.default_autonomy_tier !== t.default_autonomy_tier) parts.push(`default ${tierWord(editForm.default_autonomy_tier)}`);
      if (parts.length) ask(`Change the mandate type ${q(t.label)} (${t.slug}): ${parts.join("; ")}`);
      setEditingId(null);
      return;
    }
    setSaving(true);
    try {
      await api(`/api/admin/mandate-types/${t.id}`, { method: "PATCH", body: editForm });
      setEditingId(null);
      refetch();
    } finally { setSaving(false); }
  }

  async function createType() {
    const slug = newForm.slug.toLowerCase().replace(/\s+/g, "_");
    if (ask) {
      ask(`Add the mandate type ${q(newForm.label)} (slug ${slug}), default ${tierWord(newForm.default_autonomy_tier)}${newForm.description.trim() ? `, description ${q(newForm.description)}` : ""}`);
      setShowNew(false);
      setNewForm({ slug: "", label: "", default_autonomy_tier: "tier_2", description: "" });
      return;
    }
    setSaving(true);
    try {
      await api("/api/admin/mandate-types", { method: "POST", body: { ...newForm, slug } });
      setShowNew(false);
      setNewForm({ slug: "", label: "", default_autonomy_tier: "tier_2", description: "" });
      refetch();
    } finally { setSaving(false); }
  }

  return (
    <Card className="mb-4" data-testid="admin-mandate-types">
      <div className="mb-3 flex flex-wrap items-center justify-between gap-2">
        <div className="flex items-center gap-2 font-semibold text-ink"><Tag size={14} /> Mandate types</div>
        <button className="btn-outline px-2 py-1 text-xs" onClick={() => setShowNew((v) => !v)}><Plus size={12} /> Add type</button>
      </div>
      {showNew && (
        <div className="mb-3 grid gap-3 rounded-lg border border-border-soft p-3 cq-sm:grid-cols-2">
          <div><label className="label">Slug</label><input className="input" placeholder="bespoke" value={newForm.slug} onChange={(e) => setNewForm({ ...newForm, slug: e.target.value })} /></div>
          <div><label className="label">Label</label><input className="input" placeholder="Bespoke" value={newForm.label} onChange={(e) => setNewForm({ ...newForm, label: e.target.value })} /></div>
          <div><label className="label">Default tier</label>
            <select className="input" value={newForm.default_autonomy_tier} onChange={(e) => setNewForm({ ...newForm, default_autonomy_tier: e.target.value })}>
              {TIERS.map((t) => <option key={t.v} value={t.v}>{t.l}</option>)}
            </select></div>
          <div><label className="label">Description</label><input className="input" value={newForm.description} onChange={(e) => setNewForm({ ...newForm, description: e.target.value })} /></div>
          <div className="flex items-end gap-2">
            <button className="btn-primary px-3 py-1 text-xs" onClick={createType} disabled={saving || !newForm.slug.trim() || !newForm.label.trim()} data-testid="admin-mandate-type-create">
              {ask ? "Ask Astra" : "Create"}
            </button>
            <button className="btn-outline px-3 py-1 text-xs" onClick={() => setShowNew(false)}>Cancel</button>
          </div>
        </div>
      )}
      <div className="divide-y divide-border-soft">
        {(types || []).map((t: any) => (
          <div key={t.id} className="flex items-start gap-3 py-2.5">
            {editingId === t.id ? (
              <div className="grid flex-1 items-end gap-2 cq-sm:grid-cols-3">
                <div><label className="label">Label</label><input className="input py-1" value={editForm.label ?? t.label} onChange={(e) => setEditForm({ ...editForm, label: e.target.value })} /></div>
                <div><label className="label">Default tier</label>
                  <select className="input py-1" value={editForm.default_autonomy_tier ?? t.default_autonomy_tier} onChange={(e) => setEditForm({ ...editForm, default_autonomy_tier: e.target.value })}>
                    {TIERS.map((ti) => <option key={ti.v} value={ti.v}>{ti.l}</option>)}
                  </select></div>
                <div className="flex gap-1">
                  <button className="btn-primary px-2 py-1 text-xs" onClick={() => saveEdit(t)} disabled={saving} aria-label={ask ? "Ask Astra" : "Save"}>
                    <Save size={12} />{ask ? " Ask Astra" : ""}
                  </button>
                  <button className="btn-outline px-2 py-1 text-xs" onClick={() => setEditingId(null)} aria-label="Cancel"><X size={12} /></button>
                </div>
              </div>
            ) : (
              <>
                <div className="min-w-0 flex-1">
                  <div className="flex flex-wrap items-center gap-2">
                    <span className="text-sm font-medium text-ink">{t.label}</span>
                    <span className={`${chip} font-mono text-[10px]`}>{t.slug}</span>
                    <TierBadge tier={t.default_autonomy_tier} />
                  </div>
                  {t.description && <div className="mt-0.5 text-xs text-ink-muted">{t.description}</div>}
                </div>
                <button className="shrink-0 text-ink-muted hover:text-ink" onClick={() => { setEditingId(t.id); setEditForm({}); }} aria-label="Edit"><Pencil size={13} /></button>
              </>
            )}
          </div>
        ))}
      </div>
    </Card>
  );
}

function mandateChange(m: any, e: any): string | null {
  const parts: string[] = [];
  const s = m.suitability || {};
  const t = e.suitability || {};
  if (e.mandate_type !== m.mandate_type) parts.push(`type ${titleCase(e.mandate_type).toLowerCase()}`);
  if ((t.risk_tolerance || "balanced") !== (s.risk_tolerance || "balanced")) parts.push(`risk tolerance ${t.risk_tolerance}`);
  if ((t.investment_horizon || "medium") !== (s.investment_horizon || "medium")) parts.push(`horizon ${t.investment_horizon}`);
  if (t.max_equity != null && Math.round(t.max_equity * 100) !== Math.round((s.max_equity ?? 0.6) * 100)) parts.push(`max equity ${Math.round(t.max_equity * 100)}%`);
  if (!!e.is_active !== !!m.is_active) parts.push(e.is_active ? "reactivate it" : "deactivate it");
  return parts.length ? `Change the suitability of the mandate ${q(m.name)}${m.client_name ? ` for ${m.client_name}` : ""}: ${parts.join("; ")}` : null;
}

export function MandatesTab() {
  const ask = useAsk();
  const { data: mandates, loading, refetch } = useApi<any[]>("/api/admin/mandates");
  const [editing, setEditing] = useState<any>(null);
  const [saving, setSaving] = useState(false);

  const original = editing ? mandates?.find((m) => m.id === editing.id) : null;
  const editSentence = ask && original ? mandateChange(original, editing) : null;

  async function saveMandate() {
    if (!editing) return;
    if (ask) {
      if (editSentence) ask(withRef(editSentence + because(editing.reason || ""), "mandate", editing.id));
      setEditing(null);
      return;
    }
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
    <div className="max-w-5xl space-y-4" data-testid="admin-mandates">
      <MandateTypesConfig />
      {editing && (
        <Card>
          <div className="mb-3 flex items-center gap-2 font-semibold text-ink"><Pencil size={14} /> {editing.client_name || editing.name}</div>
          <div className="mb-3 grid gap-3 cq-sm:grid-cols-3">
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
            <label className="flex cursor-pointer items-center gap-2 text-sm text-ink">
              <input type="checkbox" className="h-4 w-4 rounded" checked={editing.is_active}
                onChange={(e) => setEditing({ ...editing, is_active: e.target.checked })} />
              Active mandate
            </label>
            {ask && (
              <div><label className="label">Reason</label>
                <ReasonInput className="w-full" value={editing.reason || ""} onChange={(v) => setEditing({ ...editing, reason: v })} />
              </div>
            )}
          </div>
          <div className="flex flex-wrap gap-2">
            <button className="btn-primary" onClick={saveMandate} disabled={saving || (!!ask && !editSentence)} data-testid="admin-mandate-save">
              {saving ? "Saving…" : <><Save size={14} /> {ask ? "Ask Astra" : "Save"}</>}
            </button>
            <button className="btn-outline" onClick={() => setEditing(null)}>Cancel</button>
          </div>
        </Card>
      )}
      {loading && !mandates ? <Card><Spinner /></Card> : !mandates?.length ? (
        <Card><Empty>No mandates</Empty></Card>
      ) : (
        <TableCard testid="admin-mandates-table">
          <thead>
            <tr className="border-b border-border-soft">
              <th className={TH}>Client</th>
              <th className={TH}>Mandate</th>
              <th className={TH}>Type</th>
              <th className={TH}>Risk</th>
              <th className={`${TH} text-right`}>Max equity</th>
              <th className={TH}>Horizon</th>
              <th className={TH}>Status</th>
              <th className={`${TH} text-right`}>Edit</th>
            </tr>
          </thead>
          <tbody className="divide-y divide-border-soft">
            {mandates.map((m) => (
              <tr key={m.id}>
                <td className={`${TD} text-xs text-ink-soft`}>{m.client_name || "—"}</td>
                <td className={`${TD} text-xs font-medium text-ink`}>{m.name}</td>
                <td className={`${TD} text-xs capitalize text-ink-soft`}>{m.mandate_type?.replace(/_/g, " ")}</td>
                <td className={TD}><span className={chip}>{titleCase(m.suitability?.risk_tolerance || "—")}</span></td>
                <td className={`${TD} text-right font-mono text-xs tabular-nums text-ink-soft`}>{m.suitability?.max_equity != null ? `${Math.round(m.suitability.max_equity * 100)}%` : "—"}</td>
                <td className={`${TD} text-xs text-ink-soft`}>{HORIZON_LABELS[m.suitability?.investment_horizon] || "—"}</td>
                <td className={TD}><ActiveChip active={!!m.is_active} /></td>
                <td className={`${TD} text-right`}>
                  <button className="btn-outline ml-auto px-2 py-1 text-xs" onClick={() => setEditing({ ...m, reason: "" })}>
                    <Pencil size={11} /> Edit
                  </button>
                </td>
              </tr>
            ))}
          </tbody>
        </TableCard>
      )}
    </div>
  );
}

// ── Model portfolios ──────────────────────────────────────────────────────────

const ASSET_CLASSES = ["equity", "fixed_income", "alternatives", "cash", "property", "infrastructure", "commodities"];

export function ModelPortfoliosTab() {
  const ask = useAsk();
  const { data, loading, refetch } = useApi<any[]>("/api/admin/model-portfolios");
  const [creating, setCreating] = useState(false);
  const [newForm, setNewForm] = useState({ name: "", description: "", drift_band: "5", benchmark_symbol: "" });
  const [editing, setEditing] = useState<any | null>(null);
  const [weights, setWeights] = useState<Record<string, string>>({});
  const [busy, setBusy] = useState(false);

  async function create() {
    if (ask) {
      ask(`Create the model portfolio ${q(newForm.name)}, drift band ${Number(newForm.drift_band)}%`
        + `${newForm.benchmark_symbol.trim() ? `, benchmark ${newForm.benchmark_symbol.trim()}` : ""}`
        + `${newForm.description.trim() ? `, description ${q(newForm.description)}` : ""}`);
      setNewForm({ name: "", description: "", drift_band: "5", benchmark_symbol: "" }); setCreating(false);
      return;
    }
    setBusy(true);
    try {
      await api("/api/admin/model-portfolios", { body: { name: newForm.name, description: newForm.description, drift_band: Number(newForm.drift_band) / 100, benchmark_symbol: newForm.benchmark_symbol || null } });
      setNewForm({ name: "", description: "", drift_band: "5", benchmark_symbol: "" }); setCreating(false); refetch();
    } finally { setBusy(false); }
  }

  async function saveTargets(mp: any) {
    const entries = Object.entries(weights).filter(([, v]) => v && Number(v) > 0);
    if (ask) {
      ask(`Set the targets of the model portfolio ${q(mp.name)} to ${entries.map(([ac, v]) => `${titleCase(ac).toLowerCase()} ${Number(v)}%`).join(", ")}`);
      setEditing(null); setWeights({});
      return;
    }
    const targets = entries.map(([asset_class, v]) => ({ asset_class, target_weight: Number(v) / 100 }));
    setBusy(true);
    try {
      await api(`/api/admin/model-portfolios/${mp.id}/targets`, { method: "PUT", body: targets });
      setEditing(null); setWeights({}); refetch();
    } catch (e: any) { alert(e.message); } finally { setBusy(false); }
  }

  async function deleteModel(mp: any) {
    if (ask) return ask(`Delete the model portfolio ${q(mp.name)}`);
    await api(`/api/admin/model-portfolios/${mp.id}`, { method: "DELETE", body: {} });
    refetch();
  }

  const total = Object.values(weights).reduce((s, v) => s + (Number(v) || 0), 0);
  const balanced = Math.abs(total - 100) < 0.5;

  return (
    <div className="max-w-4xl space-y-4" data-testid="admin-model-portfolios">
      <div className="flex flex-wrap items-center justify-between gap-2">
        <span className={`${chip} tabular-nums`}>{data?.length ?? 0} models</span>
        {!creating && (
          <button className="btn-outline text-sm" onClick={() => setCreating(true)}><Plus size={15} /> New model</button>
        )}
      </div>

      {creating && (
        <Card>
          <div className="mb-3 font-semibold text-ink">New model portfolio</div>
          <div className="space-y-3">
            <div><label className="label">Name</label><input className="input" value={newForm.name} onChange={(e) => setNewForm({ ...newForm, name: e.target.value })} placeholder="Conservative Growth" /></div>
            <div><label className="label">Description</label><input className="input" value={newForm.description} onChange={(e) => setNewForm({ ...newForm, description: e.target.value })} /></div>
            <div className="flex flex-wrap gap-3">
              <div><label className="label">Drift band %</label><input className="input w-32" type="number" value={newForm.drift_band} onChange={(e) => setNewForm({ ...newForm, drift_band: e.target.value })} /></div>
              <div><label className="label">Benchmark symbol</label><input className="input w-48" value={newForm.benchmark_symbol} onChange={(e) => setNewForm({ ...newForm, benchmark_symbol: e.target.value })} placeholder="SPY" /></div>
            </div>
            <div className="flex flex-wrap gap-2">
              <button className="btn-primary" disabled={busy || !newForm.name.trim()} onClick={create} data-testid="admin-model-create">{busy ? "Creating…" : ask ? "Ask Astra" : "Create"}</button>
              <button className="btn-ghost" onClick={() => setCreating(false)}>Cancel</button>
            </div>
          </div>
        </Card>
      )}

      {loading && !data ? <Card><Spinner /></Card> : !data?.length ? (
        <Card><Empty>No model portfolios</Empty></Card>
      ) : (
        <div className="space-y-3">
          {data.map((mp) => (
            <Card key={mp.id}>
              <div className="mb-2 flex flex-wrap items-start justify-between gap-2">
                <div className="min-w-0">
                  <div className="font-medium text-ink">{mp.name}</div>
                  {mp.description && <div className="mt-0.5 text-xs text-ink-muted">{mp.description}</div>}
                  <div className="mt-1 flex flex-wrap gap-1.5">
                    <span className={`${chip} font-mono`}>drift {(mp.drift_band * 100).toFixed(1)}%</span>
                    {mp.benchmark_symbol && <span className={`${chip} font-mono`}>{mp.benchmark_symbol}</span>}
                  </div>
                </div>
                <div className="flex items-center gap-2">
                  <button className="btn-outline px-2 py-1 text-xs" onClick={() => { setEditing(mp.id); const w: Record<string, string> = {}; (mp.targets || []).forEach((t: any) => { w[t.asset_class] = String(Math.round(t.target_weight * 100)); }); setWeights(w); }}>
                    <Pencil size={12} /> Edit weights
                  </button>
                  {ask
                    ? <button className="btn-ghost text-xs text-crit" onClick={() => deleteModel(mp)} aria-label="Ask Astra to delete"><Trash2 size={13} /></button>
                    : <InlineConfirmButton label={<X size={13} />} confirmLabel="Delete?" onConfirm={() => deleteModel(mp)} />}
                </div>
              </div>

              {editing !== mp.id && mp.targets?.length > 0 && (
                <div className="mt-2 grid grid-cols-2 gap-1.5 cq-sm:grid-cols-4">
                  {mp.targets.map((t: any) => (
                    <div key={t.asset_class} className="rounded-lg border border-border-soft p-2 text-center">
                      <div className="text-[11px] text-ink-muted">{titleCase(t.asset_class)}</div>
                      <div className="font-mono text-sm font-semibold tabular-nums text-ink">{(t.target_weight * 100).toFixed(0)}%</div>
                    </div>
                  ))}
                </div>
              )}

              {editing === mp.id && (
                <div className="mt-3 space-y-3 border-t border-border-soft pt-3">
                  <div className="grid grid-cols-2 gap-2 cq-sm:grid-cols-4">
                    {ASSET_CLASSES.map((ac) => (
                      <div key={ac}>
                        <label className="label text-[11px]">{titleCase(ac)} %</label>
                        <input className="input text-sm" type="number" min="0" max="100" value={weights[ac] || ""} onChange={(e) => setWeights({ ...weights, [ac]: e.target.value })} placeholder="0" />
                      </div>
                    ))}
                  </div>
                  <span className={`chip tabular-nums ${balanced ? "bg-ok-bg text-ok" : "bg-warn-bg text-warn"}`}>
                    Total {total.toFixed(0)}% / 100%
                  </span>
                  <div className="flex flex-wrap gap-2">
                    <button className="btn-primary text-sm" disabled={busy || !balanced} onClick={() => saveTargets(mp)} data-testid="admin-model-targets-save">{busy ? "Saving…" : ask ? "Ask Astra" : "Save weights"}</button>
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
