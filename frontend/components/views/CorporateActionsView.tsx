"use client";

import { useState } from "react";
import { CalendarClock, Check, ChevronDown, ChevronRight } from "lucide-react";
import { Card, StatTile, Empty, Spinner } from "@/components/ui";
import { useApi } from "@/lib/hooks";
import { api } from "@/lib/api";
import { titleCase, money } from "@/lib/format";
import { withRef } from "@/lib/refs";
import { useViewNav } from "./ViewNav";
import { ViewHeader, chip } from "./ViewHeader";

const STATUSES = ["announced", "election_open", "election_closed", "posted", "verified"];
const STATUS_COLOR: Record<string, string> = {
  announced: "bg-border-soft text-ink-muted", election_open: "bg-warn-bg text-warn",
  election_closed: "bg-warn-bg text-warn", posted: "bg-ok-bg text-ok", verified: "bg-ok-bg text-ok",
};

/** The action in words, for a sentence: "the ABC Cash dividend payable 2026-11-15". */
const actionName = (a: any) =>
  `the ${a.symbol || "unnamed instrument"} ${titleCase(a.action_type)}${a.payable_date ? ` payable ${a.payable_date}` : ""}`;

function EntitlementRow({ entitlement, action, onPosted }: { entitlement: any; action: any; onPosted: () => void }) {
  const nav = useViewNav();
  const [busy, setBusy] = useState(false);

  async function post() {
    if (nav.say) {
      const figures = `${entitlement.shares_entitled.toLocaleString()} share(s)`
        + `${entitlement.cash_amount !== null ? ` and ${money(entitlement.cash_amount)} cash` : ""}`;
      return nav.say(withRef(
        `Post ${entitlement.client ? `${entitlement.client}'s` : "one account's"} entitlement`
        + `${entitlement.account_name ? ` on ${entitlement.account_name}` : ""} under ${actionName(action)}: ${figures}.`
        + " Posting cannot be undone.",
        "entitlement", entitlement.id));
    }
    setBusy(true);
    try {
      await api(`/api/corporate-actions/entitlements/${entitlement.id}/post`, { method: "POST", body: {} });
      onPosted();
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="flex flex-wrap items-center gap-3 py-1.5 text-sm">
      <span className={`chip ${STATUS_COLOR[entitlement.status] || "bg-border-soft text-ink-muted"}`}>
        {titleCase(entitlement.status)}
      </span>
      {entitlement.client && <span className="chip text-ink-soft" title={entitlement.account_name ?? undefined}>{entitlement.client}</span>}
      <span className="flex-1 font-mono tabular-nums text-ink-muted">
        {entitlement.shares_entitled.toLocaleString()} share(s)
        {entitlement.cash_amount !== null ? ` · ${money(entitlement.cash_amount)}` : ""}
      </span>
      {entitlement.status !== "posted" && (
        <button className="btn-outline text-xs" disabled={busy} onClick={post} data-testid="ca-post-entitlement">
          {busy ? "Posting…" : nav.say ? "Ask Astra to post" : "Post"}
        </button>
      )}
      {entitlement.status === "posted" && (
        <span className="inline-flex items-center gap-1 text-xs text-ok"><Check size={13} /> Posted</span>
      )}
    </div>
  );
}

function ActionRow({ action, onChanged }: { action: any; onChanged: () => void }) {
  const nav = useViewNav();
  const [open, setOpen] = useState(false);
  const [computing, setComputing] = useState(false);
  const { data: entitlements, loading, refetch } = useApi<any>(
    open ? `/api/corporate-actions/${action.id}/entitlements` : null, [open, action.id]
  );

  async function compute() {
    if (nav.say) {
      return nav.say(withRef(`Compute the entitlements for ${actionName(action)}.`, "corporate_action", action.id));
    }
    setComputing(true);
    try {
      await api(`/api/corporate-actions/${action.id}/compute-entitlements`, { method: "POST", body: {} });
      refetch(); onChanged();
    } finally {
      setComputing(false);
    }
  }

  return (
    <div className="border-b border-border-soft last:border-0">
      <div className="py-3 flex flex-wrap items-center gap-3">
        <button onClick={() => setOpen((v) => !v)} className="text-ink-muted shrink-0"
          aria-label={open ? "Hide entitlements" : "Show entitlements"} aria-expanded={open}>
          {open ? <ChevronDown size={16} /> : <ChevronRight size={16} />}
        </button>
        <div className="min-w-[10rem] flex-1">
          <div className="text-sm font-medium text-ink">
            {action.symbol || "—"} · {titleCase(action.action_type)}
            {action.is_voluntary && <span className="chip bg-border-soft text-ink-muted ml-2">Voluntary</span>}
          </div>
          <div className="mt-1 flex flex-wrap gap-1.5">
            {action.payable_date
              ? <span className={`${chip} tabular-nums`}>Payable {action.payable_date}</span>
              : <span className="chip text-ink-muted">no payable date</span>}
          </div>
        </div>
        <span className={`chip ${STATUS_COLOR[action.status] || "bg-border-soft text-ink-muted"}`}>
          {titleCase(action.status)}
        </span>
        <span className="text-xs text-ink-muted whitespace-nowrap tabular-nums">
          {action.entitlements_posted}/{action.entitlement_count} posted
        </span>
      </div>
      {open && (
        <div className="pl-7 pb-3">
          {action.entitlement_count === 0 && (
            <button className="btn-outline text-xs" disabled={computing} onClick={compute} data-testid="ca-compute-entitlements">
              {computing ? "Computing…" : nav.say ? "Ask Astra to compute entitlements" : "Compute entitlements"}
            </button>
          )}
          {loading && <Spinner />}
          {entitlements?.items?.map((e: any) => (
            <EntitlementRow key={e.id} entitlement={e} action={action} onPosted={() => { refetch(); onChanged(); }} />
          ))}
        </div>
      )}
    </div>
  );
}

/** The corporate-action queue: capture, entitlement, election and posting. In the work pane,
 * computing and posting are the conversation's tools behind their Confirm card; on a page the
 * buttons act directly. */
export function CorporateActionsView() {
  const [status, setStatus] = useState<string>("");
  const { data, loading, refetch } = useApi<any>(
    `/api/corporate-actions${status ? `?status=${status}` : ""}`, [status]
  );

  const openCount = data?.items?.filter((a: any) => a.status !== "posted" && a.status !== "verified").length || 0;

  return (
    <div className="view-container">
      <ViewHeader title="Corporate actions" />

      <div className="grid grid-cols-2 gap-4 mb-6">
        <StatTile label="Total actions" value={data?.items?.length || 0} />
        <StatTile label="Awaiting posting" value={openCount} accent={openCount ? "warn" : "ok"} />
      </div>

      <Card>
        <div className="flex flex-wrap items-center justify-between gap-2 mb-3">
          <div className="font-semibold text-ink flex items-center gap-2"><CalendarClock size={17} /> Queue</div>
          <select className="input w-40 text-xs" aria-label="Status" value={status} onChange={(e) => setStatus(e.target.value)}>
            <option value="">All statuses</option>
            {STATUSES.map((s) => <option key={s} value={s}>{titleCase(s)}</option>)}
          </select>
        </div>
        {loading && <Spinner />}
        {!loading && !data?.items?.length && <Empty>No corporate actions{status ? ` · ${titleCase(status)}` : ""}</Empty>}
        {!loading && data?.items?.map((a: any) => (
          <ActionRow key={a.id} action={a} onChanged={refetch} />
        ))}
      </Card>
    </div>
  );
}
