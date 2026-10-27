"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import { getUser } from "@/lib/api";
import { matchPaneView } from "@/lib/paneViews";
import { confirmPendingAction, fetchMentionables, fetchThread, sendMessageStream } from "../api";
import { useWorkspace } from "../context";
import { ThreadView } from "../components/ThreadView";
import { reduceLive } from "../lib/live";
import { takeFirstMessage } from "../lib/start";
import type { LiveTurn, Mentionable, ThreadDetail } from "../types";

/** The view Astra asked to open this turn (an `open_view` result on the reply), if any. */
function viewOpenedBy(thread: ThreadDetail, sinceCount: number): string | null {
  for (const m of thread.messages.slice(sinceCount).reverse()) {
    const a = m.artifacts?.find((x) => x.tool_key === "open_view" && typeof x.result?.view === "string");
    if (a) return a.result.view as string;
  }
  return null;
}

export default function ThreadPage({ params }: { params: { id: string } }) {
  const { composerInsert, refetchThreads, registerSender, insertInComposer, openView } = useWorkspace();
  const [thread, setThread] = useState<ThreadDetail | null>(null);
  const [mentionables, setMentionables] = useState<Mentionable[]>([]);
  const [loading, setLoading] = useState(true);
  const [live, setLive] = useState<LiveTurn | null>(null);
  const [pendingUser, setPendingUser] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);

  const reload = useCallback(async () => {
    const data = await fetchThread(params.id);
    setThread(data);
    return data;
  }, [params.id]);

  // A decision taken in the work pane may be the very proposal this thread is talking about.
  useEffect(() => {
    const again = () => {
      reload().catch(() => {});
    };
    window.addEventListener("astra:changed", again);
    return () => window.removeEventListener("astra:changed", again);
  }, [reload]);

  useEffect(() => {
    setLoading(true);
    reload()
      .catch((err) => setError(err instanceof Error ? err.message : "Failed to load thread"))
      .finally(() => setLoading(false));
    fetchMentionables().then((d) => setMentionables(d.mentionables)).catch(() => {});
  }, [reload]);

  const handleSend = async (text: string) => {
    setError(null);
    setPendingUser(text);
    setLive({ text: "", steps: [] });
    const before = thread?.messages.length ?? 0;
    try {
      await sendMessageStream(params.id, text, (event) => {
        if (event.type === "error") setError(event.message);
        else setLive((prev) => reduceLive(prev, event));
        // "done" and "pending_action" just end the stream; the reload() below
        // fetches the authoritative persisted state rather than reconstructing
        // it from stream events.
      });
      const data = await reload();
      refetchThreads();
      // Astra opened a view with its reply: show it beside the conversation. Only for the turn
      // just streamed - reopening an old thread does not reopen what it once showed.
      const opened = viewOpenedBy(data, before);
      if (opened && matchPaneView(opened, getUser()?.role)) openView(opened);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Failed to send message");
    } finally {
      setLive(null);
      setPendingUser(null);
    }
  };

  // A conversation started from the home composer, the palette or a Needs-you row arrives with
  // its first sentence waiting; send it once the thread has loaded so the turn streams in view.
  const sent = useRef(false);
  useEffect(() => {
    if (loading || !thread || sent.current) return;
    sent.current = true;
    const first = takeFirstMessage(params.id);
    if (first && thread.messages.length === 0) void handleSend(first);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [loading, thread]);

  // A confirmed run is on the thread from the moment it starts (the server publishes its card
  // before the agent runs) and settles when it finishes. Look again while that is happening - a
  // run can also be mid-way when this page is opened.
  const [confirming, setConfirming] = useState(false);
  const running = thread?.messages.some((m) => m.card?.kind === "agent_run" && m.card.data?.status === "running");
  // The reply to the last question may still be being written: the server finishes a turn even if the
  // browser that asked has left, so coming back mid-reply shows the question and waits for the answer.
  // `working` is exact for the replica running the turn; a question asked in the last two minutes with
  // no answer yet is also looked at again, because another replica cannot say.
  const last = thread?.messages[thread.messages.length - 1];
  const askedAt = last
    ? Date.parse(/(Z|[+-]\d\d:?\d\d)$/.test(last.created_at) ? last.created_at : `${last.created_at}Z`)
    : NaN;
  const awaitingReply =
    live === null && !!thread && (!!thread.working || (last?.role === "user" && Date.now() - askedAt < 120_000));
  useEffect(() => {
    if (!running && !confirming && !awaitingReply) return;
    const timer = setInterval(() => {
      reload().catch(() => {});
    }, 1500);
    return () => clearInterval(timer);
  }, [running, confirming, awaitingReply, reload]);
  useEffect(() => {
    if (!running) refetchThreads();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [running]);
  // A run settling put new proposals on the book: the open view (and the rail) look again.
  const wasRunning = useRef(false);
  useEffect(() => {
    if (wasRunning.current && !running) window.dispatchEvent(new Event("astra:changed"));
    wasRunning.current = !!running;
  }, [running]);

  // The work pane hands sentences to this thread (an agent run from a client's page, a Delegate
  // chip). While the thread cannot take one - a reply streaming, a confirmation waiting - the
  // sentence goes into the composer instead, to send when it can.
  const busy = live !== null || thread?.status === "awaiting_confirmation" || awaitingReply;
  const sendRef = useRef(handleSend);
  sendRef.current = handleSend;
  const busyRef = useRef(busy);
  busyRef.current = busy;
  useEffect(() => {
    registerSender((text) => (busyRef.current ? insertInComposer(text) : void sendRef.current(text)));
    return () => registerSender(null);
  }, [registerSender, insertInComposer]);

  const handleConfirm = async (pendingActionId: string, seenTier?: string) => {
    setError(null);
    setConfirming(true);
    try {
      await confirmPendingAction(pendingActionId, true, seenTier);
      await reload();
      refetchThreads();
      // The view beside the conversation may be showing what this just changed.
      window.dispatchEvent(new Event("astra:changed"));
    } catch (err) {
      setError(err instanceof Error ? err.message : "Failed to confirm action");
      await reload().catch(() => {}); // the card may now show a different tier, or no longer be open
    } finally {
      setConfirming(false);
    }
  };

  const handleReject = async (pendingActionId: string) => {
    setError(null);
    try {
      await confirmPendingAction(pendingActionId, false);
      await reload();
      refetchThreads();
    } catch (err) {
      setError(err instanceof Error ? err.message : "Failed to reject action");
    }
  };

  if (loading) {
    return (
      <div className="flex h-full items-center justify-center">
        <div className="h-6 w-6 animate-spin rounded-full border-2 border-border border-t-accent" />
      </div>
    );
  }

  if (!thread) {
    return (
      <div className="flex h-full items-center justify-center text-sm text-ink-muted">
        Conversation not found
      </div>
    );
  }

  return (
    <ThreadView
      messages={thread.messages}
      status={thread.status}
      live={live}
      pendingUser={pendingUser}
      awaitingReply={awaitingReply}
      error={error}
      hasThread
      onSend={handleSend}
      onConfirm={handleConfirm}
      onReject={handleReject}
      mentionables={mentionables}
      composerInsert={composerInsert}
    />
  );
}
