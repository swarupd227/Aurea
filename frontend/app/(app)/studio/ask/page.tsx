"use client";
import { useState } from "react";
import { Send, Sparkles, ShieldCheck } from "lucide-react";
import PageHeader from "@/components/PageHeader";
import { api } from "@/lib/api";

const SUGGESTIONS = [
  "Which clients are overweight equities with a loss to harvest?",
  "Who has a values exclusion that isn't yet implemented?",
  "Which households have a next-gen heir to engage?",
];

export default function Ask() {
  const [q, setQ] = useState("");
  const [thread, setThread] = useState<any[]>([]);
  const [busy, setBusy] = useState(false);

  async function ask(question: string) {
    if (!question.trim()) return;
    setBusy(true);
    setThread((t) => [...t, { role: "user", text: question }]);
    setQ("");
    try {
      const res = await api("/api/studio/ask", { body: { question } });
      setThread((t) => [...t, { role: "assistant", ...res }]);
    } catch (e: any) {
      setThread((t) => [...t, { role: "assistant", answer: e.message, error: true }]);
    } finally {
      setBusy(false);
    }
  }

  return (
    <div>
      <PageHeader
        title="Ask your book"
        sub="Ask questions of your book in plain language. Answers cite the underlying data."
      />

      <div className="card p-5 min-h-[50vh] flex flex-col">
        <div className="flex-1 space-y-4">
          {thread.length === 0 && (
            <div className="text-center py-10">
              <Sparkles className="mx-auto text-gold mb-2" />
              <p className="text-ink-muted text-sm">Ask anything about your book.</p>
              <div className="flex flex-col items-center gap-2 mt-4">
                {SUGGESTIONS.map((s) => (
                  <button key={s} onClick={() => ask(s)} className="text-sm text-navy-700 hover:underline">
                    “{s}”
                  </button>
                ))}
              </div>
            </div>
          )}
          {thread.map((m, i) =>
            m.role === "user" ? (
              <div key={i} className="flex justify-end">
                <div className="bg-navy-800 text-white rounded-2xl rounded-br-sm px-4 py-2 max-w-[80%] text-sm">{m.text}</div>
              </div>
            ) : (
              <div key={i} className="flex justify-start">
                <div className="bg-navy-50 rounded-2xl rounded-bl-sm px-4 py-3 max-w-[85%]">
                  <p className={`text-sm whitespace-pre-line ${m.error ? "text-critical" : "text-ink-soft"}`}>{m.answer}</p>
                  {!m.error && (
                    <div className="flex items-center gap-2 mt-2 text-xs text-ink-muted">
                      <ShieldCheck size={13} className="text-positive" />
                      Governed answer{m.is_fallback ? " · deterministic (no LLM key)" : " · LLM-grounded"}
                      {m.grounded_on?.length ? ` · ${m.grounded_on.length} households` : ""}
                    </div>
                  )}
                </div>
              </div>
            )
          )}
          {busy && <div className="text-sm text-ink-muted">Thinking…</div>}
        </div>

        <form
          onSubmit={(e) => {
            e.preventDefault();
            ask(q);
          }}
          className="flex gap-2 mt-4 pt-4 border-t border-navy-100"
        >
          <input className="input" placeholder="e.g. which clients are overweight equities?" value={q} onChange={(e) => setQ(e.target.value)} />
          <button className="btn-primary" disabled={busy}>
            <Send size={16} />
          </button>
        </form>
      </div>
    </div>
  );
}
