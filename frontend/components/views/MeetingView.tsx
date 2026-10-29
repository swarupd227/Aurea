"use client";

import { useEffect, useState } from "react";
import { Play, FileText, Mic, CheckSquare, Target, Sparkles, AlertTriangle } from "lucide-react";
import { Breadcrumb, Card, ErrorState, Spinner, StatusBadge } from "@/components/ui";
import RecommendationCard from "@/components/RecommendationCard";
import { useApi } from "@/lib/hooks";
import { api } from "@/lib/api";
import { money, titleCase } from "@/lib/format";
import { withRef } from "@/lib/refs";
import { ViewLink, useViewNav } from "./ViewNav";
import { ViewHeader, chip } from "./ViewHeader";

/** One meeting: the prepared brief, the transcript, and the notes and follow-ups captured from it.
 * In the work pane, preparing the brief and capturing the notes are the conversation's run_agent
 * on this meeting, behind its Confirm card; the transcript is typed and saved here. */
export function MeetingView({ id }: { id: string }) {
  const nav = useViewNav();
  const { data: m, loading, error, refetch } = useApi<any>(`/api/engage/meetings/${id}`, [id]);
  const [transcript, setTranscript] = useState("");
  const [busy, setBusy] = useState<string | null>(null);
  const [companionRec, setCompanionRec] = useState<any>(null);

  useEffect(() => { if (m) setTranscript(m.transcript || ""); }, [m?.id]);
  useEffect(() => {
    if (m?.companion_recommendation_id) {
      api(`/api/studio/recommendations/${m.companion_recommendation_id}`).then(setCompanionRec).catch(() => {});
    }
  }, [m?.companion_recommendation_id, m?.status]);

  if (loading) return <Spinner />;
  if (error || !m) return <Card><ErrorState what="this meeting" message={error ?? "Not found"} onRetry={refetch} /></Card>;
  const brief = m.brief || {};
  const notes = m.notes || {};
  // A seeded title already names the client ("… — The Patel Household"); don't name them twice.
  const who = m.household_name && !m.title.includes(m.household_name) ? `${m.title} with ${m.household_name}` : m.title;

  async function runPrep() {
    if (nav.say) return nav.say(withRef(`Prepare the brief for ${who}.`, "meeting", id));
    setBusy("prep");
    try { await api(`/api/engage/meetings/${id}/prep`, { body: {} }); await refetch(); } finally { setBusy(null); }
  }
  async function saveTranscript() {
    setBusy("save");
    try { await api(`/api/engage/meetings/${id}/transcript`, { body: { transcript } }); await refetch(); } finally { setBusy(null); }
  }
  async function runCompanion() {
    setBusy("comp");
    try {
      await api(`/api/engage/meetings/${id}/transcript`, { body: { transcript } });
      if (nav.say) {
        nav.say(withRef(`Capture the notes and follow-ups from ${who}.`, "meeting", id));
        return;
      }
      await api(`/api/engage/meetings/${id}/companion`, { body: {} });
      await refetch();
    } finally { setBusy(null); }
  }

  return (
    <div className="view-container">
      {!nav.inPane && <Breadcrumb items={[{ label: "Meetings", href: "/studio/meetings" }, { label: m.title }]} />}
      <ViewHeader
        title={m.title}
        meta={
          <>
            {m.household_name && <span className={chip}>{m.household_name}</span>}
            {m.scheduled_at && <span className={`${chip} tabular-nums`}>{new Date(m.scheduled_at).toLocaleString()}</span>}
            <StatusBadge status={m.status} />
            {m.date_passed && <span className="chip bg-warn-bg text-warn" data-testid="meeting-date-passed">Date passed, not held</span>}
          </>
        }
      />

      <div className="grid gap-5 cq-md:grid-cols-2">
        <Card>
          <div className="mb-3 flex items-center justify-between gap-2">
            <div className="flex items-center gap-2 font-semibold text-ink"><Sparkles size={17} className="text-ink-muted" aria-hidden /> Prepared brief</div>
            <button className="btn-outline text-xs" data-testid="meeting-run-prep" disabled={busy === "prep"} onClick={runPrep}>
              <Play size={13} /> {busy === "prep" ? "Preparing…" : brief.agenda ? "Refresh" : "Run prep"}
            </button>
          </div>
          {brief.agenda ? (
            <div className="space-y-4 text-sm">
              <div className="grid grid-cols-2 gap-3">
                <div className="rounded-lg bg-border-soft p-2.5"><div className="tile-label">Portfolio</div><div className="font-semibold tabular-nums text-ink">{brief.portfolio?.total_value != null ? money(brief.portfolio.total_value) : "—"}</div></div>
                <div className="rounded-lg bg-border-soft p-2.5"><div className="tile-label">Goals on track</div><div className="font-semibold tabular-nums text-ink">{brief.goals?.length ? `${brief.goals.filter((g: any) => g.on_track).length}/${brief.goals.length}` : "—"}</div></div>
              </div>
              <div>
                <div className="tile-label mb-1">Suggested agenda</div>
                <ul className="space-y-1">{brief.agenda.map((a: string, i: number) => <li key={i} className="flex gap-2 text-ink-soft"><span className="text-ink-faint" aria-hidden>•</span> {a}</li>)}</ul>
              </div>
              {brief.watch_items?.length > 0 && (
                <div><div className="tile-label mb-1">Watch items</div>
                  <ul className="space-y-1">{brief.watch_items.map((w: any, i: number) => (
                    <li key={i} className="flex gap-1.5 text-xs text-ink-soft"><AlertTriangle size={12} className="mt-0.5 shrink-0 text-warn" aria-hidden /> {w.title}</li>
                  ))}</ul>
                </div>
              )}
              {brief.house_views?.length > 0 && (
                <div><div className="tile-label mb-1 flex items-center gap-1"><FileText size={12} aria-hidden /> House views</div>
                  {brief.house_views.map((h: any, i: number) => <div key={i} className="text-xs font-medium text-ink">{h.title}</div>)}
                </div>
              )}
            </div>
          ) : (
            <span className={chip}>No brief yet</span>
          )}
        </Card>

        <Card>
          <div className="mb-3 flex items-center gap-2 font-semibold text-ink"><Mic size={17} className="text-ink-muted" aria-hidden /> Meeting companion</div>
          <label className="label" htmlFor="meeting-transcript">Transcript</label>
          <textarea id="meeting-transcript" className="input font-mono text-sm" rows={6} value={transcript} onChange={(e) => setTranscript(e.target.value)} placeholder="Paste or dictate the meeting transcript…" />
          <div className="mt-2 flex gap-2">
            <button className="btn-ghost text-xs" data-testid="meeting-save-transcript" disabled={busy === "save"} onClick={saveTranscript}>Save</button>
            <button className="btn-primary ml-auto text-xs" data-testid="meeting-run-companion" disabled={busy === "comp" || !transcript.trim()} onClick={runCompanion}>
              <Play size={13} /> {busy === "comp" ? "Capturing…" : "Capture notes & follow-ups"}
            </button>
          </div>

          {notes.summary?.length > 0 && (
            <div className="mt-4 space-y-3 border-t border-border-soft pt-3 text-sm">
              <div className="flex items-center gap-2"><span className="tile-label">Sentiment</span><span className={`chip ${notes.sentiment === "cautious" ? "bg-warn-bg text-warn" : notes.sentiment === "positive" ? "bg-ok-bg text-ok" : "bg-border-soft text-ink-muted"}`}>{titleCase(notes.sentiment)}</span></div>
              {notes.action_items?.length > 0 && (
                <div><div className="tile-label mb-1 flex items-center gap-1"><CheckSquare size={12} aria-hidden /> Follow-up tasks</div>
                  <ul className="space-y-1">{notes.action_items.map((a: string, i: number) => <li key={i} className="flex gap-2 text-xs text-ink-soft"><span className="text-ink-faint" aria-hidden>→</span> {a}</li>)}</ul>
                </div>
              )}
              {notes.proposed_goals?.length > 0 && (
                <div><div className="tile-label mb-1 flex items-center gap-1"><Target size={12} aria-hidden /> Proposed goals</div>
                  {notes.proposed_goals.map((g: any, i: number) => <div key={i} className="text-xs text-ink-soft">{g.name} · <span className="tabular-nums">{money(g.target_amount)}</span></div>)}
                </div>
              )}
            </div>
          )}
        </Card>
      </div>

      {companionRec && companionRec.status === "proposed" && (
        <div className="mt-5">
          <div className="mb-2 text-sm font-semibold text-ink">Approve notes & create tasks/goals</div>
          <RecommendationCard rec={companionRec} defaultOpen onDecided={(u) => { setCompanionRec(u); refetch(); nav.changed(); }} />
        </div>
      )}
      {m.status === "completed" && (
        <div className="card mt-5 flex flex-wrap items-center gap-2 border-positive/30 p-4 text-sm">
          <CheckSquare className="text-positive" size={18} aria-hidden />
          <span className="chip bg-ok-bg text-ok">Notes approved</span>
          <span className="chip bg-ok-bg text-ok">Tasks and goals added</span>
          <ViewLink href="/studio/tasks" className="ml-1 text-ink underline">View tasks</ViewLink>
        </div>
      )}
    </div>
  );
}
