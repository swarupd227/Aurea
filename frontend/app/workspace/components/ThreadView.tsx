"use client";

import { useEffect, useRef } from "react";
import { CircleAlert } from "lucide-react";
import { Composer, type ComposerInsert } from "./Composer";
import { MessageView } from "./MessageView";
import { LiveMessage } from "./LiveMessage";
import { Briefing } from "./Briefing";
import { getUser } from "@/lib/api";
import { roleStarters, useSuggestionContext } from "@/lib/suggestions";
import type { LiveTurn, Message, Mentionable, ThreadStatus } from "../types";

export function ThreadView({
  messages,
  status,
  live,
  pendingUser = null,
  awaitingReply = false,
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
  live: LiveTurn | null;
  /** The sentence just sent, shown at once while the turn streams in. */
  pendingUser?: string | null;
  /** The reply to the last question is still being written - the browser left and came back. */
  awaitingReply?: boolean;
  error: string | null;
  hasThread: boolean;
  onSend: (text: string) => void;
  onConfirm: (id: string, seenTier?: string) => Promise<void>;
  onReject: (id: string) => Promise<void>;
  mentionables: Mentionable[];
  composerInsert: ComposerInsert | null;
}) {
  const streaming = live !== null;
  const role = typeof window !== "undefined" ? getUser()?.role ?? "" : "";
  const ctx = useSuggestionContext();
  const starters = roleStarters(role, ctx);
  const bottomRef = useRef<HTMLDivElement>(null);
  useEffect(() => {
    bottomRef.current?.scrollIntoView({ block: "end", behavior: "smooth" });
  }, [messages.length, streaming, live?.text, live?.steps.length]);

  const waiting = status === "awaiting_confirmation";
  const last = messages[messages.length - 1];
  const suggestions = !streaming && !waiting && last && last.role !== "user" ? last.suggestions : [];
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
              <div className="mt-6" data-testid="workspace-starters">
                <h2 className="font-mono text-[11px] uppercase tracking-wider text-ink-muted">Try saying</h2>
                <ul className="mt-2 grid gap-2 sm:grid-cols-2">
                  {starters.map((s) => (
                    <li key={s.prompt}>
                      <button
                        type="button"
                        onClick={() => onSend(s.prompt)}
                        className="h-full w-full rounded-lg border border-border bg-surface px-3 py-2.5 text-left text-sm text-ink hover:border-accent/60 transition-colors"
                      >
                        <span className="text-ink-muted">&ldquo;</span>
                        {s.prompt}
                        <span className="text-ink-muted">&rdquo;</span>
                      </button>
                    </li>
                  ))}
                </ul>
              </div>
            </div>
          )}

          {messages.map((m) => (
            <MessageView key={m.id} message={m} onConfirm={onConfirm} onReject={onReject} />
          ))}

          {pendingUser && (
            <div className="flex justify-end">
              <div className="max-w-[85%] whitespace-pre-wrap break-words rounded-lg bg-accent/15 border border-accent/20 px-3.5 py-2 text-sm text-ink">
                {pendingUser}
              </div>
            </div>
          )}

          {live && <LiveMessage live={live} />}
          {!live && awaitingReply && (
            <div data-testid="awaiting-reply">
              <LiveMessage live={{ text: "", steps: [] }} label="Astra is still replying" />
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
            disabled={streaming || waiting || awaitingReply}
            onSend={onSend}
            mentionables={mentionables}
            insert={composerInsert}
            placeholder={
              awaitingReply
                ? "Astra is still replying"
                : waiting
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
