"use client";

import { useEffect, useState } from "react";
import { MessageList, Message } from "../components/MessageList";
import { MessageInput } from "../components/MessageInput";
import { ConfirmationCard } from "../components/ConfirmationCard";
import * as api from "../api";

interface PendingAction {
  id: string;
  tool_key: string;
  confirmation_text: string;
}

interface ThreadData {
  id: string;
  kind: string;
  title: string;
  created_at: string;
  messages: Message[];
}

export default function ThreadPage({ params }: { params: { id: string } }) {
  const [thread, setThread] = useState<ThreadData | null>(null);
  const [messages, setMessages] = useState<Message[]>([]);
  const [pendingAction, setPendingAction] = useState<PendingAction | null>(
    null
  );
  const [isLoading, setIsLoading] = useState(true);
  const [isSending, setIsSending] = useState(false);
  const [error, setError] = useState<string | null>(null);

  // Load thread on mount
  useEffect(() => {
    const loadThread = async () => {
      try {
        const data = await api.fetchThread(params.id);
        setThread(data);
        setMessages(data.messages || []);
      } catch (err) {
        setError(err instanceof Error ? err.message : "Failed to load thread");
      } finally {
        setIsLoading(false);
      }
    };

    loadThread();
  }, [params.id]);

  const handleSendMessage = async (text: string) => {
    setIsSending(true);
    setError(null);

    try {
      const data = await api.sendMessage(params.id, text);

      // Add user message
      if (data.events) {
        // Multi-event response
        const userMsg: Message = {
          id: Math.random().toString(),
          role: "user",
          text,
          createdAt: new Date().toISOString(),
        };
        setMessages((prev) => [...prev, userMsg]);

        // Process events
        for (const event of data.events) {
          if (event.type === "message") {
            const astraMsg: Message = {
              id: Math.random().toString(),
              role: "astra",
              text: event.text,
              card: event.card,
              createdAt: new Date().toISOString(),
            };
            setMessages((prev) => [...prev, astraMsg]);
          }
        }
      } else if (data.type === "pending_action") {
        // Pending action response
        const userMsg: Message = {
          id: Math.random().toString(),
          role: "user",
          text,
          createdAt: new Date().toISOString(),
        };
        setMessages((prev) => [...prev, userMsg]);

        setPendingAction({
          id: data.pending_action_id,
          tool_key: data.tool_key,
          confirmation_text: data.confirmation_text,
        });
      }
    } catch (err) {
      setError(err instanceof Error ? err.message : "Failed to send message");
    } finally {
      setIsSending(false);
    }
  };

  const handleConfirmAction = async (actionId: string) => {
    try {
      const data = await api.confirmPendingAction(actionId, true);

      // Add success message
      if (data.result) {
        const successMsg: Message = {
          id: Math.random().toString(),
          role: "astra",
          text: `${data.tool_key} executed successfully`,
          createdAt: new Date().toISOString(),
        };
        setMessages((prev) => [...prev, successMsg]);
      }

      setPendingAction(null);
    } catch (err) {
      setError(
        err instanceof Error ? err.message : "Failed to confirm action"
      );
    }
  };

  const handleRejectAction = async (actionId: string) => {
    try {
      await api.confirmPendingAction(actionId, false);

      // Add rejection message
      const rejectionMsg: Message = {
        id: Math.random().toString(),
        role: "astra",
        text: "Action was rejected. Let me know what else I can help with.",
        createdAt: new Date().toISOString(),
      };
      setMessages((prev) => [...prev, rejectionMsg]);

      setPendingAction(null);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Failed to reject action");
    }
  };

  if (isLoading) {
    return (
      <div className="flex h-screen items-center justify-center">
        <div className="text-center">
          <div className="animate-spin rounded-full h-8 w-8 border-2 border-border border-t-accent mx-auto mb-4" />
          <p className="text-ink-muted text-sm">Loading conversation...</p>
        </div>
      </div>
    );
  }

  if (!thread) {
    return (
      <div className="flex h-screen items-center justify-center">
        <div className="text-center">
          <p className="text-ink-muted text-sm">Thread not found</p>
        </div>
      </div>
    );
  }

  return (
    <div className="flex flex-col h-screen">
      {/* Header */}
      <div className="border-b border-border bg-paper px-6 py-4">
        <h1 className="text-lg font-semibold text-ink">{thread.title}</h1>
        <p className="text-xs text-ink-muted mt-1">{thread.kind}</p>
      </div>

      {/* Messages */}
      <div className="flex-1 overflow-y-auto px-6 py-4">
        <MessageList messages={messages} isLoading={isSending} />
      </div>

      {/* Error */}
      {error && (
        <div className="border-t border-border bg-crit-bg text-crit px-6 py-3 text-sm">
          {error}
        </div>
      )}

      {/* Pending action */}
      {pendingAction && (
        <div className="border-t border-border bg-paper px-6 py-4">
          <ConfirmationCard
            pendingActionId={pendingAction.id}
            confirmationText={pendingAction.confirmation_text}
            toolKey={pendingAction.tool_key}
            onConfirm={handleConfirmAction}
            onReject={handleRejectAction}
          />
        </div>
      )}

      {/* Input */}
      <MessageInput onSend={handleSendMessage} isLoading={isSending} />
    </div>
  );
}
