"use client";

import { useState, useRef } from "react";

export interface MessageInputProps {
  onSend: (text: string) => Promise<void>;
  isLoading?: boolean;
  placeholder?: string;
}

export function MessageInput({
  onSend,
  isLoading = false,
  placeholder = "Ask Astra anything...",
}: MessageInputProps) {
  const [text, setText] = useState("");
  const [isSending, setIsSending] = useState(false);
  const textareaRef = useRef<HTMLTextAreaElement>(null);

  const handleSend = async () => {
    if (!text.trim()) return;

    setIsSending(true);
    try {
      await onSend(text);
      setText("");
      if (textareaRef.current) {
        textareaRef.current.style.height = "auto";
      }
    } finally {
      setIsSending(false);
    }
  };

  const handleKeyDown = (e: React.KeyboardEvent<HTMLTextAreaElement>) => {
    if (e.key === "Enter" && e.ctrlKey) {
      handleSend();
    }
  };

  const handleChange = (e: React.ChangeEvent<HTMLTextAreaElement>) => {
    setText(e.target.value);
    // Auto-resize textarea
    if (textareaRef.current) {
      textareaRef.current.style.height = "auto";
      textareaRef.current.style.height = Math.min(
        textareaRef.current.scrollHeight,
        200
      ) + "px";
    }
  };

  const isDisabled = isSending || isLoading;

  return (
    <div className="border-t border-border bg-paper pt-4 px-4 pb-4">
      <div className="flex gap-2">
        <textarea
          ref={textareaRef}
          value={text}
          onChange={handleChange}
          onKeyDown={handleKeyDown}
          placeholder={placeholder}
          disabled={isDisabled}
          rows={1}
          className="flex-1 resize-none rounded border border-border bg-surface text-ink placeholder-ink-muted px-3 py-2 text-sm focus:outline-none focus:ring-2 focus:ring-accent disabled:opacity-50 disabled:cursor-not-allowed"
        />
        <button
          onClick={handleSend}
          disabled={isDisabled || !text.trim()}
          className="bg-accent text-ink px-4 py-2 rounded font-medium text-sm hover:bg-yellow-400 disabled:opacity-50 disabled:cursor-not-allowed transition-colors self-end"
        >
          Send
        </button>
      </div>
      <p className="text-xs text-ink-muted mt-2">
        Cmd+Enter or Ctrl+Enter to send
      </p>
    </div>
  );
}
