"use client";
import { useEffect, useRef, useState } from "react";
import { Send, Sparkles } from "lucide-react";
import { api, getUser } from "@/lib/api";

/** A thread and its composer. `onSend` resolves once the words are on their way; it throws to keep
 * them in the box and say why. */
export function MessageTimeline({ messages, onSend }: { messages: any[]; onSend: (text: string) => Promise<void> }) {
  const [text, setText] = useState("");
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState<string | null>(null);
  const endRef = useRef<HTMLDivElement>(null);
  const role = typeof window !== "undefined" ? getUser()?.role : null;
  const isClient = role === "client";

  useEffect(() => {
    endRef.current?.scrollIntoView({ behavior: "smooth", block: "nearest" });
  }, [messages.length]);

  async function send() {
    if (!text.trim()) return;
    setBusy(true);
    setErr(null);
    try {
      await onSend(text.trim());
      setText("");
    } catch (e: any) {
      setErr(e.message || "The message did not send.");
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="flex h-full flex-col" data-testid="message-thread">
      <div className="max-h-[380px] flex-1 space-y-3 overflow-y-auto pr-1">
        {messages.length === 0 && <div className="py-8 text-center text-sm text-ink-muted">No messages</div>}
        {messages.map((m) => {
          const mine = isClient ? m.author_role === "client" : m.author_role !== "client";
          return (
            <div key={m.id} data-testid="message" className={`flex ${mine ? "justify-end" : "justify-start"}`}>
              <div className={`max-w-[80%] whitespace-pre-line break-words rounded-lg border px-3 py-2 text-sm ${mine ? "border-border bg-border-soft text-ink" : "border-border-soft bg-surface text-ink-soft"}`}>
                {/* Staff share a thread, so each sees who wrote what; a client knows their own words. */}
                {(!mine || !isClient) && (
                  <div className="mb-0.5 flex items-center gap-1 text-[11px] font-medium text-ink-muted">
                    {m.from_agent && <Sparkles size={10} aria-hidden />}{m.author_name}
                  </div>
                )}
                {m.body}
              </div>
            </div>
          );
        })}
        <div ref={endRef} />
      </div>
      <form onSubmit={(e) => { e.preventDefault(); send(); }} className="mt-3 flex gap-2 border-t border-border-soft pt-3">
        <input className="input" aria-label="Message" data-testid="message-input" placeholder="Write a message" value={text} onChange={(e) => setText(e.target.value)} />
        <button className="btn-primary" data-testid="message-send" aria-label="Send" disabled={busy || !text.trim()}><Send size={16} aria-hidden /></button>
      </form>
      {err && <div className="mt-2 text-xs text-crit">{err}</div>}
    </div>
  );
}

/** The client's own thread in Canvas, and a colleague's preview of it. */
export default function MessageThread({ householdId }: { householdId?: string }) {
  const [messages, setMessages] = useState<any[]>([]);
  const q = householdId ? `?household_id=${householdId}` : "";

  async function load() {
    setMessages(await api(`/api/canvas/messages${q}`));
  }
  useEffect(() => {
    load();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [householdId]);

  return (
    <MessageTimeline messages={messages} onSend={async (text) => {
      await api(`/api/canvas/messages`, { body: { body: text, household_id: householdId } });
      await load();
    }} />
  );
}
