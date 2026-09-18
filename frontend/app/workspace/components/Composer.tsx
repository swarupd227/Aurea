"use client";

import { useEffect, useLayoutEffect, useMemo, useRef, useState } from "react";
import { ArrowUp } from "lucide-react";
import {
  applyMention,
  duplicateNames,
  findMentionQuery,
  isCompletedMention,
  rankMentionables,
} from "../lib/mention";
import type { Mentionable } from "../types";

export interface ComposerInsert {
  text: string;
  nonce: number;
}

export function Composer({
  disabled,
  onSend,
  placeholder,
  mentionables = [],
  insert = null,
}: {
  disabled: boolean;
  onSend: (text: string) => void;
  placeholder: string;
  mentionables?: Mentionable[];
  insert?: ComposerInsert | null;
}) {
  const [text, setText] = useState("");
  const [caret, setCaret] = useState(0);
  const [highlight, setHighlight] = useState(0);
  const [dismissedAt, setDismissedAt] = useState<number | null>(null);
  const ref = useRef<HTMLTextAreaElement>(null);
  const pendingCaret = useRef<number | null>(null);

  useEffect(() => {
    const el = ref.current;
    if (!el) return;
    el.style.height = "auto";
    el.style.height = `${Math.min(el.scrollHeight, 200)}px`;
  }, [text]);

  useLayoutEffect(() => {
    const el = ref.current;
    if (!el || pendingCaret.current === null) return;
    el.focus();
    el.setSelectionRange(pendingCaret.current, pendingCaret.current);
    setCaret(pendingCaret.current);
    pendingCaret.current = null;
  }, [text]);

  useEffect(() => {
    if (!insert) return;
    setText((current) => {
      const next = current && !/\s$/.test(current) ? `${current} ${insert.text}` : `${current}${insert.text}`;
      pendingCaret.current = next.length;
      return next;
    });
  }, [insert]);

  const mention = findMentionQuery(text, caret);
  const matches = useMemo(
    () => (mention ? rankMentionables(mentionables, mention.query) : []),
    [mention?.query, mention?.start, mentionables]
  );
  const dupes = useMemo(() => duplicateNames(mentionables), [mentionables]);
  const menuOpen = !!mention && matches.length > 0 && dismissedAt !== mention.start && !isCompletedMention(mention.query, mentionables);

  useEffect(() => setHighlight(0), [mention?.query, mention?.start]);
  useEffect(() => {
    if (!mention) setDismissedAt(null);
  }, [mention?.start]);

  const choose = (agent: Mentionable) => {
    if (!mention) return;
    const next = applyMention(text, mention, caret, agent.name);
    pendingCaret.current = next.caret;
    setText(next.text);
  };

  const submit = () => {
    const t = text.trim();
    if (!t || disabled) return;
    onSend(t);
    setText("");
    setDismissedAt(null);
  };

  const syncCaret = (el: HTMLTextAreaElement) => setCaret(el.selectionStart ?? el.value.length);

  return (
    <form
      className="relative flex items-end gap-2 rounded-lg border border-border bg-surface p-2 focus-within:border-accent"
      onSubmit={(e) => {
        e.preventDefault();
        submit();
      }}
    >
      {menuOpen && (
        <ul
          role="listbox"
          aria-label="Your agents"
          className="absolute bottom-full left-0 z-20 mb-1 max-h-64 w-full max-w-sm overflow-y-auto rounded-lg border border-border bg-surface p-1 shadow-lg"
        >
          {matches.map((a, i) => (
            <li
              key={a.id}
              id={`mention-${a.id}`}
              role="option"
              aria-selected={i === highlight}
              onMouseDown={(e) => {
                e.preventDefault();
                choose(a);
              }}
              onMouseEnter={() => setHighlight(i)}
              className={`cursor-pointer rounded px-2 py-1.5 text-sm ${i === highlight ? "bg-border-soft" : ""}`}
            >
              <div className="flex min-w-0 items-baseline gap-2">
                <span className="truncate text-ink">{a.name}</span>
                {dupes.has(a.name.toLowerCase()) && (
                  <span className="shrink-0 font-mono text-[10px] text-ink-muted">{a.id.slice(0, 8)}</span>
                )}
              </div>
              {a.description && <div className="truncate text-xs text-ink-muted">{a.description}</div>}
            </li>
          ))}
        </ul>
      )}
      <textarea
        ref={ref}
        rows={1}
        value={text}
        onChange={(e) => {
          setText(e.target.value);
          syncCaret(e.target);
        }}
        onSelect={(e) => syncCaret(e.currentTarget)}
        onKeyDown={(e) => {
          if (menuOpen) {
            if (e.key === "ArrowDown" || e.key === "ArrowUp") {
              e.preventDefault();
              const step = e.key === "ArrowDown" ? 1 : -1;
              setHighlight((h) => (h + step + matches.length) % matches.length);
              return;
            }
            if ((e.key === "Enter" || e.key === "Tab") && !e.shiftKey && !e.nativeEvent.isComposing) {
              e.preventDefault();
              choose(matches[Math.min(highlight, matches.length - 1)]);
              return;
            }
            if (e.key === "Escape") {
              e.preventDefault();
              setDismissedAt(mention!.start);
              return;
            }
          }
          if (e.key === "Enter" && !e.shiftKey && !e.nativeEvent.isComposing) {
            e.preventDefault();
            submit();
          }
        }}
        placeholder={placeholder}
        aria-label="Message Astra"
        disabled={disabled}
        className="max-h-[200px] min-h-[36px] flex-1 resize-none bg-transparent px-2 py-1.5 text-sm text-ink outline-none placeholder:text-ink-muted disabled:opacity-50"
      />
      <button
        type="submit"
        disabled={disabled || !text.trim()}
        aria-label="Send"
        className="flex h-8 w-8 shrink-0 items-center justify-center rounded bg-accent text-ink hover:bg-yellow-400 disabled:opacity-40 disabled:cursor-not-allowed transition-colors"
      >
        <ArrowUp className="h-4 w-4" />
      </button>
    </form>
  );
}
