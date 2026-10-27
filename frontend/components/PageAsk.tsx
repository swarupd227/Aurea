"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import Link from "next/link";
import { usePathname } from "next/navigation";
import { CircleAlert, ChevronDown, ExternalLink, Plus } from "lucide-react";
import { getUser } from "@/lib/api";
import { readable } from "@/lib/refs";
import { pageSuggestions, useSuggestionContext } from "@/lib/suggestions";
import {
  confirmPendingAction, createThread, fetchMentionables, fetchThread, sendMessageStream,
} from "@/app/workspace/api";
import { Composer } from "@/app/workspace/components/Composer";
import { LiveMessage } from "@/app/workspace/components/LiveMessage";
import { MessageView } from "@/app/workspace/components/MessageView";
import { reduceLive } from "@/app/workspace/lib/live";
import type { LiveTurn, Mentionable, ThreadDetail } from "@/app/workspace/types";

/** A conversation with Astra docked at the foot of a page. It is the same Astra with the same
 * tools as the workspace - a Confirm / Not now card here is the server-held pause, not a copy -
 * and it never replaces the page: answers open above the composer and fold away. After a
 * confirmed change the page's data is told to reload (the "astra:changed" event). */
export default function PageAsk() {
  const pathname = usePathname();
  const ctx = useSuggestionContext();
  const role: string = typeof window !== "undefined" ? getUser()?.role ?? "" : "";
  const chips = pageSuggestions(pathname, role, ctx);

  const [mentionables, setMentionables] = useState<Mentionable[]>([]);
  const [thread, setThread] = useState<ThreadDetail | null>(null);
  const [open, setOpen] = useState(false);
  const [live, setLive] = useState<LiveTurn | null>(null);
  const [pendingUser, setPendingUser] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const bottomRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    fetchMentionables().then((d) => setMentionables(d.mentionables)).catch(() => {});
  }, []);

  // A page change starts a fresh conversation about the new page; the old one stays in Astra. A reply still
  // being written for the old page finishes on the server and lands in that thread, not on this page.
  const pathRef = useRef(pathname);
  useEffect(() => {
    pathRef.current = pathname;
    setThread(null);
    setOpen(false);
    setError(null);
  }, [pathname]);

  useEffect(() => {
    if (open) bottomRef.current?.scrollIntoView({ block: "end", behavior: "smooth" });
  }, [open, thread?.messages.length, live?.text, live?.steps.length, pendingUser]);

  const send = useCallback(
    async (text: string) => {
      setError(null);
      setOpen(true);
      setPendingUser(text);
      setLive({ text: "", steps: [] });
      const startedOn = pathRef.current;
      try {
        const id = thread?.id ?? (await createThread({ kind: "ask_astra", title: readable(text).slice(0, 60) })).id;
        await sendMessageStream(id, text, (event) => {
          if (event.type === "error") setError(event.message);
          else setLive((prev) => reduceLive(prev, event));
        });
        if (pathRef.current === startedOn) setThread(await fetchThread(id));
      } catch (err) {
        setError(err instanceof Error ? err.message : "Failed to reach Astra");
      } finally {
        setLive(null);
        setPendingUser(null);
      }
    },
    [thread?.id]
  );

  // A confirmed run is on the thread while it works; look again until it settles.
  const [confirming, setConfirming] = useState(false);
  const running = !!thread?.messages.some((m) => m.card?.kind === "agent_run" && m.card.data?.status === "running");
  const threadId = thread?.id;
  useEffect(() => {
    if (!threadId || (!running && !confirming)) return;
    const timer = setInterval(() => {
      fetchThread(threadId).then(setThread).catch(() => {});
    }, 1500);
    return () => clearInterval(timer);
  }, [threadId, running, confirming]);
  const wasRunning = useRef(false);
  useEffect(() => {
    // The run settled: what it proposed is new data for the page behind the dock.
    if (wasRunning.current && !running) window.dispatchEvent(new Event("astra:changed"));
    wasRunning.current = running;
  }, [running]);

  const settle = useCallback(
    async (pendingActionId: string, confirmed: boolean, seenTier?: string) => {
      setError(null);
      setConfirming(confirmed);
      try {
        await confirmPendingAction(pendingActionId, confirmed, seenTier);
        if (thread) setThread(await fetchThread(thread.id));
        // The page behind the dock is showing data this just changed: have it reload.
        if (confirmed) window.dispatchEvent(new Event("astra:changed"));
      } catch (err) {
        setError(err instanceof Error ? err.message : "Could not record that");
        if (thread) fetchThread(thread.id).then(setThread).catch(() => {});
      } finally {
        setConfirming(false);
      }
    },
    [thread]
  );

  const messages = thread?.messages ?? [];
  const waiting = thread?.status === "awaiting_confirmation";
  const last = messages[messages.length - 1];
  const followUps = !live && !waiting && last && last.role !== "user" ? last.suggestions : [];
  const shownChips = followUps.length > 0 ? followUps.map((s) => ({ label: s, prompt: s })) : thread ? [] : chips;
  const hasConversation = messages.length > 0 || !!live || !!pendingUser;

  return (
    <div
      data-testid="page-ask"
      className="fixed bottom-0 left-64 right-0 z-30 border-t border-border bg-paper/95 backdrop-blur"
    >
      <div className="mx-auto max-w-[1280px] px-8 pb-4 pt-2">
        {open && hasConversation && (
          <section
            aria-label="Conversation about this page"
            data-testid="page-ask-panel"
            className="mb-2 overflow-hidden rounded-lg border border-border bg-surface"
          >
            <header className="flex items-center gap-2 border-b border-border-soft px-3 py-1.5">
              <span className="font-mono text-[11px] uppercase tracking-wider text-ink-muted">Astra</span>
              <span className="flex-1" />
              {thread && (
                <Link
                  href={`/workspace/${thread.id}`}
                  data-testid="page-ask-open"
                  className="flex items-center gap-1 rounded px-1.5 py-0.5 text-xs text-ink-muted hover:bg-border-soft hover:text-ink"
                >
                  <ExternalLink className="h-3 w-3" aria-hidden /> Open in Astra
                </Link>
              )}
              <button
                type="button"
                onClick={() => {
                  setThread(null);
                  setOpen(false);
                  setError(null);
                }}
                data-testid="page-ask-new"
                disabled={!!live}
                className="flex items-center gap-1 rounded px-1.5 py-0.5 text-xs text-ink-muted hover:bg-border-soft hover:text-ink disabled:opacity-40"
              >
                <Plus className="h-3 w-3" aria-hidden /> New
              </button>
              <button
                type="button"
                onClick={() => setOpen(false)}
                data-testid="page-ask-close"
                aria-label="Fold the conversation away"
                className="rounded p-0.5 text-ink-muted hover:bg-border-soft hover:text-ink"
              >
                <ChevronDown className="h-4 w-4" />
              </button>
            </header>
            <div className="max-h-[45vh] space-y-4 overflow-y-auto p-3">
              {messages.map((m) => (
                <MessageView
                  key={m.id}
                  message={m}
                  onConfirm={(id, tier) => settle(id, true, tier)}
                  onReject={(id) => settle(id, false)}
                />
              ))}
              {pendingUser && (
                <div className="flex justify-end">
                  <div className="max-w-[85%] whitespace-pre-wrap break-words rounded-lg border border-accent/20 bg-accent/15 px-3.5 py-2 text-sm text-ink">
                    {readable(pendingUser)}
                  </div>
                </div>
              )}
              {live && <LiveMessage live={live} />}
              {error && (
                <div role="alert" className="flex gap-2 rounded-lg border border-crit/35 bg-crit-bg p-3 text-sm text-ink">
                  <CircleAlert className="mt-0.5 h-4 w-4 shrink-0 text-crit" aria-hidden />
                  <span>{error}</span>
                </div>
              )}
              <div ref={bottomRef} />
            </div>
          </section>
        )}

        {!open && thread && messages.length > 0 && (
          <button
            type="button"
            onClick={() => setOpen(true)}
            data-testid="page-ask-reopen"
            className="mb-2 flex w-full items-center gap-2 rounded-lg border border-border bg-surface px-3 py-1.5 text-left text-xs text-ink-muted hover:text-ink"
          >
            <span className="font-mono uppercase tracking-wider">Astra</span>
            <span className="truncate">{thread.title}</span>
            {waiting && <span className="ml-auto font-mono uppercase tracking-wider text-warn">Waiting on you</span>}
          </button>
        )}

        {shownChips.length > 0 && !live && (
          <ul className="mb-2 flex flex-wrap gap-2" data-testid="page-ask-chips">
            {shownChips.map((c) => (
              <li key={c.prompt}>
                <button
                  type="button"
                  onClick={() => void send(c.prompt)}
                  disabled={waiting}
                  title={c.prompt}
                  data-testid="page-ask-chip"
                  className="rounded-full border border-border px-3 py-1 text-xs text-ink-soft transition-colors hover:border-accent/60 hover:text-ink disabled:opacity-40"
                >
                  {c.label}
                </button>
              </li>
            ))}
          </ul>
        )}

        <Composer
          disabled={!!live || waiting}
          onSend={(t) => void send(t)}
          mentionables={mentionables}
          testId="page-ask-input"
          placeholder={waiting ? "Confirm or choose Not now above to continue" : "Ask Astra about this page, or tell an agent what to do"}
        />
      </div>
    </div>
  );
}
