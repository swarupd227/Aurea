"use client";

import { useState } from "react";
import { Network, Plus } from "lucide-react";
import { Card, Empty, ErrorState, SkeletonCard } from "@/components/ui";
import { useApi } from "@/lib/hooks";
import { api, getUser } from "@/lib/api";
import { withRef } from "@/lib/refs";
import { useViewNav } from "./ViewNav";
import { IntroductionsCard, IntroPathRow, type IntroPath } from "@/app/workspace/components/artifacts/IntroductionsCard";

interface Prospect { contact_id: string; name: string; stage: string; best: IntroPath | null; in_touch: { user: { name: string } }[] }

const KINDS: [string, string][] = [["person", "Client"], ["contact", "Contact"], ["user", "Staff"]];

/** Who knows them: one end is the prospect; the other is named exactly, with where it comes from. */
function RecordLink({ prospect, onRecorded }: { prospect: Prospect; onRecorded: () => void }) {
  const nav = useViewNav();
  const [open, setOpen] = useState(false);
  const [type, setType] = useState("person");
  const [name, setName] = useState("");
  const [closeness, setCloseness] = useState("");
  const [evidence, setEvidence] = useState("");
  const [busy, setBusy] = useState(false);
  const [failed, setFailed] = useState<string | null>(null);
  if (!open) {
    return <button className="btn-ghost text-xs" onClick={() => setOpen(true)} data-testid="ways-in-record"><Plus size={12} /> Who knows them</button>;
  }
  async function submit() {
    if (!name.trim() || evidence.trim().length < 3) return;
    if (nav.say) {
      nav.say(withRef(`Record that ${name.trim()} knows ${prospect.name}${closeness ? ` (${closeness})` : ""}. Evidence: ${evidence.trim()}`,
        "contact", prospect.contact_id));
      return setOpen(false);
    }
    setBusy(true);
    setFailed(null);
    try {
      await api("/api/introductions/links", { body: { from_type: type, from_name: name.trim(), to_type: "contact", to_id: prospect.contact_id,
        kind: "knows", closeness: closeness || undefined, evidence: evidence.trim() } });
      setOpen(false); setName(""); setEvidence(""); setCloseness("");
      onRecorded();
    } catch (e: any) {
      setFailed(e?.message || "Not recorded.");
    } finally {
      setBusy(false);
    }
  }
  return (
    <div className="mt-1 flex flex-wrap items-center gap-1.5">
      <select className="input w-24 text-xs" aria-label="Who they are" value={type} onChange={(e) => setType(e.target.value)}>
        {KINDS.map(([v, l]) => <option key={v} value={v}>{l}</option>)}
      </select>
      <input className="input w-36 text-xs" placeholder="Exact name" aria-label="Their exact name" value={name} onChange={(e) => setName(e.target.value)} />
      <select className="input w-28 text-xs" aria-label="How close, if someone said" value={closeness} onChange={(e) => setCloseness(e.target.value)}>
        <option value="">Not said</option><option value="close">Close</option><option value="known">Known</option><option value="met">Met</option>
      </select>
      <input className="input w-56 text-xs" placeholder="Where this comes from" aria-label="Evidence" value={evidence} onChange={(e) => setEvidence(e.target.value)} />
      <button className="btn-primary text-xs" disabled={busy || !name.trim() || evidence.trim().length < 3} onClick={submit}>{nav.say ? "Ask Astra" : "Record"}</button>
      <button className="btn-ghost text-xs" aria-label="Close" onClick={() => setOpen(false)}>×</button>
      {failed && <span className="text-xs text-crit">{failed}</span>}
    </div>
  );
}

function AllWays({ prospect }: { prospect: Prospect }) {
  const nav = useViewNav();
  const [found, setFound] = useState<any>(null);
  async function show() {
    if (nav.say) return nav.say(withRef(`Who could introduce me to ${prospect.name}?`, "contact", prospect.contact_id));
    setFound(found ? null : await api(`/api/introductions?contact_id=${prospect.contact_id}`));
  }
  return (
    <>
      <button className="btn-ghost text-xs" onClick={show} data-testid="ways-in-all"><Network size={12} /> All ways in</button>
      {found && <div className="mt-2 w-full"><IntroductionsCard result={found} /></div>}
    </>
  );
}

/** Each prospect not yet met and the best way in through people the firm already knows (B-200). */
export function WaysInPanel() {
  const role = getUser()?.role || "";
  return ["adviser", "paraplanner", "portfolio_team", "branch_leader"].includes(role) ? <WaysIn /> : null;
}

function WaysIn() {
  const { data, loading, error, refetch } = useApi<{ prospects: Prospect[]; scope: string }>("/api/introductions/prospects");
  if (error) return <Card><ErrorState message={error} onRetry={refetch} what="ways in" /></Card>;
  return (
    <Card>
      <div className="mb-3 flex items-center gap-2 font-semibold text-ink"><Network size={17} /> Ways in</div>
      {loading && <SkeletonCard rows={3} />}
      {data && (
        <div className="divide-y divide-border-soft" data-testid="ways-in-panel">
          {data.prospects.map((p) => (
            <div key={p.contact_id} className="py-2" data-testid="ways-in-prospect">
              <div className="flex flex-wrap items-center gap-1.5">
                <span className="text-sm text-ink">{p.name}</span>
                <span className="chip text-ink-muted">{p.stage}</span>
                {p.in_touch.length > 0 && <span className="chip text-ok">in touch: {p.in_touch[0].user.name}</span>}
                {!p.best && <span className="chip text-warn">no way in recorded</span>}
              </div>
              {p.best && <div className="-mx-3"><IntroPathRow path={p.best} /></div>}
              <div className="flex flex-wrap items-center gap-3">
                {p.best && <AllWays prospect={p} />}
                <RecordLink prospect={p} onRecorded={refetch} />
              </div>
            </div>
          ))}
          {!data.prospects.length && <Empty>No prospect waiting for a first meeting.</Empty>}
        </div>
      )}
    </Card>
  );
}
