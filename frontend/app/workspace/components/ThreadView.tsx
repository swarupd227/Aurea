"use client";

import { useEffect, useRef } from "react";
import { CircleAlert } from "lucide-react";
import { Composer, type ComposerInsert } from "./Composer";
import { MessageView } from "./MessageView";
import { Briefing } from "./Briefing";
import { STARTERS } from "../lib/prompts";
import type { Message, Mentionable, ThreadStatus } from "../types";

export function ThreadView({
  messages,
  status,
  streaming,
  error,
  hasThread,
  onSend,
  onConfirm,
  onReject,
  mentionables,
  composerInsert,
}: {
  messages: Message[];
  status: ThreadStatus;
  streaming: boolean;
  error: string | null;
  hasThread: boolean;
  onSend: (text: string) => void;
  onConfirm: (id: string) => Promise<void>;
  onReject: (id: string) => Promise<void>;
  mentionables: Mentionable[];
  composerInsert: ComposerInsert | null;
}) {
  const bottomRef = useRef<HTMLDivElement>(null);
  useEffect(() => {
    bottomRef.current?.scrollIntoView({ block: "end", behavior: "smooth" });
  }, [messages.length, streaming]);

  const waiting = status === "awaiting_confirmation";
  const last = messages[messages.length - 1];
  const suggestions = !streaming && !waiting && last?.role === "astra" ? last.suggestions : [];
  const empty = messages.length === 0 && !streaming;

  return (
    <div className="flex h-full min-h-0 flex-col">
      <div className="min-h-0 flex-1 overflow-y-auto">
        <div className="mx-auto w-full max-w-3xl space-y-6 px-4 py-6 sm:px-6">
          {empty && (
            <div className="pt-[6vh]">
              <h1 className="text-2xl font-semibold tracking-tight text-balance text-ink">
                What should your agents do?
              </h1>
              <p className="mt-2 max-w-prose text-sm text-ink-muted">
                Ask about your clients, portfolios and recommendations, or have Astra do the
                work. Anything that changes the platform waits for your confirmation, and every
                answer shows what tools it used.
              </p>
              <Briefing onSend={onSend} />
              <div className="mt-6 grid gap-2 sm:grid-cols-2">
                {STARTERS.map((s) => (
                  <button
                    key={s.label}
                    type="button"
                    onClick={() => onSend(s.prompt)}
                    className="rounded-lg border border-border bg-surface px-3 py-2.5 text-left text-sm text-ink hover:border-accent/60 transition-colors"
                  >
                    {s.label}
                  </button>
                ))}
              </div>
            </div>
          )}

          {messages.map((m) => (
            <MessageView key={m.id} message={m} onConfirm={onConfirm} onReject={onReject} />
          ))}

          {streaming && (
            <div className="flex gap-3" aria-live="polite">
              <div
                aria-hidden
                className="mt-0.5 flex h-6 w-6 shrink-0 items-center justify-center rounded bg-accent font-mono text-[11px] font-bold text-ink agent-working"
              >
                A
              </div>
              <div className="flex items-center gap-2 text-sm text-ink-muted">
                <span aria-hidden className="inline-block h-1.5 w-1.5 animate-pulse rounded-full bg-accent" />
                Working
              </div>
            </div>
          )}

          {error && (
            <div role="alert" className="flex gap-2 rounded-lg border border-crit/35 bg-crit-bg p-3 text-sm text-ink">
              <CircleAlert className="mt-0.5 h-4 w-4 shrink-0 text-crit" aria-hidden />
              <span>{error}</span>
            </div>
          )}

          <div ref={bottomRef} />
        </div>
      </div>

      <div className="shrink-0 border-t border-border/60 bg-paper px-4 pb-4 pt-3 sm:px-6">
        <div className="mx-auto w-full max-w-3xl space-y-2">
          {suggestions.length > 0 && (
            <div className="flex flex-wrap gap-2">
              {suggestions.map((s) => (
                <button
                  key={s}
                  type="button"
                  onClick={() => onSend(s)}
                  className="rounded-full border border-border px-3 py-1 text-xs text-ink-soft hover:border-accent/60 hover:text-ink transition-colors"
                >
                  {s}
                </button>
              ))}
            </div>
          )}
          <Composer
            disabled={streaming || waiting}
            onSend={onSend}
            mentionables={mentionables}
            insert={composerInsert}
            placeholder={
              waiting
                ? "Confirm or choose Not now above to continue"
                : hasThread
                ? "Reply to Astra"
                : "Ask Astra, or type @ for one of your agents"
            }
          />
        </div>
      </div>
    </div>
  );
}
