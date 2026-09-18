"use client";

import { useCallback, useEffect, useState } from "react";
import { confirmPendingAction, fetchMentionables, fetchThread, sendMessage } from "../api";
import { useWorkspace } from "../context";
import { ThreadView } from "../components/ThreadView";
import type { Mentionable, ThreadDetail } from "../types";

export default function ThreadPage({ params }: { params: { id: string } }) {
  const { composerInsert, refetchThreads } = useWorkspace();
  const [thread, setThread] = useState<ThreadDetail | null>(null);
  const [mentionables, setMentionables] = useState<Mentionable[]>([]);
  const [loading, setLoading] = useState(true);
  const [streaming, setStreaming] = useState(false);
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
    setStreaming(true);
    setError(null);
    try {
      await sendMessage(params.id, text);
      await reload();
      refetchThreads();
    } catch (err) {
      setError(err instanceof Error ? err.message : "Failed to send message");
    } finally {
      setStreaming(false);
    }
  };

  const handleConfirm = async (pendingActionId: string) => {
    try {
      await confirmPendingAction(pendingActionId, true);
      await reload();
      refetchThreads();
    } catch (err) {
      setError(err instanceof Error ? err.message : "Failed to confirm action");
    }
  };

  const handleReject = async (pendingActionId: string) => {
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
      streaming={streaming}
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
