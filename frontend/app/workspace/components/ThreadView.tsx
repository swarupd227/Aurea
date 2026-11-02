"use client";

import { useEffect, useRef } from "react";
import { CircleAlert, PanelRight, X } from "lucide-react";
import { contextPrefix, useWorkspaceOptional } from "../context";
import { Composer, type ComposerInsert } from "./Composer";
import { MessageView } from "./MessageView";
import { LiveMessage } from "./LiveMessage";
import { Briefing } from "./Briefing";
import { getUser } from "@/lib/api";
import { readable } from "@/lib/refs";
import { roleStarters, useSuggestionContext } from "@/lib/suggestions";
import type { LiveTurn, Message, Mentionable, ThreadStatus } from "../types";

function greeting(): string {
  const hour = new Date().getHours();
  return hour < 12 ? "Good morning" : hour < 18 ? "Good afternoon" : "Good evening";
}

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
  const ws = useWorkspaceOptional();
  const chip = ws?.contextChip ?? null;
  // "Ask about this" names the open view (route and id, never its data) in front of the message;
  // Astra still calls a tool before it states anything about it.
  const send = (text: string) => {
    if (chip) {
      onSend(`${contextPrefix(chip)} ${text}`);
      ws?.setContextChip(null);
    } else onSend(text);
  };
  const streaming = live !== null;
  const me = typeof window !== "undefined" ? getUser() : null;
  const role = me?.role ?? "";
  const firstName = String(me?.full_name ?? "").split(" ")[0];
  const today = new Date().toLocaleDateString(undefined, { weekday: "long", day: "numeric", month: "long" });
  const ctx = useSuggestionContext();
  const starters = roleStarters(role, ctx);
  // The newest words stay in view while someone is reading the end of the thread. Opening a view
  // beside it, or a card landing, changes its height, so following watches the size as well as the
  // messages. Scrolling up to read stops it; sending starts it again.
  const scroller = useRef<HTMLDivElement>(null);
  const body = useRef<HTMLDivElement>(null);
  const following = useRef(true);
  useEffect(() => {
    const box = scroller.current;
    const inner = body.current;
    if (!box || !inner) return;
    const toEnd = () => {
      if (following.current) box.scrollTop = box.scrollHeight;
    };
    const seen = new ResizeObserver(toEnd);
    seen.observe(box);
    seen.observe(inner);
    return () => seen.disconnect();
  }, []);
  useEffect(() => {
    if (pendingUser) following.current = true;
    if (following.current && scroller.current) scroller.current.scrollTop = scroller.current.scrollHeight;
  }, [messages.length, pendingUser, streaming, live?.text, live?.steps.length]);

  const waiting = status === "awaiting_confirmation";
  const last = messages[messages.length - 1];
  const offered = !streaming && !waiting && last && last.role !== "user" ? last.suggestions : [];
  // Beside an open view the column is narrow: two, so they never push the conversation off the screen.
  const suggestions = ws?.pane ? offered.slice(0, 2) : offered;
  const empty = messages.length === 0 && !streaming;

  return (
    <div className="flex h-full min-h-0 flex-col">
      <div
        ref={scroller}
        data-testid="thread-scroll"
        className="min-h-0 flex-1 overflow-y-auto"
        onScroll={(e) => {
          const box = e.currentTarget;
          following.current = box.scrollHeight - box.scrollTop - box.clientHeight < 80;
        }}
      >
        <div ref={body} className="mx-auto w-full max-w-3xl space-y-6 px-4 py-6 sm:px-6">
          {empty && (
            <div className="pt-[6vh]">
              <h1 className="text-2xl font-semibold tracking-tight text-balance text-ink" data-testid="workspace-greeting">
                {greeting()}{firstName ? `, ${firstName}` : ""}
              </h1>
              <p className="mt-1 text-sm text-ink-muted">{today}</p>
              <Briefing onSend={onSend} />
              <div className="mt-6" data-testid="workspace-starters">
                <h2 className="font-mono text-[11px] uppercase tracking-wider text-ink-muted">Ask Astra</h2>
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
            <MessageView key={m.id} message={m} onConfirm={onConfirm} onReject={onReject} onOpenView={ws?.openView} />
          ))}

          {pendingUser && (
            <div className="flex justify-end">
              <div className="max-w-[85%] whitespace-pre-wrap break-words rounded-lg bg-accent/15 border border-accent/20 px-3.5 py-2 text-sm text-ink">
                {readable(pendingUser)}
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
                  title={s}
                  className="max-w-full truncate rounded-full border border-border px-3 py-1 text-xs text-ink-soft hover:border-accent/60 hover:text-ink transition-colors"
                >
                  {s}
                </button>
              ))}
            </div>
          )}
          {chip && (
            <div className="flex items-center gap-2" data-testid="composer-context">
              <span className="inline-flex min-w-0 items-center gap-1.5 rounded-full border border-border bg-surface py-0.5 pl-2.5 pr-1 text-xs text-ink-soft">
                <PanelRight className="h-3 w-3 shrink-0 text-ink-muted" aria-hidden />
                <button
                  type="button"
                  onClick={() => ws?.openView(chip.route)}
                  className="min-w-0 truncate hover:text-ink"
                  title={chip.route}
                >
                  {chip.label}
                  {chip.id && <span className="ml-1 font-mono text-[10px] text-ink-muted">{chip.id.slice(0, 8)}</span>}
                </button>
                <button
                  type="button"
                  onClick={() => ws?.setContextChip(null)}
                  aria-label="Remove the view from this message"
                  className="flex h-4 w-4 shrink-0 items-center justify-center rounded-full text-ink-muted hover:bg-border-soft hover:text-ink"
                >
                  <X className="h-3 w-3" />
                </button>
              </span>
            </div>
          )}
          <Composer
            disabled={streaming || waiting || awaitingReply}
            onSend={send}
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
