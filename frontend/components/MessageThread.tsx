"use client";
import { useEffect, useRef, useState } from "react";
import { Send, ShieldCheck, Sparkles } from "lucide-react";
import { api, getUser } from "@/lib/api";

export default function MessageThread({ householdId }: { householdId?: string }) {
  const [messages, setMessages] = useState<any[]>([]);
  const [text, setText] = useState("");
  const [busy, setBusy] = useState(false);
  const endRef = useRef<HTMLDivElement>(null);
  const role = typeof window !== "undefined" ? getUser()?.role : null;
  const isClient = role === "client";
  const q = householdId ? `?household_id=${householdId}` : "";

  async function load() {
    setMessages(await api(`/api/canvas/messages${q}`));
  }
  useEffect(() => {
    load();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [householdId]);
  useEffect(() => {
    endRef.current?.scrollIntoView({ behavior: "smooth" });
  }, [messages.length]);

  async function send() {
    if (!text.trim()) return;
    setBusy(true);
    try {
      await api(`/api/canvas/messages`, { body: { body: text, household_id: householdId } });
      setText("");
      await load();
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="flex flex-col h-full">
      <div className="flex-1 overflow-y-auto space-y-3 pr-1" style={{ maxHeight: 380 }}>
        {messages.length === 0 && (
          <div className="text-sm text-ink-muted text-center py-8">No messages yet. Start the conversation.</div>
        )}
        {messages.map((m) => {
          const mine = isClient ? m.author_role === "client" : m.author_role !== "client";
          return (
            <div key={m.id} className={`flex ${mine ? "justify-end" : "justify-start"}`}>
              <div className={`max-w-[80%] rounded-2xl px-4 py-2 text-sm ${mine ? "bg-navy-800 text-white rounded-br-sm" : "bg-navy-50 text-ink-soft rounded-bl-sm"}`}>
                {!mine && <div className="text-[11px] font-medium opacity-70 mb-0.5 flex items-center gap-1">
                  {m.from_agent && <Sparkles size={10} />}{m.author_name}
                </div>}
                {m.body}
              </div>
            </div>
          );
        })}
        <div ref={endRef} />
      </div>
      <form onSubmit={(e) => { e.preventDefault(); send(); }} className="flex gap-2 mt-3 pt-3 border-t border-navy-100">
        <input className="input" placeholder="Write a secure message…" value={text} onChange={(e) => setText(e.target.value)} />
        <button className="btn-primary" disabled={busy}><Send size={16} /></button>
      </form>
      <div className="flex items-center gap-1.5 text-xs text-ink-muted mt-2">
        <ShieldCheck size={12} className="text-positive" /> Encrypted, adviser-branded. Stays in your firm.
      </div>
    </div>
  );
}
