"use client";

import { useEffect, useRef, useState } from "react";
import { Square, Volume2 } from "lucide-react";
import { readable } from "@/lib/refs";
import { speak, speechSupported, stopSpeaking } from "@/lib/voice";

/** Read one of Astra's replies aloud. Shown only where the browser can speak. */
export function ListenButton({ text }: { text: string }) {
  const [supported, setSupported] = useState(false);
  const [speaking, setSpeaking] = useState(false);

  // Decided after mounting, so the server's render and the browser's agree.
  useEffect(() => setSupported(speechSupported()), []);
  // Leaving the conversation stops this reply's reading (and only this one's).
  const speakingRef = useRef(false);
  speakingRef.current = speaking;
  useEffect(() => () => {
    if (speakingRef.current) stopSpeaking();
  }, []);

  if (!supported || !text.trim()) return null;

  const toggle = () => {
    if (speaking) {
      stopSpeaking();
      setSpeaking(false);
      return;
    }
    setSpeaking(true);
    speak(readable(text), () => setSpeaking(false));
  };

  return (
    <div className="flex">
      <button
        type="button"
        data-testid="message-listen"
        aria-label={speaking ? "Stop reading this reply" : "Listen to this reply"}
        title={speaking ? "Stop" : "Listen"}
        onClick={toggle}
        className={`inline-flex items-center gap-1 rounded-full border px-2.5 py-0.5 text-xs transition-colors focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-accent ${
          speaking ? "border-ink-faint text-ink" : "border-border text-ink-soft hover:border-ink-faint hover:text-ink"
        }`}
      >
        {speaking ? <Square className="h-3 w-3 text-ink-muted" aria-hidden /> : <Volume2 className="h-3 w-3 text-ink-muted" aria-hidden />}
        {speaking ? "Stop" : "Listen"}
      </button>
    </div>
  );
}
