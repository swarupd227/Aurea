"use client";

import { useState } from "react";
import { UserPlus, TrendingUp, Phone, Plus, ArrowRight, FileText } from "lucide-react";
import { Card, Empty, SkeletonCard, ErrorState } from "@/components/ui";
import { useApi } from "@/lib/hooks";
import { api, getUser } from "@/lib/api";
import { money, titleCase } from "@/lib/format";
import { withRef } from "@/lib/refs";
import { useViewNav } from "./ViewNav";
import { ViewHeader, chip } from "./ViewHeader";
import { REGISTRATION_OPTIONS } from "./OnboardingView";
import { CrmPipelineCard, ProspectBriefCard } from "@/app/workspace/components/artifacts/CrmPipelineCard";

const STAGE_COLOR: Record<string, string> = {
  lead: "bg-ink-muted", qualified: "bg-info", proposal: "bg-warn", won: "bg-ok", lost: "bg-crit",
};
const CONTACT_TYPES: [string, string][] = [
  ["prospect", "Prospect"],
  ["referral_source", "Referral source"],
  ["centre_of_influence", "Centre of influence"],
];
const typeLabel = (t: string) => (CONTACT_TYPES.find(([v]) => v === t)?.[1] ?? titleCase(t)).toLowerCase();

function AddContactForm({ onAdded }: { onAdded: () => void }) {
  const nav = useViewNav();
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

  function clear() {
    setFullName(""); setContactType("prospect"); setSource(""); setOpen(false);
  }

  async function submit() {
    if (!fullName.trim()) return;
    if (nav.say) {
      nav.say(`Add ${fullName.trim()} to the pipeline as a ${typeLabel(contactType)}${source.trim() ? `, source: ${source.trim()}` : ""}.`);
      return clear();
    }
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
      <input className="input w-48" placeholder="Full name" aria-label="Full name" value={fullName}
        onChange={(e) => setFullName(e.target.value)} data-testid="pipeline-contact-name" />
      <select className="input w-40" aria-label="Contact type" value={contactType} onChange={(e) => setContactType(e.target.value)}>
        {CONTACT_TYPES.map(([v, l]) => <option key={v} value={v}>{l}</option>)}
      </select>
      <input className="input w-36" placeholder="Source (optional)" aria-label="Source" value={source}
        onChange={(e) => setSource(e.target.value)} />
      <button className="btn-primary text-sm" disabled={busy || !fullName.trim()} onClick={submit}
        data-testid="pipeline-contact-save">
        {busy ? "Saving…" : nav.say ? "Ask Astra" : "Save"}
      </button>
      <button className="btn-ghost text-sm" onClick={() => setOpen(false)}>Cancel</button>
    </div>
  );
}

function OpportunityForm({ contact, onAdded }: { contact: any; onAdded: () => void }) {
  const nav = useViewNav();
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
    if (nav.say) {
      nav.say(withRef(
        `Open an opportunity "${title.trim()}" for ${contact.full_name}${aum.trim() ? ` with an estimated AUM of ${aum.trim()}` : ""}; no probability was given.`,
        "contact", contact.id));
      setTitle(""); setAum(""); setOpen(false);
      return;
    }
    setBusy(true);
    try {
      await api("/api/crm/opportunities", {
        method: "POST",
        body: { contact_id: contact.id, title: title.trim(), estimated_aum: aum ? Number(aum) : undefined },
      });
      setTitle(""); setAum(""); setOpen(false);
      onAdded();
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="mt-1 flex flex-wrap items-center gap-1.5">
      <input className="input text-xs w-32" placeholder="Deal title" aria-label="Deal title" value={title}
        onChange={(e) => setTitle(e.target.value)} />
      <input className="input text-xs w-24 font-mono" inputMode="decimal" placeholder="Est. AUM" aria-label="Estimated AUM" value={aum}
        onChange={(e) => setAum(e.target.value)} />
      <button className="btn-primary text-xs" disabled={busy || !title.trim()} onClick={submit}>{nav.say ? "Ask Astra" : "Add"}</button>
      <button className="btn-ghost text-xs" aria-label="Close" onClick={() => setOpen(false)}>×</button>
    </div>
  );
}

function LogActivityForm({ contact, onLogged }: { contact: any; onLogged: () => void }) {
  const nav = useViewNav();
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
    if (nav.say) {
      nav.say(withRef(`Log a ${type} with ${contact.full_name}: ${detail.trim()}`, "contact", contact.id));
      setDetail(""); setType("call"); setOpen(false);
      return;
    }
    setBusy(true);
    try {
      await api("/api/crm/activity", { method: "POST", body: { contact_id: contact.id, activity_type: type, detail: detail.trim() } });
      setDetail(""); setOpen(false);
      onLogged();
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="mt-1 flex flex-wrap items-center gap-1.5">
      <select className="input text-xs w-24" aria-label="Activity type" value={type} onChange={(e) => setType(e.target.value)}>
        <option value="call">Call</option><option value="email">Email</option>
        <option value="meeting">Meeting</option><option value="note">Note</option>
      </select>
      <input className="input text-xs w-40" placeholder="What happened" aria-label="What happened" value={detail}
        onChange={(e) => setDetail(e.target.value)} />
      <button className="btn-primary text-xs" disabled={busy || !detail.trim()} onClick={submit}>{nav.say ? "Ask Astra" : "Log"}</button>
      <button className="btn-ghost text-xs" aria-label="Close" onClick={() => setOpen(false)}>×</button>
    </div>
  );
}

const OPEN_STAGES = ["lead", "qualified", "proposal"] as const;

/** Won is the onboarding case opening: one account type, and the case takes the contact's name and email. */
function StartOnboarding({ deal, onStarted }: { deal: any; onStarted: () => void }) {
  const nav = useViewNav();
  const [open, setOpen] = useState(false);
  const [type, setType] = useState("individual");
  const [busy, setBusy] = useState(false);
  const [failed, setFailed] = useState<string | null>(null);
  const market = (getUser()?.firm?.jurisdiction || "").toUpperCase();
  const options = REGISTRATION_OPTIONS.filter((o) => !o.only || o.only === market);

  if (!open) {
    return (
      <button className="btn-outline text-xs shrink-0" data-testid="pipeline-start-onboarding" onClick={() => setOpen(true)}>
        <ArrowRight size={12} /> Start onboarding
      </button>
    );
  }

  async function submit() {
    const label = options.find((o) => o.value === type)?.label || type;
    if (nav.say) {
      nav.say(withRef(`Start onboarding ${deal.contact || deal.title} from the pipeline deal "${deal.title}", as a ${label} account.`,
        "opportunity", deal.id));
      return setOpen(false);
    }
    setBusy(true);
    setFailed(null);
    try {
      await api("/api/onboarding/cases", { body: { prospect_name: deal.contact || deal.title, registration_type: type, opportunity_id: deal.id } });
      setOpen(false);
      onStarted();
    } catch (e: any) {
      setFailed(e?.message || "The case was not opened.");
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="flex flex-wrap items-center gap-1.5">
      <select className="input text-xs w-44" aria-label="Account type" value={type} onChange={(e) => setType(e.target.value)}>
        {options.map((o) => <option key={o.value} value={o.value}>{o.label}</option>)}
      </select>
      <button className="btn-primary text-xs" disabled={busy} onClick={submit}>{nav.say ? "Ask Astra" : "Open case"}</button>
      <button className="btn-ghost text-xs" aria-label="Close" onClick={() => setOpen(false)}>×</button>
      {failed && <span className="text-xs text-crit">{failed}</span>}
    </div>
  );
}

/** Lead, qualified, proposal or lost; lost asks for its reason. Won is not here: it is the case opening. */
function MoveStage({ deal, onMoved }: { deal: any; onMoved: () => void }) {
  const nav = useViewNav();
  const [to, setTo] = useState<string | null>(null);
  const [reason, setReason] = useState("");
  const [busy, setBusy] = useState(false);
  const [failed, setFailed] = useState<string | null>(null);

  async function submit(stage: string, why: string) {
    if (nav.say) {
      nav.say(withRef(
        `Move "${deal.title}"${deal.contact ? ` for ${deal.contact}` : ""} from ${titleCase(deal.stage)} to ${titleCase(stage)}${why ? `, because ${why}` : ""}.`,
        "opportunity", deal.id));
      setTo(null); setReason("");
      return;
    }
    setBusy(true);
    setFailed(null);
    try {
      await api(`/api/crm/opportunities/${deal.id}`, { method: "PATCH", body: { stage, reason: why || undefined } });
      setTo(null); setReason("");
      onMoved();
    } catch (e: any) {
      setFailed(e?.message || "Not moved.");
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="flex flex-wrap items-center gap-1.5">
      <select className="input w-32 text-xs shrink-0" aria-label={`Stage of "${deal.title}"`} value={to ?? deal.stage} disabled={busy}
        onChange={(e) => { const v = e.target.value; if (v === deal.stage) return setTo(null); setTo(v); }}>
        {[...OPEN_STAGES, "lost"].map((s) => <option key={s} value={s}>{titleCase(s)}</option>)}
      </select>
      {to && (
        <>
          <input className="input text-xs w-44" placeholder={to === "lost" ? "Why it was lost" : "Why (optional)"} aria-label="Reason"
            value={reason} onChange={(e) => setReason(e.target.value)} />
          <button className="btn-primary text-xs" disabled={busy || (to === "lost" && !reason.trim())}
            onClick={() => submit(to, reason.trim())}>{nav.say ? "Ask Astra" : "Move"}</button>
          <button className="btn-ghost text-xs" aria-label="Cancel" onClick={() => { setTo(null); setReason(""); }}>×</button>
        </>
      )}
      {failed && <span className="text-xs text-crit">{failed}</span>}
    </div>
  );
}

function BriefButton({ contact }: { contact: { id: string; full_name: string } }) {
  const nav = useViewNav();
  const [brief, setBrief] = useState<any>(null);
  async function show() {
    if (nav.say) return nav.say(withRef(`Prepare me for my meeting with ${contact.full_name}.`, "contact", contact.id));
    setBrief(brief ? null : await api(`/api/crm/contacts/${contact.id}/brief`));
  }
  return (
    <>
      <button className="btn-ghost text-xs" data-testid="pipeline-brief" onClick={show}><FileText size={12} /> Brief</button>
      {brief && <div className="mt-2 w-full"><ProspectBriefCard result={brief} /></div>}
    </>
  );
}

/** The funnel from lead to invested, each deal with its next move, and the contacts. In the work pane, every change
 * is the conversation's tool behind its Confirm card; on a page the forms act directly. */
export function PipelineView() {
  const nav = useViewNav();
  const { data: contacts, loading: contactsLoading, error: contactsError, refetch: refetchContacts } =
    useApi<any>("/api/crm/contacts");
  const { data: funnel, loading, error, refetch: refetchFunnel } = useApi<any>("/api/crm/overview");

  function refetchAll() {
    refetchContacts(); refetchFunnel();
  }

  const deals: any[] = funnel?.opportunities || [];
  const open = deals.filter((d) => OPEN_STAGES.includes(d.stage));
  const closed = deals.filter((d) => !OPEN_STAGES.includes(d.stage));

  return (
    <div className="view-container">
      <ViewHeader
        title="Growth pipeline"
        meta={funnel ? <span className="chip text-ink-muted">{funnel.scope_label}</span> : null}
        actions={<AddContactForm onAdded={refetchAll} />}
      />

      {loading && <SkeletonCard rows={4} />}
      {error && <ErrorState message={error} onRetry={refetchFunnel} what="pipeline" />}
      {funnel && <div className="mb-6"><CrmPipelineCard result={funnel} /></div>}

      <div className="grid gap-5 cq-lg:grid-cols-3">
        <div className="space-y-5 cq-lg:col-span-2">
          <Card>
            <div className="font-semibold text-ink mb-3 flex items-center gap-2"><TrendingUp size={17} /> Open deals</div>
            <div className="divide-y divide-border-soft">
              {open.map((o) => (
                <div key={o.id} className="py-3 flex flex-wrap items-center gap-3" data-testid="pipeline-deal">
                  <span className={`h-2 w-2 rounded-full shrink-0 ${STAGE_COLOR[o.stage]}`} />
                  <div className="min-w-0 flex-1">
                    <div className="text-sm font-medium text-ink">{o.title}</div>
                    <div className="mt-1 flex flex-wrap items-center gap-1.5">
                      <span className={chip}>{o.contact || "—"}</span>
                      {o.estimated_aum !== null
                        ? <span className={`${chip} font-mono tabular-nums`}>{money(o.estimated_aum, funnel?.currency)}</span>
                        : <span className="chip text-ink-muted">no estimate</span>}
                      {o.probability_pct !== null && <span className={`${chip} tabular-nums`}>{o.probability_pct}%</span>}
                      {o.quiet
                        ? <span className="chip bg-warn-bg text-warn tabular-nums" data-testid="pipeline-quiet">quiet {Math.round(o.quiet.days)}d · target {o.quiet.limit}d</span>
                        : o.days_since_touch !== null && <span className="chip text-ink-muted tabular-nums">touched {Math.round(o.days_since_touch)}d ago</span>}
                      {o.owner && <span className="chip text-ink-muted">{o.owner}</span>}
                    </div>
                  </div>
                  <MoveStage deal={o} onMoved={refetchAll} />
                  <StartOnboarding deal={o} onStarted={refetchAll} />
                </div>
              ))}
              {!loading && !open.length && <Empty>No open deals.</Empty>}
            </div>
          </Card>
          {closed.length > 0 && (
            <Card>
              <div className="font-semibold text-ink mb-3">Won and lost</div>
              <div className="divide-y divide-border-soft">
                {closed.map((o) => (
                  <div key={o.id} className="py-2.5 flex flex-wrap items-center gap-3" data-testid="pipeline-closed">
                    <span className={`h-2 w-2 rounded-full shrink-0 ${STAGE_COLOR[o.stage]}`} />
                    <div className="min-w-0 flex-1 text-sm text-ink">{o.contact || o.title}</div>
                    {o.stage === "won" ? (
                      o.onboarding ? (
                        <>
                          <span className={`chip tabular-nums ${o.onboarding.state === "invested" ? "bg-ok-bg text-ok" : "text-ink-muted"}`}>
                            {o.onboarding.state === "invested" ? "Invested" : `${o.onboarding.label} · step ${o.onboarding.step} of ${o.onboarding.steps}`}
                          </span>
                          {o.onboarding.overdue && <span className="chip bg-crit-bg text-crit">past SLA · {o.onboarding.owner}</span>}
                          {o.onboarding.case_id && o.onboarding.state !== "rejected" && (
                            <button className="btn-ghost text-xs" onClick={() => nav.open(`/studio/onboarding/${o.onboarding.case_id}`)}>Case</button>
                          )}
                        </>
                      ) : <span className="chip text-warn">won with no onboarding case</span>
                    ) : <span className="chip text-ink-muted">lost{o.lost_reason ? `: ${o.lost_reason}` : ", no reason recorded"}</span>}
                  </div>
                ))}
              </div>
            </Card>
          )}
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
                    <div className="ml-9 mt-1 flex flex-wrap items-center gap-3">
                      <BriefButton contact={c} />
                      {c.contact_type !== "client_contact" && <OpportunityForm contact={c} onAdded={refetchAll} />}
                      <LogActivityForm contact={c} onLogged={refetchAll} />
                    </div>
                  </div>
                ))}
                {!contacts?.items?.length && <Empty>No contacts yet</Empty>}
              </div>
            )}
          </Card>
        </div>
      </div>
    </div>
  );
}
