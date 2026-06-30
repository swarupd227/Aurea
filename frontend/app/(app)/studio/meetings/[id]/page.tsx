"use client";
import { useEffect, useState } from "react";
import { useParams } from "next/navigation";
import Link from "next/link";
import { Play, FileText, Mic, CheckSquare, Target, Sparkles } from "lucide-react";
import PageHeader from "@/components/PageHeader";
import { Breadcrumb, Card, Spinner, StatusBadge } from "@/components/ui";
import RecommendationCard from "@/components/RecommendationCard";
import { useApi } from "@/lib/hooks";
import { api } from "@/lib/api";
import { money, pct, titleCase } from "@/lib/format";

export default function MeetingDetail() {
  const { id } = useParams<{ id: string }>();
  const { data: m, loading, refetch } = useApi<any>(`/api/engage/meetings/${id}`, [id]);
  const [transcript, setTranscript] = useState("");
  const [busy, setBusy] = useState<string | null>(null);
  const [companionRec, setCompanionRec] = useState<any>(null);

  useEffect(() => { if (m) setTranscript(m.transcript || ""); }, [m?.id]);
  useEffect(() => {
    if (m?.companion_recommendation_id) {
      api(`/api/studio/recommendations/${m.companion_recommendation_id}`).then(setCompanionRec).catch(() => {});
    }
  }, [m?.companion_recommendation_id, m?.status]);

  if (loading || !m) return <Spinner />;
  const brief = m.brief || {};
  const notes = m.notes || {};

  async function runPrep() { setBusy("prep"); try { await api(`/api/engage/meetings/${id}/prep`, { body: {} }); await refetch(); } finally { setBusy(null); } }
  async function saveTranscript() { setBusy("save"); try { await api(`/api/engage/meetings/${id}/transcript`, { body: { transcript } }); await refetch(); } finally { setBusy(null); } }
  async function runCompanion() { setBusy("comp"); try { await api(`/api/engage/meetings/${id}/transcript`, { body: { transcript } }); await api(`/api/engage/meetings/${id}/companion`, { body: {} }); await refetch(); } finally { setBusy(null); } }

  return (
    <div>
      <Breadcrumb items={[{ label: "Meetings", href: "/studio/meetings" }, { label: m.title }]} />
      <PageHeader
        title={m.title}
        sub={`${m.household_name}${m.scheduled_at ? ` · ${new Date(m.scheduled_at).toLocaleString()}` : ""}`}
        actions={<StatusBadge status={m.status} />}
      />

      <div className="grid lg:grid-cols-2 gap-5">
        {/* Prep brief */}
        <Card>
          <div className="flex items-center justify-between mb-3">
            <div className="font-semibold text-ink flex items-center gap-2"><Sparkles size={17} className="text-gold" /> Prepared brief</div>
            <button className="btn-outline text-xs" disabled={busy === "prep"} onClick={runPrep}><Play size={13} /> {busy === "prep" ? "Preparing…" : brief.agenda ? "Refresh" : "Run prep"}</button>
          </div>
          {brief.agenda ? (
            <div className="space-y-4 text-sm">
              <div className="grid grid-cols-2 gap-3">
                <div className="rounded-lg bg-navy-50 p-2.5"><div className="tile-label">Portfolio</div><div className="font-semibold text-ink">{money(brief.portfolio?.total_value)}</div></div>
                <div className="rounded-lg bg-navy-50 p-2.5"><div className="tile-label">Goals on track</div><div className="font-semibold text-ink">{(brief.goals || []).filter((g: any) => g.on_track).length}/{(brief.goals || []).length}</div></div>
              </div>
              <div>
                <div className="tile-label mb-1">Suggested agenda</div>
                <ul className="space-y-1">{brief.agenda.map((a: string, i: number) => <li key={i} className="text-ink-soft flex gap-2"><span className="text-gold">•</span> {a}</li>)}</ul>
              </div>
              {brief.watch_items?.length > 0 && (
                <div><div className="tile-label mb-1">Watch items</div>
                  <ul className="space-y-1">{brief.watch_items.map((w: any, i: number) => <li key={i} className="text-ink-soft text-xs">⚠ {w.title}</li>)}</ul>
                </div>
              )}
              {brief.house_views?.length > 0 && (
                <div><div className="tile-label mb-1 flex items-center gap-1"><FileText size={12} /> House views</div>
                  {brief.house_views.map((h: any, i: number) => <div key={i} className="text-xs text-ink-soft"><span className="font-medium text-ink">{h.title}</span></div>)}
                </div>
              )}
            </div>
          ) : (
            <div className="text-sm text-ink-muted">Run prep to assemble a brief from the client brain.</div>
          )}
        </Card>

        {/* Companion */}
        <Card>
          <div className="font-semibold text-ink flex items-center gap-2 mb-3"><Mic size={17} className="text-navy-600" /> Meeting companion</div>
          <label className="label">Transcript</label>
          <textarea className="input text-sm font-mono" rows={6} value={transcript} onChange={(e) => setTranscript(e.target.value)} placeholder="Paste or dictate the meeting transcript…" />
          <div className="flex gap-2 mt-2">
            <button className="btn-ghost text-xs" disabled={busy === "save"} onClick={saveTranscript}>Save</button>
            <button className="btn-primary text-xs ml-auto" disabled={busy === "comp" || !transcript.trim()} onClick={runCompanion}><Play size={13} /> {busy === "comp" ? "Capturing…" : "Capture notes & follow-ups"}</button>
          </div>

          {notes.summary?.length > 0 && (
            <div className="mt-4 space-y-3 text-sm border-t border-navy-100 pt-3">
              <div className="flex items-center gap-2"><span className="tile-label">Sentiment</span><span className={`chip ${notes.sentiment === "cautious" ? "bg-caution/10 text-caution" : notes.sentiment === "positive" ? "bg-positive/10 text-positive" : "bg-navy-50 text-ink-muted"}`}>{titleCase(notes.sentiment)}</span></div>
              {notes.action_items?.length > 0 && (
                <div><div className="tile-label mb-1 flex items-center gap-1"><CheckSquare size={12} /> Follow-up tasks</div>
                  <ul className="space-y-1">{notes.action_items.map((a: string, i: number) => <li key={i} className="text-ink-soft text-xs flex gap-2"><span className="text-navy-500">→</span> {a}</li>)}</ul>
                </div>
              )}
              {notes.proposed_goals?.length > 0 && (
                <div><div className="tile-label mb-1 flex items-center gap-1"><Target size={12} /> Proposed goals</div>
                  {notes.proposed_goals.map((g: any, i: number) => <div key={i} className="text-xs text-ink-soft">{g.name} · {money(g.target_amount)}</div>)}
                </div>
              )}
            </div>
          )}
        </Card>
      </div>

      {/* Decision on companion output */}
      {companionRec && companionRec.status === "proposed" && (
        <div className="mt-5">
          <div className="text-sm font-semibold text-ink mb-2">Approve notes & create tasks/goals</div>
          <RecommendationCard rec={companionRec} defaultOpen onDecided={(u) => { setCompanionRec(u); refetch(); }} />
        </div>
      )}
      {m.status === "completed" && (
        <div className="card p-4 mt-5 border-positive/30 text-sm flex items-center gap-2">
          <CheckSquare className="text-positive" size={18} /> Notes approved · tasks and goals added to the plan.
          <Link href="/studio/tasks" className="text-navy-700 underline ml-1">View tasks</Link>
        </div>
      )}
    </div>
  );
}
