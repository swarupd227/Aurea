"use client";

import { useCallback, useEffect, useState } from "react";
import { confirmPendingAction, fetchMentionables, fetchThread, sendMessageStream } from "../api";
import { useWorkspace } from "../context";
import { ThreadView } from "../components/ThreadView";
import type { LiveTurn, Mentionable, ThreadDetail } from "../types";

export default function ThreadPage({ params }: { params: { id: string } }) {
  const { composerInsert, refetchThreads } = useWorkspace();
  const [thread, setThread] = useState<ThreadDetail | null>(null);
  const [mentionables, setMentionables] = useState<Mentionable[]>([]);
  const [loading, setLoading] = useState(true);
  const [live, setLive] = useState<LiveTurn | null>(null);
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
    setLive({ text: "", steps: [] });
    try {
      await sendMessageStream(params.id, text, (event) => {
        if (event.type === "token") {
          setLive((prev) => ({ text: (prev?.text ?? "") + event.text, steps: prev?.steps ?? [] }));
        } else if (event.type === "tool_start") {
          setLive((prev) => ({
            text: prev?.text ?? "",
            steps: [...(prev?.steps ?? []), { tool_key: event.tool_key, state: "running" }],
          }));
        } else if (event.type === "tool_result") {
          setLive((prev) => ({
            text: prev?.text ?? "",
            steps: (prev?.steps ?? []).map((s) =>
              s.tool_key === event.tool_key ? { ...s, state: event.ok ? "ok" : "failed" } : s
            ),
          }));
        } else if (event.type === "error") {
          setError(event.message);
        }
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
    }
  };

  const handleConfirm = async (pendingActionId: string) => {
    setError(null);
    try {
      await confirmPendingAction(pendingActionId, true);
      await reload();
      refetchThreads();
    } catch (err) {
      setError(err instanceof Error ? err.message : "Failed to confirm action");
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
