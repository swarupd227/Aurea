"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import { confirmPendingAction, fetchMentionables, fetchThread, sendMessageStream } from "../api";
import { useWorkspace } from "../context";
import { ThreadView } from "../components/ThreadView";
import { reduceLive } from "../lib/live";
import { takeFirstMessage } from "../lib/start";
import type { LiveTurn, Mentionable, ThreadDetail } from "../types";

export default function ThreadPage({ params }: { params: { id: string } }) {
  const { composerInsert, refetchThreads } = useWorkspace();
  const [thread, setThread] = useState<ThreadDetail | null>(null);
  const [mentionables, setMentionables] = useState<Mentionable[]>([]);
  const [loading, setLoading] = useState(true);
  const [live, setLive] = useState<LiveTurn | null>(null);
  const [pendingUser, setPendingUser] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);

  const reload = useCallback(async () => {
    const data = await fetchThread(params.id);
    setThread(data);
  }, [params.id]);

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
    try {
      await sendMessageStream(params.id, text, (event) => {
        if (event.type === "error") setError(event.message);
        else setLive((prev) => reduceLive(prev, event));
        // "done" and "pending_action" just end the stream; the reload() below
        // fetches the authoritative persisted state rather than reconstructing
        // it from stream events.
      });
      await reload();
      refetchThreads();
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
  useEffect(() => {
    if (!running && !confirming) return;
    const timer = setInterval(() => {
      reload().catch(() => {});
    }, 1500);
    return () => clearInterval(timer);
  }, [running, confirming, reload]);
  useEffect(() => {
    if (!running) refetchThreads();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [running]);

  const handleConfirm = async (pendingActionId: string, seenTier?: string) => {
    setError(null);
    setConfirming(true);
    try {
      await confirmPendingAction(pendingActionId, true, seenTier);
      await reload();
      refetchThreads();
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
