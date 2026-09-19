"use client";
import { useState } from "react";
import { CalendarClock, Check, ChevronDown, ChevronRight } from "lucide-react";
import PageHeader from "@/components/PageHeader";
import { Card, StatTile, Empty, Spinner } from "@/components/ui";
import { useApi } from "@/lib/hooks";
import { api } from "@/lib/api";
import { titleCase, money } from "@/lib/format";

const STATUSES = ["announced", "election_open", "election_closed", "posted", "verified"];
const STATUS_COLOR: Record<string, string> = {
  announced: "bg-border-soft text-ink-muted", election_open: "bg-warn-bg text-warn",
  election_closed: "bg-warn-bg text-warn", posted: "bg-ok-bg text-ok", verified: "bg-ok-bg text-ok",
};

function EntitlementRow({ entitlement, onPosted }: { entitlement: any; onPosted: () => void }) {
  const [busy, setBusy] = useState(false);

  async function post() {
    setBusy(true);
    try {
      await api(`/api/corporate-actions/entitlements/${entitlement.id}/post`, { method: "POST", body: {} });
      onPosted();
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="flex items-center gap-3 py-1.5 text-sm">
      <span className={`chip ${STATUS_COLOR[entitlement.status] || "bg-border-soft text-ink-muted"}`}>
        {titleCase(entitlement.status)}
      </span>
      <span className="text-ink-muted flex-1">
        {entitlement.shares_entitled.toLocaleString()} share(s)
        {entitlement.cash_amount !== null ? ` · ${money(entitlement.cash_amount)}` : ""}
      </span>
      {entitlement.status !== "posted" && (
        <button className="btn-outline text-xs" disabled={busy} onClick={post} data-testid="ca-post-entitlement">
          {busy ? "Posting…" : "Post"}
        </button>
      )}
      {entitlement.status === "posted" && (
        <span className="inline-flex items-center gap-1 text-xs text-ok"><Check size={13} /> Posted</span>
      )}
    </div>
  );
}

function ActionRow({ action, onChanged }: { action: any; onChanged: () => void }) {
  const [open, setOpen] = useState(false);
  const [computing, setComputing] = useState(false);
  const { data: entitlements, loading, refetch } = useApi<any>(
    open ? `/api/corporate-actions/${action.id}/entitlements` : null, [open, action.id]
  );

  async function compute() {
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
      <div className="py-3 flex items-center gap-3">
        <button onClick={() => setOpen((v) => !v)} className="text-ink-muted shrink-0">
          {open ? <ChevronDown size={16} /> : <ChevronRight size={16} />}
        </button>
        <div className="min-w-0 flex-1">
          <div className="text-sm font-medium text-ink">
            {action.symbol || "—"} · {titleCase(action.action_type)}
            {action.is_voluntary && <span className="chip bg-border-soft text-ink-muted ml-2">Voluntary</span>}
          </div>
          <div className="text-xs text-ink-muted">
            {action.payable_date ? `Payable ${action.payable_date}` : "No payable date set"}
          </div>
        </div>
        <span className={`chip ${STATUS_COLOR[action.status] || "bg-border-soft text-ink-muted"}`}>
          {titleCase(action.status)}
        </span>
        <span className="text-xs text-ink-muted whitespace-nowrap">
          {action.entitlements_posted}/{action.entitlement_count} posted
        </span>
      </div>
      {open && (
        <div className="pl-7 pb-3">
          {action.entitlement_count === 0 && (
            <button className="btn-outline text-xs" disabled={computing} onClick={compute} data-testid="ca-compute-entitlements">
              {computing ? "Computing…" : "Compute entitlements"}
            </button>
          )}
          {loading && <Spinner />}
          {entitlements?.items?.map((e: any) => (
            <EntitlementRow key={e.id} entitlement={e} onPosted={() => { refetch(); onChanged(); }} />
          ))}
        </div>
      )}
    </div>
  );
}

export default function CorporateActions() {
  const [status, setStatus] = useState<string>("");
  const { data, loading, refetch } = useApi<any>(
    `/api/corporate-actions${status ? `?status=${status}` : ""}`, [status]
  );

  const openCount = data?.items?.filter((a: any) => a.status !== "posted" && a.status !== "verified").length || 0;

  return (
    <div>
      <PageHeader title="Corporate actions"
        sub="Capture, entitlement, election, and posting — the operations track L100 never named (L200-4 §5)." />

      <div className="grid grid-cols-2 gap-4 mb-6">
        <StatTile label="Total actions" value={data?.items?.length || 0} />
        <StatTile label="Awaiting posting" value={openCount} accent={openCount ? "warn" : "ok"} />
      </div>

      <Card>
        <div className="flex items-center justify-between mb-3">
          <div className="font-semibold text-ink flex items-center gap-2"><CalendarClock size={17} /> Queue</div>
          <select className="input w-40 text-xs" value={status} onChange={(e) => setStatus(e.target.value)}>
            <option value="">All statuses</option>
            {STATUSES.map((s) => <option key={s} value={s}>{titleCase(s)}</option>)}
          </select>
        </div>
        {loading && <Spinner />}
        {!loading && !data?.items?.length && <Empty>No corporate actions{status ? ` with status "${status}"` : ""}.</Empty>}
        {!loading && data?.items?.map((a: any) => (
          <ActionRow key={a.id} action={a} onChanged={refetch} />
        ))}
      </Card>
    </div>
  );
}
