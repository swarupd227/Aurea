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
  const [saving, setSaving] = useState(false);
  const [saved, setSaved] = useState(false);

  useEffect(() => {
    (async () => {
      try {
        const path = hid ? `/api/canvas/questionnaire?household_id=${hid}` : "/api/canvas/questionnaire";
        const data = await api<any>(path);
        setSchema(data.schema || []);
        setAnswers(data.answers || {});
      } catch {}
      setLoading(false);
    })();
  }, [hid]);

  async function submit(e: React.FormEvent) {
    e.preventDefault();
    setSaving(true);
    try {
      const body: any = { answers };
      if (hid) body.household_id = hid;
      await api("/api/canvas/questionnaire", { body });
      setSaved(true);
      setTimeout(() => router.back(), 2000);
    } catch {}
    setSaving(false);
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
        ) : (
          <form onSubmit={submit} className="space-y-6">
            {schema.map((q) => (
              <div key={q.key}>
                <label className="label mb-2">{q.question}</label>
                <div className="space-y-2">
                  {q.options.map((opt: string) => (
                    <label key={opt} className={`flex items-center gap-3 p-3 rounded-xl border cursor-pointer transition ${answers[q.key] === opt ? "border-navy-600 bg-navy-50" : "border-navy-100 hover:border-navy-300"}`}>
                      <input
                        type="radio"
                        name={q.key}
                        value={opt}
                        checked={answers[q.key] === opt}
                        onChange={() => setAnswers((a) => ({ ...a, [q.key]: opt }))}
                        className="accent-navy-700"
                      />
                      <span className="text-sm text-ink">{opt}</span>
                    </label>
                  ))}
                </div>
              </div>
            ))}
            <button
              type="submit"
              className="btn-primary w-full"
              disabled={saving || Object.keys(answers).length < schema.length}
            >
              {saving ? "Saving…" : "Save answers"}
            </button>
            {Object.keys(answers).length < schema.length && (
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
