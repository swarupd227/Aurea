"use client";
import { useState } from "react";
import { UserPlus, TrendingUp, Phone, Plus } from "lucide-react";
import PageHeader from "@/components/PageHeader";
import { Card, StatTile, Empty, SkeletonCard, ErrorState } from "@/components/ui";
import { useApi } from "@/lib/hooks";
import { api } from "@/lib/api";
import { money, titleCase, timeAgo } from "@/lib/format";

const STAGES = ["lead", "qualified", "proposal", "won", "lost"] as const;
const STAGE_COLOR: Record<string, string> = {
  lead: "bg-ink-muted", qualified: "bg-info", proposal: "bg-warn", won: "bg-ok", lost: "bg-crit",
};

function AddContactForm({ onAdded }: { onAdded: () => void }) {
  const [open, setOpen] = useState(false);
  const [fullName, setFullName] = useState("");
  const [contactType, setContactType] = useState("prospect");
  const [source, setSource] = useState("");
  const [busy, setBusy] = useState(false);

  if (!open) {
    return (
      <button className="btn-outline text-sm" onClick={() => setOpen(true)} data-testid="pipeline-add-contact">
        <Plus size={15} /> Add contact
      </button>
    );
  }

  async function submit() {
    if (!fullName.trim()) return;
    setBusy(true);
    try {
      await api("/api/crm/contacts", {
        method: "POST",
        body: { full_name: fullName.trim(), contact_type: contactType, source: source || undefined },
      });
      setFullName(""); setSource(""); setOpen(false);
      onAdded();
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="flex flex-wrap items-center gap-2">
      <input className="input w-48" placeholder="Full name" value={fullName}
        onChange={(e) => setFullName(e.target.value)} data-testid="pipeline-contact-name" />
      <select className="input w-40" value={contactType} onChange={(e) => setContactType(e.target.value)}>
        <option value="prospect">Prospect</option>
        <option value="referral_source">Referral source</option>
        <option value="centre_of_influence">Centre of influence</option>
      </select>
      <input className="input w-36" placeholder="Source (optional)" value={source}
        onChange={(e) => setSource(e.target.value)} />
      <button className="btn-primary text-sm" disabled={busy || !fullName.trim()} onClick={submit}
        data-testid="pipeline-contact-save">
        {busy ? "Saving…" : "Save"}
      </button>
      <button className="btn-ghost text-sm" onClick={() => setOpen(false)}>Cancel</button>
    </div>
  );
}

function OpportunityForm({ contactId, onAdded }: { contactId: string; onAdded: () => void }) {
  const [open, setOpen] = useState(false);
  const [title, setTitle] = useState("");
  const [aum, setAum] = useState("");
  const [busy, setBusy] = useState(false);

  if (!open) {
    return (
      <button className="btn-ghost text-xs" onClick={() => setOpen(true)}>
        <Plus size={12} /> Opportunity
      </button>
    );
  }

  async function submit() {
    if (!title.trim()) return;
    setBusy(true);
    try {
      await api("/api/crm/opportunities", {
        method: "POST",
        body: { contact_id: contactId, title: title.trim(), estimated_aum: aum ? Number(aum) : undefined },
      });
      setTitle(""); setAum(""); setOpen(false);
      onAdded();
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="flex items-center gap-1.5 mt-1">
      <input className="input text-xs w-32" placeholder="Deal title" value={title}
        onChange={(e) => setTitle(e.target.value)} />
      <input className="input text-xs w-24" placeholder="Est. AUM" value={aum}
        onChange={(e) => setAum(e.target.value)} />
      <button className="btn-primary text-xs" disabled={busy || !title.trim()} onClick={submit}>Add</button>
      <button className="btn-ghost text-xs" onClick={() => setOpen(false)}>×</button>
    </div>
  );
}

function LogActivityForm({ contactId, onLogged }: { contactId: string; onLogged: () => void }) {
  const [open, setOpen] = useState(false);
  const [detail, setDetail] = useState("");
  const [type, setType] = useState("call");
  const [busy, setBusy] = useState(false);

  if (!open) {
    return (
      <button className="btn-ghost text-xs" onClick={() => setOpen(true)}>
        <Phone size={12} /> Log activity
      </button>
    );
  }

  async function submit() {
    if (!detail.trim()) return;
    setBusy(true);
    try {
      await api("/api/crm/activity", { method: "POST", body: { contact_id: contactId, activity_type: type, detail: detail.trim() } });
      setDetail(""); setOpen(false);
      onLogged();
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="flex items-center gap-1.5 mt-1">
      <select className="input text-xs w-24" value={type} onChange={(e) => setType(e.target.value)}>
        <option value="call">Call</option><option value="email">Email</option>
        <option value="meeting">Meeting</option><option value="note">Note</option>
      </select>
      <input className="input text-xs w-40" placeholder="What happened" value={detail}
        onChange={(e) => setDetail(e.target.value)} />
      <button className="btn-primary text-xs" disabled={busy || !detail.trim()} onClick={submit}>Log</button>
      <button className="btn-ghost text-xs" onClick={() => setOpen(false)}>×</button>
    </div>
  );
}

export default function Pipeline() {
  const { data: contacts, loading: contactsLoading, error: contactsError, refetch: refetchContacts } =
    useApi<any>("/api/crm/contacts");
  const { data: opportunities, refetch: refetchOpps } = useApi<any>("/api/crm/opportunities");
  const { data: summary, refetch: refetchSummary } = useApi<any>("/api/crm/pipeline-summary");
  const [movingId, setMovingId] = useState<string | null>(null);

  function refetchAll() {
    refetchContacts(); refetchOpps(); refetchSummary();
  }

  async function moveStage(id: string, stage: string) {
    setMovingId(id);
    try {
      await api(`/api/crm/opportunities/${id}`, { method: "PATCH", body: { stage } });
      refetchAll();
    } finally {
      setMovingId(null);
    }
  }

  const contactsById: Record<string, any> = {};
  (contacts?.items || []).forEach((c: any) => { contactsById[c.id] = c; });

  return (
    <div>
      <PageHeader title="Growth pipeline"
        sub="Contacts, opportunities, and activity — kept separate from the client graph until a deal converts (L200-8 §2.1)."
        actions={<AddContactForm onAdded={refetchAll} />} />

      {summary && (
        <div className="grid grid-cols-2 lg:grid-cols-4 gap-4 mb-6">
          <StatTile label="Contacts" value={contacts?.items?.length || 0} />
          <StatTile label="Open opportunities" value={summary.open_pipeline_count} />
          <StatTile label="Weighted open value" value={money(summary.weighted_open_value)} accent="warn" />
          <StatTile label="Win rate" value={summary.win_rate_pct !== null ? `${summary.win_rate_pct}%` : "—"} accent="ok" />
        </div>
      )}

      <div className="grid lg:grid-cols-3 gap-5">
        <div className="lg:col-span-2 space-y-5">
          <Card>
            <div className="font-semibold text-ink mb-3 flex items-center gap-2"><TrendingUp size={17} /> Pipeline</div>
            <div className="grid grid-cols-5 gap-2 mb-4">
              {STAGES.map((s) => (
                <div key={s} className="text-center">
                  <div className={`h-1.5 rounded-full ${STAGE_COLOR[s]} mb-1`} />
                  <div className="text-xs text-ink-muted">{titleCase(s)}</div>
                  <div className="text-sm font-semibold text-ink">{summary?.by_stage?.[s] || 0}</div>
                </div>
              ))}
            </div>
            <div className="divide-y divide-border-soft">
              {(opportunities?.items || []).map((o: any) => (
                <div key={o.id} className="py-3 flex items-center gap-3">
                  <span className={`h-2 w-2 rounded-full shrink-0 ${STAGE_COLOR[o.stage]}`} />
                  <div className="min-w-0 flex-1">
                    <div className="text-sm font-medium text-ink">{o.title}</div>
                    <div className="text-xs text-ink-muted">
                      {contactsById[o.contact_id]?.full_name || "—"} · {o.estimated_aum ? money(o.estimated_aum) : "no AUM estimate"}
                    </div>
                  </div>
                  <select className="input w-32 text-xs shrink-0" value={o.stage} disabled={movingId === o.id}
                    onChange={(e) => moveStage(o.id, e.target.value)}>
                    {STAGES.map((s) => <option key={s} value={s}>{titleCase(s)}</option>)}
                  </select>
                </div>
              ))}
              {!opportunities?.items?.length && <Empty>No opportunities yet.</Empty>}
            </div>
          </Card>
        </div>

        <div className="space-y-5">
          <Card>
            <div className="font-semibold text-ink mb-3 flex items-center gap-2"><UserPlus size={17} /> Contacts</div>
            {contactsLoading && <SkeletonCard rows={3} />}
            {contactsError && <ErrorState message={contactsError} onRetry={refetchContacts} what="contacts" />}
            {!contactsLoading && !contactsError && (
              <div className="space-y-4">
                {(contacts?.items || []).map((c: any) => (
                  <div key={c.id}>
                    <div className="flex items-center gap-2">
                      <span className="h-7 w-7 rounded-full bg-border-soft text-ink flex items-center justify-center text-xs font-semibold">
                        {c.full_name.slice(0, 1)}
                      </span>
                      <div className="min-w-0">
                        <div className="text-sm text-ink">{c.full_name}</div>
                        <div className="text-[11px] text-ink-muted">{titleCase(c.contact_type)}{c.source ? ` · ${c.source}` : ""}</div>
                      </div>
                    </div>
                    <div className="ml-9 mt-1 flex items-center gap-3">
                      <OpportunityForm contactId={c.id} onAdded={refetchAll} />
                      <LogActivityForm contactId={c.id} onLogged={refetchAll} />
                    </div>
                  </div>
                ))}
                {!contacts?.items?.length && <Empty>No contacts yet — add one above.</Empty>}
              </div>
            )}
          </Card>
        </div>
      </div>
    </div>
  );
}
