"use client";
import { Suspense, useEffect, useState } from "react";
import { useSearchParams, useRouter } from "next/navigation";
import { CheckCircle2, ChevronLeft } from "lucide-react";
import { api } from "@/lib/api";
import { Spinner } from "@/components/ui";

function QuestionnaireContent() {
  const params = useSearchParams();
  const router = useRouter();
  const hid = params.get("household_id");

  const [schema, setSchema] = useState<any[]>([]);
  const [answers, setAnswers] = useState<Record<string, string>>({});
  const [loading, setLoading] = useState(true);
  const [loadError, setLoadError] = useState<string | null>(null);
  const [saving, setSaving] = useState(false);
  const [saveError, setSaveError] = useState<string | null>(null);
  const [saved, setSaved] = useState(false);

  const draftKey = `aurea_questionnaire_draft:${hid || "self"}`;

  async function load() {
    setLoading(true);
    setLoadError(null);
    try {
      const path = hid ? `/api/canvas/questionnaire?household_id=${hid}` : "/api/canvas/questionnaire";
      const data = await api<any>(path);
      setSchema(data.schema || []);
      // Restore an in-progress draft if the client navigated away or refreshed.
      let restored: Record<string, string> | null = null;
      try {
        const raw = sessionStorage.getItem(draftKey);
        if (raw) restored = JSON.parse(raw);
      } catch {}
      setAnswers(restored || data.answers || {});
    } catch (e: any) {
      setLoadError(e?.message || "We couldn't load your questions.");
      setSchema([]);
    } finally {
      setLoading(false);
    }
  }

  useEffect(() => {
    load();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [hid]);

  function choose(key: string, opt: string) {
    setAnswers((a) => {
      const next = { ...a, [key]: opt };
      try { sessionStorage.setItem(draftKey, JSON.stringify(next)); } catch {}
      return next;
    });
  }

  const answered = schema.filter((q) => answers[q.key]).length;
  // Guard on schema.length too: if the questions never loaded, `answered < schema.length`
  // is 0 < 0 — false — which would leave Save enabled and let the client "complete" a
  // questionnaire that captured nothing.
  const canSubmit = schema.length > 0 && answered === schema.length;

  async function submit(e: React.FormEvent) {
    e.preventDefault();
    if (!canSubmit) return;
    setSaving(true);
    setSaveError(null);
    try {
      const body: any = { answers };
      if (hid) body.household_id = hid;
      await api("/api/canvas/questionnaire", { body });
      try { sessionStorage.removeItem(draftKey); } catch {}
      setSaved(true);
      setTimeout(() => router.back(), 2000);
    } catch (e: any) {
      setSaveError(e?.message || "We couldn't save your answers. Please try again.");
    } finally {
      setSaving(false);
    }
  }

  if (loading) return <Spinner label="Loading questionnaire…" />;

  return (
    <div className="max-w-xl mx-auto">
      <button onClick={() => router.back()} className="flex items-center gap-1 text-sm text-ink-muted hover:text-ink mb-4">
        <ChevronLeft size={15} /> Back
      </button>
      <div className="card p-6">
        <h1 className="font-serif text-2xl text-ink mb-1">Risk & suitability questionnaire</h1>
        <p className="text-sm text-ink-muted mb-6">
          Your answers help your adviser ensure your investments match your goals and comfort level.
          All responses are saved securely and reviewed by your adviser.
        </p>

        {saved ? (
          <div className="flex flex-col items-center gap-3 py-8">
            <CheckCircle2 size={40} className="text-positive" />
            <p className="font-medium text-ink">Questionnaire saved — returning to your wealth view.</p>
          </div>
        ) : loadError || !schema.length ? (
          /* Never show an empty form with a live Save button — that lets someone
             "complete" a questionnaire that captured nothing. */
          <div className="text-center py-8" role="alert">
            <p className="font-medium text-ink">We couldn&rsquo;t load your questions</p>
            <p className="text-sm text-ink-muted mt-1.5 max-w-sm mx-auto">
              {loadError || "No questions are available for you at the moment."} Nothing has been
              saved, so you can safely try again.
            </p>
            <button className="btn-outline text-sm mt-4" onClick={load}>Try again</button>
          </div>
        ) : (
          <form onSubmit={submit} className="space-y-6">
            <div>
              <div className="flex items-center justify-between text-xs text-ink-muted mb-1.5">
                <span>{answered} of {schema.length} answered</span>
                <span>{Math.round((answered / schema.length) * 100)}%</span>
              </div>
              <div
                className="h-1.5 rounded-full bg-navy-100 overflow-hidden"
                role="progressbar"
                aria-valuenow={answered}
                aria-valuemin={0}
                aria-valuemax={schema.length}
                aria-label="Questionnaire progress"
              >
                <div
                  className="h-full bg-navy-600 transition-all duration-300"
                  style={{ width: `${(answered / schema.length) * 100}%` }}
                />
              </div>
            </div>

            {schema.map((q, i) => (
              <fieldset key={q.key} className="border-0 p-0 m-0">
                <legend className="label mb-2">
                  <span className="text-ink-muted">{i + 1}.</span> {q.question}
                </legend>
                <div className="space-y-2">
                  {q.options.map((opt: string) => (
                    <label key={opt} className={`flex items-center gap-3 p-3 rounded-xl border cursor-pointer transition ${answers[q.key] === opt ? "border-navy-600 bg-navy-50" : "border-navy-100 hover:border-navy-300"}`}>
                      <input
                        type="radio"
                        name={q.key}
                        value={opt}
                        checked={answers[q.key] === opt}
                        onChange={() => choose(q.key, opt)}
                        className="accent-navy-700"
                      />
                      <span className="text-sm text-ink">{opt}</span>
                    </label>
                  ))}
                </div>
              </fieldset>
            ))}

            {saveError && (
              <p className="text-sm text-critical bg-critical/5 rounded-lg px-3 py-2" role="alert">
                {saveError}
              </p>
            )}
            <button type="submit" className="btn-primary w-full" disabled={saving || !canSubmit}>
              {saving ? "Saving…" : "Save answers"}
            </button>
            {!canSubmit && (
              <p className="text-xs text-ink-muted text-center">Please answer all questions to continue.</p>
            )}
          </form>
        )}
      </div>
    </div>
  );
}

export default function QuestionnairePage() {
  return (
    <Suspense fallback={<Spinner label="Loading…" />}>
      <QuestionnaireContent />
    </Suspense>
  );
}
