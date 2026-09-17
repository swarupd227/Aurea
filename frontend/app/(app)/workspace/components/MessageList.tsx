"use client";

import { useEffect, useRef } from "react";

export interface Message {
  id: string;
  role: "user" | "astra" | "agent";
  text: string;
  card?: {
    kind: string;
    data?: Record<string, unknown>;
  } | null;
  createdAt: string;
}

export interface MessageListProps {
  messages: Message[];
  isLoading?: boolean;
}

export function MessageList({ messages, isLoading }: MessageListProps) {
  const messagesEndRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    messagesEndRef.current?.scrollIntoView({ behavior: "smooth" });
  }, [messages]);

  return (
    <div className="flex flex-col gap-4 overflow-y-auto flex-1 pb-4">
      {messages.length === 0 ? (
        <div className="flex flex-col items-center justify-center h-full text-ink-muted">
          <p className="text-sm">No messages yet</p>
          <p className="text-xs mt-1">Start a conversation to begin</p>
        </div>
      ) : (
        messages.map((message) => (
          <MessageBubble key={message.id} message={message} />
        ))
      )}

      {isLoading && (
        <div className="flex gap-2 items-start">
          <div className="flex-shrink-0 w-8 h-8 rounded-full bg-surface border border-border flex items-center justify-center">
            <span className="text-xs font-medium text-accent">A</span>
          </div>
          <div className="flex gap-1">
            <div className="w-2 h-2 rounded-full bg-ink-muted animate-pulse" />
            <div className="w-2 h-2 rounded-full bg-ink-muted animate-pulse" style={{ animationDelay: "0.2s" }} />
            <div className="w-2 h-2 rounded-full bg-ink-muted animate-pulse" style={{ animationDelay: "0.4s" }} />
          </div>
        </div>
      )}

      <div ref={messagesEndRef} />
    </div>
  );
}

function MessageBubble({ message }: { message: Message }) {
  const isUser = message.role === "user";
  const isAstra = message.role === "astra";

  return (
    <div className={`flex gap-2 ${isUser ? "justify-end" : "justify-start"}`}>
      {!isUser && (
        <div className="flex-shrink-0 w-8 h-8 rounded-full bg-surface border border-border flex items-center justify-center">
          <span className="text-xs font-medium text-accent">
            {isAstra ? "A" : "•"}
          </span>
        </div>
      )}

      <div
        className={`max-w-sm rounded-lg px-3 py-2 ${
          isUser
            ? "bg-accent text-ink rounded-br-none"
            : "bg-surface border border-border text-ink rounded-bl-none"
        }`}
      >
        <p className="text-sm whitespace-pre-wrap">{message.text}</p>
        {message.card && (
          <div className="mt-2 pt-2 border-t border-border-soft">
            <CardComponent card={message.card} />
          </div>
        )}
        <p className={`text-xs mt-1 ${isUser ? "text-ink-muted" : "text-ink-muted"}`}>
          {new Date(message.createdAt).toLocaleTimeString("en-US", {
            hour: "2-digit",
            minute: "2-digit",
          })}
        </p>
      </div>
    </div>
  );
}

function CardComponent({ card }: { card: Message["card"] }) {
  if (!card) return null;

  if (card.kind === "recommendation") {
    return (
      <div className="bg-border-soft rounded p-2 text-xs space-y-1">
        <p className="font-medium">{card.data?.action as string}</p>
        {card.data?.note && <p className="text-ink-muted">{card.data.note}</p>}
      </div>
    );
  }

  if (card.kind === "brief") {
    return (
      <div className="bg-border-soft rounded p-2 text-xs space-y-1">
        <p className="font-medium">Daily Brief</p>
        {card.data?.items && Array.isArray(card.data.items) && (
          <ul className="list-disc list-inside space-y-0.5">
            {(card.data.items as string[]).map((item, i) => (
              <li key={i} className="text-ink-muted">
                {item}
              </li>
            ))}
          </ul>
        )}
      </div>
    );
  }

  return (
    <div className="bg-border-soft rounded p-2 text-xs text-ink-muted">
      {card.kind}
    </div>
  );
}
