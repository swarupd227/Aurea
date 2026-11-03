"use client";

import { useEffect, useState, type FormEvent } from "react";
import { useRouter } from "next/navigation";
import { CheckCircle2 } from "lucide-react";
import { Card, ErrorState, SkeletonList } from "@/components/ui";
import { api, getUser } from "@/lib/api";
import { useApi } from "@/lib/hooks";
import { withRef } from "@/lib/refs";
import { CanvasHouseholdPicker } from "./CanvasView";
import { ViewLink, useViewNav } from "./ViewNav";
import { AllClients, ViewHeader } from "./ViewHeader";

const ROUTE = "/canvas/questionnaire";
/** Staff who may record a client's answers for them (the backend's QUESTIONNAIRE_RECORDERS). */
const RECORDERS = new Set(["adviser", "paraplanner"]);

/** The risk and suitability questionnaire. A client answers their own; an adviser or paraplanner records a
 * client's answers for them (in the work pane, through the conversation's Confirm card); anyone else on
 * staff reads the questions only. No result is shown here: what it indicates goes to the adviser. */
export function CanvasQuestionnaireView({ household = null, subject = null }: { household?: string | null; subject?: string | null }) {
  const role: string = getUser()?.role ?? "";
  if (role === "client") return <Questionnaire householdId={null} initialSubject={subject} canRecord />;
  if (!household) return <div className="view-container"><CanvasHouseholdPicker title="Risk questionnaire" route={ROUTE} testId="questionnaire-households" /></div>;
  return <Questionnaire key={household} householdId={household} initialSubject={subject} canRecord={RECORDERS.has(role)} />;
}

function Questionnaire({ householdId, initialSubject, canRecord }: { householdId: string | null; initialSubject: string | null; canRecord: boolean }) {
  const client = householdId === null;
  const nav = useViewNav();
  const router = useRouter();
  // The household's name, for the title and for the sentence handed to the conversation.
  const households = useApi<any[]>(client ? null : "/api/core/households");
  const householdName: string = households.data?.find((h: any) => h.id === householdId)?.name ?? "";

  const [schema, setSchema] = useState<any[]>([]);
  const [answers, setAnswers] = useState<Record<string, string>>({});
  const [loading, setLoading] = useState(true);
  const [loadError, setLoadError] = useState<string | null>(null);
  const [saving, setSaving] = useState(false);
  const [saveError, setSaveError] = useState<string | null>(null);
  const [saved, setSaved] = useState(false);
  const [handed, setHanded] = useState(false);
  const [subjects, setSubjects] = useState<any[]>([]);
  const [subject, setSubject] = useState<string | null>(initialSubject);

  const draftKey = `aurea_questionnaire_draft:${householdId || "self"}:${subject || "household"}`;
  // In the work pane, staff hand the answers to the conversation, behind its Confirm card.
  const viaConversation = !client && nav.inPane && !!nav.say;

  async function load() {
    setLoading(true);
    setLoadError(null);
    try {
      const query = new URLSearchParams();
      if (householdId) query.set("household_id", householdId);
      if (subject) query.set("subject_id", subject);
      const qs = query.toString();
      const data = await api<any>(`/api/canvas/questionnaire${qs ? `?${qs}` : ""}`);
      setSchema(data.schema || []);
      setSubjects(data.subjects || []);
      // Restore an in-progress draft if the page was left or refreshed.
      let restored: Record<string, string> | null = null;
      try {
        const raw = sessionStorage.getItem(draftKey);
        if (raw) restored = JSON.parse(raw);
      } catch {}
      setAnswers(restored || data.answers || {});
    } catch (e: any) {
      setLoadError(e?.message || "Failed to load");
      setSchema([]);
    } finally {
      setLoading(false);
    }
  }

  useEffect(() => {
    load();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [householdId, subject]);

  function choose(key: string, opt: string) {
    if (!canRecord) return;
    setHanded(false);
    setAnswers((a) => {
      const next = { ...a, [key]: opt };
      try { sessionStorage.setItem(draftKey, JSON.stringify(next)); } catch {}
      return next;
    });
  }

  const answered = schema.filter((q) => answers[q.key]).length;
  // Guard on schema.length too: with no questions loaded, 0 of 0 must not count as complete.
  const canSubmit = canRecord && schema.length > 0 && answered === schema.length;
  const chosen = subjects.find((s: any) => s.id === subject) ?? null;

  async function submit(e: FormEvent) {
    e.preventDefault();
    if (!canSubmit) return;
    if (viaConversation && householdId) {
      const list = schema.map((q) => `${q.key}: ${answers[q.key]}`).join("; ");
      const whom = chosen ? `, answered for ${chosen.name}` : "";
      const sentence = withRef(`Record the risk questionnaire for ${householdName || "this household"} with these answers: ${list}${whom}`, "household", householdId);
      nav.say!(chosen ? withRef(sentence, chosen.kind === "entity" ? "entity" : "person", chosen.id) : sentence);
      setHanded(true);
      return;
    }
    setSaving(true);
    setSaveError(null);
    try {
      const body: any = { answers };
      if (householdId) body.household_id = householdId;
      if (subject) body.subject_id = subject;
      await api("/api/canvas/questionnaire", { body });
      try { sessionStorage.removeItem(draftKey); } catch {}
      setSaved(true);
      if (!nav.inPane) setTimeout(() => router.back(), 2000);
    } catch (e: any) {
      setSaveError(e?.message || "Could not save the answers");
    } finally {
      setSaving(false);
    }
  }

  const title = client ? "Risk questionnaire" : `Risk questionnaire · ${householdName}`;
  const back = client ? "/canvas" : `/canvas?household=${householdId}`;

  return (
    <div className="view-container" data-testid="canvas-questionnaire-view">
      <ViewHeader
        title={householdName || client ? title : "Risk questionnaire"}
        meta={<>
          {schema.length > 0 && <span className="chip text-ink-muted" data-testid="questionnaire-progress-chip">{answered} of {schema.length} answered</span>}
          {!client && !canRecord && <span className="chip text-ink-muted" data-testid="questionnaire-read-only">read only · an adviser or paraplanner records</span>}
          {viaConversation && canRecord && <span className="chip text-ink-muted">recorded through the conversation</span>}
        </>}
        actions={client ? undefined : <AllClients href={ROUTE} />}
      />

      <Card className="mx-auto max-w-2xl">
        {subjects.length > 1 && !saved && (
          <div className="mb-4 flex flex-wrap gap-1.5" data-testid="questionnaire-subject">
            {[{ id: null, name: "Everyone" }, ...subjects].map((s: any) => (
              <button key={s.id ?? "household"} type="button" onClick={() => { setSubject(s.id); setHanded(false); }} aria-pressed={subject === s.id}
                className={`chip border ${subject === s.id ? "border-ink-muted bg-border-soft text-ink" : "border-border text-ink-muted hover:bg-border-soft"}`}>{s.name}</button>
            ))}
          </div>
        )}

        {loading ? <SkeletonList count={3} /> : saved ? (
          <div className="flex flex-col items-center gap-3 py-8" data-testid="questionnaire-saved">
            <CheckCircle2 size={36} className="text-ok" aria-hidden />
            <span className="chip bg-ok/10 text-ok">saved</span>
            {nav.inPane && <ViewLink href={back} className="btn-outline text-sm">Back to the canvas</ViewLink>}
          </div>
        ) : loadError || !schema.length ? (
          // Never an empty form with a live Save: that would "complete" a questionnaire that captured nothing.
          <ErrorState what="the questions" message={loadError || "No questions are available."} onRetry={load} />
        ) : (
          <form onSubmit={submit} className="space-y-6">
            <div
              className="h-1.5 overflow-hidden rounded-full bg-border-soft"
              role="progressbar"
              aria-valuenow={answered}
              aria-valuemin={0}
              aria-valuemax={schema.length}
              aria-label="Questionnaire progress"
            >
              <div className="h-full rounded-full bg-ink-muted transition-all duration-300" style={{ width: `${(answered / schema.length) * 100}%` }} />
            </div>

            {schema.map((q, i) => (
              <fieldset key={q.key} className="m-0 border-0 p-0" disabled={!canRecord}>
                <legend className="mb-2 text-sm font-medium text-ink">
                  <span className="font-mono text-ink-muted">{i + 1}.</span> {q.question}
                </legend>
                <div className="space-y-2">
                  {q.options.map((opt: string) => {
                    const on = answers[q.key] === opt;
                    return (
                      <label key={opt}
                        className={`flex items-center gap-3 rounded-xl border p-3 transition ${canRecord ? "cursor-pointer" : "cursor-default"} ${on ? "border-ink-muted bg-border-soft" : "border-border hover:bg-border-soft"}`}>
                        <input type="radio" name={q.key} value={opt} checked={on} onChange={() => choose(q.key, opt)} disabled={!canRecord} />
                        <span className="text-sm text-ink">{opt}</span>
                      </label>
                    );
                  })}
                </div>
              </fieldset>
            ))}

            {saveError && <div role="alert" className="rounded-lg border border-crit/35 bg-crit-bg px-3 py-2 text-sm text-ink">{saveError}</div>}
            {canRecord && (
              <div className="flex flex-wrap items-center gap-2">
                <button type="submit" className="btn-primary" disabled={saving || !canSubmit || handed} data-testid="questionnaire-submit">
                  {saving ? "Saving…" : viaConversation ? "Record answers" : "Save answers"}
                </button>
                {!canSubmit && <span className="chip text-ink-muted">{schema.length - answered} unanswered</span>}
                {handed && <span className="chip text-ink-muted" data-testid="questionnaire-handed">sent to the conversation</span>}
              </div>
            )}
          </form>
        )}
      </Card>
    </div>
  );
}
