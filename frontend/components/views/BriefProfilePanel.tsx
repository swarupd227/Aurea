"use client";

import { useState } from "react";
import { SlidersHorizontal } from "lucide-react";
import { useApi } from "@/lib/hooks";
import { api, getUser } from "@/lib/api";
import { useViewNav } from "./ViewNav";
import { BriefProfileCard, type BriefProfilePayload } from "@/app/workspace/components/artifacts/BriefProfileCard";

/** The RM's own brief profile beside their meetings (B-203). In the work pane a change is the conversation's
 * set_brief_profile behind its Confirm card; on the page the form sets it directly. Hidden for a role without one. */
export function BriefProfilePanel() {
  const role = getUser()?.role || "";
  return ["adviser", "paraplanner", "portfolio_team", "branch_leader"].includes(role) ? <Panel /> : null;
}

function Panel() {
  const nav = useViewNav();
  const { data, error, refetch } = useApi<BriefProfilePayload>("/api/briefs/profile");
  const [editing, setEditing] = useState(false);
  const [lead, setLead] = useState<string[]>([]);
  const [length, setLength] = useState("standard");
  const [strengths, setStrengths] = useState("");
  const [busy, setBusy] = useState(false);
  const [failed, setFailed] = useState<string | null>(null);
  if (error || !data) return null;
  const sections = data.sections || {};
  const label = (k: string) => Object.values(sections).flat().find((s) => s.key === k)?.label || k;

  function start() {
    setLead(data!.lead); setLength(data!.length); setStrengths(data!.strengths.join(", ")); setFailed(null); setEditing(true);
  }
  function flip(k: string) {
    setLead((l) => (l.includes(k) ? l.filter((x) => x !== k) : [...l, k]));
  }
  async function save() {
    const list = strengths.split(",").map((s) => s.trim()).filter(Boolean);
    if (nav.say) {
      nav.say(`Set my brief profile: lead with ${lead.map(label).join(", ") || "the default order"}; open ${length}`
        + (list.length ? `; my strengths are ${list.join(", ")}` : "; no strengths") + ".");
      return setEditing(false);
    }
    setBusy(true);
    setFailed(null);
    try {
      await api("/api/briefs/profile", { method: "PUT", body: { lead, length, strengths: list } });
      setEditing(false);
      refetch();
    } catch (e: any) {
      setFailed(e?.message || "Not saved.");
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="mb-5 space-y-2" data-testid="brief-profile-panel">
      <BriefProfileCard result={data} />
      {!editing ? (
        <button className="btn-ghost text-xs" onClick={start} data-testid="brief-profile-edit"><SlidersHorizontal size={12} /> Change how my briefs are laid out</button>
      ) : (
        <div className="space-y-2 rounded-lg border border-border bg-surface p-3">
          {Object.entries(sections).map(([kind, list]) => (
            <div key={kind} className="flex flex-wrap items-center gap-1.5">
              <span className="tile-label w-16">{kind}</span>
              {list.map((s) => {
                const at = lead.indexOf(s.key);
                return (
                  <button key={s.key} type="button" onClick={() => flip(s.key)} aria-pressed={at >= 0}
                    className={`chip tabular-nums ${at >= 0 ? "bg-border-soft text-ink" : "text-ink-muted"}`} data-testid="brief-profile-section">
                    {at >= 0 ? `${at + 1} · ` : ""}{s.label}
                  </button>
                );
              })}
            </div>
          ))}
          <div className="flex flex-wrap items-center gap-2">
            <select className="input w-32 text-xs" aria-label="How much opens at first" value={length} onChange={(e) => setLength(e.target.value)}>
              <option value="short">Short</option><option value="standard">Standard</option><option value="full">Full</option>
            </select>
            <input className="input w-72 text-xs" placeholder="Strengths, comma separated" aria-label="Strengths" value={strengths}
              onChange={(e) => setStrengths(e.target.value)} />
            <button className="btn-primary text-xs" disabled={busy} onClick={save} data-testid="brief-profile-save">{nav.say ? "Ask Astra" : busy ? "Saving…" : "Save"}</button>
            <button className="btn-ghost text-xs" onClick={() => setEditing(false)}>Cancel</button>
            {failed && <span className="text-xs text-crit">{failed}</span>}
          </div>
        </div>
      )}
    </div>
  );
}
