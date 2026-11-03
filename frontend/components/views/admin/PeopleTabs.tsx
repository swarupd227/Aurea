"use client";

import { useState } from "react";
import { Save, Copy, Check, UserPlus, KeyRound, X, FileText, Pencil, Link2, Plus } from "lucide-react";
import { Card, Spinner, InlineConfirmButton, Empty } from "@/components/ui";
import { useApi } from "@/lib/hooks";
import { api } from "@/lib/api";
import { titleCase } from "@/lib/format";
import { withRef } from "@/lib/refs";
import { ViewLink } from "../ViewNav";
import { chip } from "../ViewHeader";
import { ActiveChip, FormError, PageOnly, ReasonInput, TableCard, TD, TH, because, q, useAsk } from "./shared";

// ── Users ─────────────────────────────────────────────────────────────────────

export const ROLE_LABELS: Record<string, string> = {
  admin: "Admin", adviser: "Adviser", paraplanner: "Paraplanner",
  portfolio_team: "Portfolio", research_cio: "Research/CIO",
  compliance: "Compliance", operations: "Operations", branch_leader: "Branch Leader",
};

function RoleBadge({ role }: { role: string }) {
  return <span className={chip}>{ROLE_LABELS[role] || titleCase(role)}</span>;
}

function CopyButton({ value }: { value: string }) {
  const [copied, setCopied] = useState(false);
  function copy() {
    navigator.clipboard.writeText(value).then(() => { setCopied(true); setTimeout(() => setCopied(false), 2000); });
  }
  return (
    <button onClick={copy} className="btn-outline px-2 py-1 text-xs">
      {copied ? <><Check size={13} className="text-ok" /> Copied</> : <><Copy size={13} /> Copy link</>}
    </button>
  );
}

const staffOnly = (users: any[] | null | undefined) =>
  users?.filter((u) => u.is_active && ["adviser", "paraplanner", "portfolio_team"].includes(u.role)) || [];

/** change_user, in words: only what changed; null when nothing did. */
function userChange(u: any, e: any): string | null {
  const parts: string[] = [];
  if ((e.full_name || "").trim() !== (u.full_name || "")) parts.push(`name ${q(e.full_name)}`);
  if (e.role !== u.role) parts.push(`role ${ROLE_LABELS[e.role] || titleCase(e.role)}`);
  if ((e.title || "").trim() !== (u.title || "")) parts.push((e.title || "").trim() ? `title ${q(e.title)}` : "clear the title");
  if (!!e.is_active !== !!u.is_active) parts.push(e.is_active ? "reactivate the account" : "deactivate the account");
  return parts.length ? `Change the user ${u.full_name} (${u.email}): ${parts.join("; ")}` : null;
}

export function UsersTab() {
  const ask = useAsk();
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
    if (ask) {
      ask(`Invite ${form.full_name.trim()} (${form.email.trim()}) as ${ROLE_LABELS[form.role]}${form.title.trim() ? `, title ${q(form.title)}` : ""}`);
      setShowForm(false);
      setForm({ email: "", full_name: "", role: "adviser", title: "" });
      return;
    }
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

  const editing = editTarget ? users?.find((u) => u.id === editTarget.id) : null;
  const editSentence = ask && editing ? userChange(editing, editTarget) : null;

  async function saveEdit() {
    if (!editTarget) return;
    if (ask) {
      if (editSentence) ask(editSentence + because(editTarget.reason || ""));
      setEditTarget(null);
      return;
    }
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
    <div className="max-w-5xl space-y-4" data-testid="admin-users">
      {tokenResult && (
        <div className="card flex items-start gap-3 border-ok/30 bg-ok-bg p-4">
          <Check size={16} className="mt-0.5 shrink-0 text-ok" />
          <div className="min-w-0 flex-1">
            <div className="flex flex-wrap items-center gap-1.5 text-sm font-medium text-ink">
              {tokenResult.type === "invite" ? "Invite link" : "Password reset link"} · {tokenResult.name}
              <span className={chip}>expires in {tokenResult.type === "invite" ? "7 days" : "24 hours"}</span>
            </div>
            <div className="mt-1 break-all font-mono text-xs text-ink-muted">{origin}/accept-invite?token={tokenResult.token}</div>
            <div className="mt-2 flex gap-2">
              <CopyButton value={`${origin}/accept-invite?token=${tokenResult.token}`} />
              <button onClick={() => setTokenResult(null)} className="btn-outline px-2 py-1 text-xs" aria-label="Close"><X size={13} /></button>
            </div>
          </div>
        </div>
      )}

      {showForm ? (
        <Card>
          <div className="mb-3 flex items-center gap-2 font-semibold text-ink"><UserPlus size={15} /> Invite user</div>
          {err && <FormError>{err}</FormError>}
          <div className="mb-3 grid gap-3 cq-sm:grid-cols-2">
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
            <div><label className="label">Title</label>
              <input className="input" placeholder="Senior Adviser" value={form.title}
                onChange={(e) => setForm({ ...form, title: e.target.value })} />
            </div>
          </div>
          <div className="flex flex-wrap gap-2">
            <button className="btn-primary" onClick={invite} disabled={saving} data-testid="admin-user-invite">
              {saving ? "Creating…" : <><UserPlus size={15} /> {ask ? "Ask Astra" : "Create & copy invite link"}</>}
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
          <div className="mb-3 font-semibold text-ink">Edit · {editTarget.email}</div>
          <div className="mb-3 grid gap-3 cq-sm:grid-cols-3">
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
            {ask && (
              <>
                <label className="flex cursor-pointer items-center gap-2 text-sm text-ink">
                  <input type="checkbox" className="h-4 w-4 rounded" checked={!!editTarget.is_active}
                    onChange={(e) => setEditTarget({ ...editTarget, is_active: e.target.checked })} />
                  Active
                </label>
                <div className="cq-sm:col-span-2"><label className="label">Reason</label>
                  <ReasonInput className="w-full" value={editTarget.reason || ""} onChange={(v) => setEditTarget({ ...editTarget, reason: v })} />
                </div>
              </>
            )}
          </div>
          <div className="flex flex-wrap gap-2">
            <button className="btn-primary" onClick={saveEdit} data-testid="admin-user-save"
              disabled={saving || (!!ask && (!editSentence || !(editTarget.reason || "").trim()))}>
              {saving ? "Saving…" : <><Save size={15} /> {ask ? "Ask Astra" : "Save"}</>}
            </button>
            <button className="btn-outline" onClick={() => setEditTarget(null)}>Cancel</button>
          </div>
        </Card>
      )}

      {loading && !users ? (
        <Card><Spinner /></Card>
      ) : !users?.length ? (
        <Card><Empty>No users</Empty></Card>
      ) : (
        <TableCard testid="admin-users-table">
          <thead>
            <tr className="border-b border-border-soft">
              <th className={TH}>Name</th>
              <th className={TH}>Email</th>
              <th className={TH}>Role</th>
              <th className={TH}>Status</th>
              <th className={`${TH} text-right`}>Actions</th>
            </tr>
          </thead>
          <tbody className="divide-y divide-border-soft">
            {users.map((u) => (
              <tr key={u.id}>
                <td className={TD}>
                  <div className="font-medium text-ink">{u.full_name}</div>
                  {u.title && <div className="text-xs text-ink-muted">{u.title}</div>}
                </td>
                <td className={`${TD} text-ink-soft`}>{u.email}</td>
                <td className={TD}><RoleBadge role={u.role} /></td>
                <td className={TD}><ActiveChip active={!!u.is_active} /></td>
                <td className={TD}>
                  <div className="flex flex-wrap items-center justify-end gap-1.5">
                    <button className="btn-outline px-2 py-1 text-xs" onClick={() => setEditTarget({ ...u, reason: "" })}>Edit</button>
                    {ask ? (
                      <>
                        <button className="btn-outline px-2 py-1 text-xs" onClick={() => setEditTarget({ ...u, is_active: !u.is_active, reason: "" })}>
                          {u.is_active ? "Deactivate" : "Activate"}
                        </button>
                        <button className="btn-outline px-2 py-1 text-xs" disabled>
                          {u.is_active ? <><KeyRound size={11} /> Reset pwd</> : <><UserPlus size={11} /> Resend invite</>}
                        </button>
                        {u.is_active && <button className="btn-outline px-2 py-1 text-xs" disabled>Force logout</button>}
                        <PageOnly />
                      </>
                    ) : (
                      <>
                        {u.is_active ? (
                          <button className="btn-outline px-2 py-1 text-xs" onClick={() => resetPwd(u)}>
                            <KeyRound size={11} /> Reset pwd
                          </button>
                        ) : (
                          <button className="btn-outline px-2 py-1 text-xs" onClick={() => resendInvite(u)}>
                            <UserPlus size={11} /> Resend invite
                          </button>
                        )}
                        {u.is_active && (
                          <InlineConfirmButton
                            label="Force logout"
                            confirmLabel="Log out all sessions?"
                            onConfirm={async () => { await api(`/api/admin/users/${u.id}/force-logout`, { method: "POST", body: {} }); }}
                          />
                        )}
                        <InlineConfirmButton
                          label={u.is_active ? "Deactivate" : "Activate"}
                          confirmLabel={u.is_active ? "Deactivate & end all sessions?" : "Reactivate account?"}
                          onConfirm={u.is_active ? () => deactivateUser(u) : () => activateUser(u)}
                        />
                      </>
                    )}
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

// ── Clients ───────────────────────────────────────────────────────────────────

export const SEGMENTS: Record<string, string> = {
  private_wealth: "Private wealth", mass_affluent: "Mass affluent",
  for_purpose: "For purpose", institutional: "Institutional", next_gen: "Next gen",
};

const assignSentence = (adviser: string, household: string, reason: string, householdId: string) =>
  withRef(`Assign ${adviser} as adviser to ${household}${because(reason)}`, "household", householdId);

/** A new client is onboarded, with screening and agreements, never created here. */
export function ClientsTab() {
  const ask = useAsk();
  const { data: clients, loading, refetch } = useApi<any[]>("/api/admin/clients");
  const { data: advisers } = useApi<any[]>("/api/admin/users");
  const [csvImporting, setCsvImporting] = useState(false);
  const [csvResult, setCsvResult] = useState<string | null>(null);
  const [assigningId, setAssigningId] = useState<string | null>(null);
  const [assignAdviser, setAssignAdviser] = useState("");
  const [reason, setReason] = useState("");

  const staffAdvisers = staffOnly(advisers);

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

  async function saveAdviser(c: any) {
    if (ask) {
      const who = staffAdvisers.find((u: any) => u.id === assignAdviser);
      if (who) ask(assignSentence(who.full_name, c.name, reason, c.id));
      setAssigningId(null);
      return;
    }
    try {
      await api(`/api/admin/clients/${c.id}/adviser?adviser_id=${assignAdviser}`, { method: "PATCH", body: {} });
      setAssigningId(null);
      refetch();
    } catch (e: any) { alert(e.message); }
  }

  return (
    <div className="max-w-5xl space-y-4" data-testid="admin-clients">
      <div className="flex flex-wrap items-center justify-end gap-2">
        {csvResult && <span className={chip}>{csvResult}</span>}
        {ask ? (
          <>
            <button className="btn-outline text-sm" disabled><FileText size={14} /> Import CSV</button>
            <PageOnly />
          </>
        ) : (
          <label className={`btn-outline cursor-pointer text-sm ${csvImporting ? "opacity-50" : ""}`}>
            <FileText size={14} /> {csvImporting ? "Importing…" : "Import CSV"}
            <input type="file" accept=".csv,text/csv" className="hidden" onChange={importCsv} disabled={csvImporting} />
          </label>
        )}
        <ViewLink href="/studio/onboarding" className="btn-primary" data-testid="admin-start-onboarding"><Plus size={15} /> Start onboarding</ViewLink>
      </div>

      {loading && !clients ? <Card><Spinner /></Card> : !clients?.length ? (
        <Card><Empty>No clients</Empty></Card>
      ) : (
        <TableCard testid="admin-clients-table">
          <thead>
            <tr className="border-b border-border-soft">
              <th className={TH}>Name</th>
              <th className={TH}>Email</th>
              <th className={TH}>Segment</th>
              <th className={TH}>Adviser</th>
              <th className={TH}>Added</th>
            </tr>
          </thead>
          <tbody className="divide-y divide-border-soft">
            {clients.map((c) => (
              <tr key={c.id}>
                <td className={`${TD} font-medium text-ink`}>{c.name}</td>
                <td className={`${TD} text-ink-soft`}>{c.primary_email || "—"}</td>
                <td className={TD}><span className={chip}>{SEGMENTS[c.segment] || c.segment}</span></td>
                <td className={TD}>
                  {assigningId === c.id ? (
                    <div className="flex flex-wrap items-center gap-1">
                      <select className="input w-auto py-1 text-xs" value={assignAdviser} onChange={(e) => setAssignAdviser(e.target.value)} aria-label="Adviser">
                        <option value="">Adviser</option>
                        {staffAdvisers.map((u: any) => <option key={u.id} value={u.id}>{u.full_name}</option>)}
                      </select>
                      {ask && <ReasonInput className="w-32" value={reason} onChange={setReason} />}
                      <button className="btn-primary px-2 py-1 text-xs" onClick={() => saveAdviser(c)} data-testid="admin-client-assign"
                        disabled={!assignAdviser || (!!ask && !reason.trim())}>{ask ? "Ask Astra" : "Save"}</button>
                      <button className="btn-outline px-2 py-1 text-xs" onClick={() => setAssigningId(null)} aria-label="Cancel"><X size={12} /></button>
                    </div>
                  ) : (
                    <span className="flex items-center gap-1.5">
                      {c.adviser ? <span className="text-ink-soft">{c.adviser}</span> : <span className={`${chip} text-ink-muted`}>Unassigned</span>}
                      <button onClick={() => { setAssigningId(c.id); setAssignAdviser(""); setReason(""); }} className="text-ink-muted hover:text-ink" aria-label="Change adviser">
                        <Pencil size={11} />
                      </button>
                    </span>
                  )}
                </td>
                <td className={`${TD} font-mono text-xs text-ink-muted`}>{c.created_at ? new Date(c.created_at).toLocaleDateString() : "—"}</td>
              </tr>
            ))}
          </tbody>
        </TableCard>
      )}
    </div>
  );
}

// ── Assignments ───────────────────────────────────────────────────────────────

export function AssignmentsTab() {
  const ask = useAsk();
  const { data: assignments, loading, refetch } = useApi<any[]>("/api/admin/assignments");
  const { data: users } = useApi<any[]>("/api/admin/users");
  const { data: clients } = useApi<any[]>("/api/admin/clients");
  const [showForm, setShowForm] = useState(false);
  const [form, setForm] = useState({ household_id: "", adviser_id: "", reason: "" });
  const [removing, setRemoving] = useState<{ id: string; reason: string } | null>(null);
  const [saving, setSaving] = useState(false);
  const [err, setErr] = useState("");

  const staffAdvisers = staffOnly(users);

  async function create() {
    setErr("");
    if (!form.household_id || !form.adviser_id) { setErr("Both client and adviser are required."); return; }
    if (ask) {
      const who = staffAdvisers.find((u: any) => u.id === form.adviser_id);
      const hh = clients?.find((c: any) => c.id === form.household_id);
      if (who && hh) ask(assignSentence(who.full_name, hh.name, form.reason, hh.id));
      setShowForm(false);
      setForm({ household_id: "", adviser_id: "", reason: "" });
      return;
    }
    setSaving(true);
    try {
      await api("/api/admin/assignments", { method: "POST", body: { household_id: form.household_id, adviser_id: form.adviser_id } });
      setShowForm(false);
      setForm({ household_id: "", adviser_id: "", reason: "" });
      refetch();
    } catch (e: any) { setErr(e.message || "Error creating assignment."); }
    finally { setSaving(false); }
  }

  function askRemove(a: any) {
    if (!ask || !removing) return;
    ask(withRef(`Leave ${a.household_name} with no adviser, removing ${a.adviser_name}${because(removing.reason)}`, "household", a.household_id));
    setRemoving(null);
  }

  return (
    <div className="max-w-4xl space-y-4" data-testid="admin-assignments">
      {showForm ? (
        <Card>
          <div className="mb-3 flex items-center gap-2 font-semibold text-ink"><Link2 size={15} /> Assign adviser</div>
          {err && <FormError>{err}</FormError>}
          <div className="mb-3 grid gap-3 cq-sm:grid-cols-2">
            <div>
              <label className="label">Client household</label>
              <select className="input" value={form.household_id} onChange={(e) => setForm({ ...form, household_id: e.target.value })}>
                <option value="">Select client</option>
                {clients?.map((c: any) => <option key={c.id} value={c.id}>{c.name}</option>)}
              </select>
            </div>
            <div>
              <label className="label">Adviser</label>
              <select className="input" value={form.adviser_id} onChange={(e) => setForm({ ...form, adviser_id: e.target.value })}>
                <option value="">Select adviser</option>
                {staffAdvisers.map((u: any) => <option key={u.id} value={u.id}>{u.full_name} ({titleCase(u.role)})</option>)}
              </select>
            </div>
            {ask && (
              <div className="cq-sm:col-span-2"><label className="label">Reason</label>
                <ReasonInput className="w-full" value={form.reason} onChange={(v) => setForm({ ...form, reason: v })} />
              </div>
            )}
          </div>
          <div className="flex flex-wrap gap-2">
            <button className="btn-primary" onClick={create} disabled={saving || (!!ask && !form.reason.trim())} data-testid="admin-assignment-save">
              {saving ? "Saving…" : <><Link2 size={14} /> {ask ? "Ask Astra" : "Assign"}</>}
            </button>
            <button className="btn-outline" onClick={() => { setShowForm(false); setErr(""); }}>Cancel</button>
          </div>
        </Card>
      ) : (
        <div className="flex justify-end">
          <button className="btn-primary" onClick={() => setShowForm(true)}><Plus size={15} /> Assign adviser</button>
        </div>
      )}
      {loading && !assignments ? <Card><Spinner /></Card> : !assignments?.length ? (
        <Card><Empty>No assignments</Empty></Card>
      ) : (
        <TableCard testid="admin-assignments-table">
          <thead>
            <tr className="border-b border-border-soft">
              <th className={TH}>Client</th>
              <th className={TH}>Adviser</th>
              <th className={TH}>Email</th>
              <th className={TH}>Assigned</th>
              <th className={`${TH} text-right`}>Actions</th>
            </tr>
          </thead>
          <tbody className="divide-y divide-border-soft">
            {assignments.map((a: any) => (
              <tr key={a.id}>
                <td className={`${TD} font-medium text-ink`}>{a.household_name}</td>
                <td className={`${TD} text-ink-soft`}>{a.adviser_name}</td>
                <td className={`${TD} text-xs text-ink-muted`}>{a.adviser_email || "—"}</td>
                <td className={`${TD} font-mono text-xs text-ink-muted`}>{a.created_at ? new Date(a.created_at).toLocaleDateString() : "—"}</td>
                <td className={`${TD} text-right`}>
                  {ask ? (
                    removing?.id === a.id ? (
                      <div className="flex flex-wrap items-center justify-end gap-1">
                        <ReasonInput className="w-32" value={removing.reason} onChange={(v) => setRemoving({ id: a.id, reason: v })} />
                        <button className="btn-primary px-2 py-1 text-xs" disabled={!removing.reason.trim()} onClick={() => askRemove(a)}>Ask Astra</button>
                        <button className="btn-outline px-2 py-1 text-xs" onClick={() => setRemoving(null)} aria-label="Cancel"><X size={12} /></button>
                      </div>
                    ) : (
                      <button className="btn-ghost text-xs text-crit" onClick={() => setRemoving({ id: a.id, reason: "" })}>Remove</button>
                    )
                  ) : (
                    <InlineConfirmButton
                      label="Remove"
                      confirmLabel="Remove assignment?"
                      onConfirm={async () => { await api(`/api/admin/assignments/${a.id}`, { method: "DELETE" }); refetch(); }}
                    />
                  )}
                </td>
              </tr>
            ))}
          </tbody>
        </TableCard>
      )}
    </div>
  );
}
