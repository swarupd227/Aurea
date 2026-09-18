"use client";

import { useEffect, useRef, useState } from "react";
import { useRouter } from "next/navigation";
import { Search } from "lucide-react";
import { useWorkspace } from "../context";

interface Item {
  key: string;
  label: string;
  detail?: string;
  action: () => void;
}

/** Cmd/Ctrl+K from anywhere in the workspace. First row always sends whatever
 * is typed to Astra as a new conversation; below it, matching existing
 * conversations to jump to instead. */
export function CommandPalette() {
  const router = useRouter();
  const { threads, askAstra } = useWorkspace();
  const [open, setOpen] = useState(false);
  const [query, setQuery] = useState("");
  const [highlight, setHighlight] = useState(0);
  const inputRef = useRef<HTMLInputElement>(null);

  useEffect(() => {
    const handler = (e: KeyboardEvent) => {
      const isMac = /Mac|iPhone|iPad/.test(navigator.userAgent);
      if ((isMac ? e.metaKey : e.ctrlKey) && e.key.toLowerCase() === "k") {
        e.preventDefault();
        setOpen((o) => !o);
      }
    };
    const openFromRail = () => setOpen(true);
    window.addEventListener("keydown", handler);
    window.addEventListener("astra:open-palette", openFromRail);
    return () => {
      window.removeEventListener("keydown", handler);
      window.removeEventListener("astra:open-palette", openFromRail);
    };
  }, []);

  useEffect(() => {
    if (open) {
      setQuery("");
      setHighlight(0);
      requestAnimationFrame(() => inputRef.current?.focus());
    }
  }, [open]);

  const q = query.trim().toLowerCase();
  const matchingThreads = threads
    .filter((t) => !q || (t.title || "").toLowerCase().includes(q))
    .slice(0, 6);

  const items: Item[] = [
    {
      key: "ask",
      label: q ? `Ask Astra "${query.trim()}"` : "Ask Astra…",
      action: () => {
        if (!q) return;
        void askAstra(query.trim());
        setOpen(false);
      },
    },
    ...matchingThreads.map((t) => ({
      key: t.id,
      label: t.title || "Conversation",
      detail: t.status === "awaiting_confirmation" ? "Waiting on you" : undefined,
      action: () => {
        router.push(`/workspace/${t.id}`);
        setOpen(false);
      },
    })),
  ];

  if (!open) return null;

  return (
    <div
      className="fixed inset-0 z-50 flex items-start justify-center bg-black/40 pt-[15vh]"
      onClick={() => setOpen(false)}
    >
      <div
        role="dialog"
        aria-modal="true"
        aria-label="Command palette"
        className="w-full max-w-lg overflow-hidden rounded-lg border border-border bg-surface shadow-xl"
        onClick={(e) => e.stopPropagation()}
      >
        <div className="flex items-center gap-2 border-b border-border px-3">
          <Search className="h-4 w-4 shrink-0 text-ink-muted" aria-hidden />
          <input
            ref={inputRef}
            value={query}
            onChange={(e) => {
              setQuery(e.target.value);
              setHighlight(0);
            }}
            onKeyDown={(e) => {
              if (e.key === "ArrowDown") {
                e.preventDefault();
                setHighlight((h) => (h + 1) % items.length);
              } else if (e.key === "ArrowUp") {
                e.preventDefault();
                setHighlight((h) => (h - 1 + items.length) % items.length);
              } else if (e.key === "Enter") {
                e.preventDefault();
                items[highlight]?.action();
              } else if (e.key === "Escape") {
                setOpen(false);
              }
            }}
            placeholder="Ask Astra, or search conversations…"
            className="w-full bg-transparent py-3 text-sm text-ink outline-none placeholder:text-ink-muted"
          />
        </div>
        <ul className="max-h-80 overflow-y-auto p-1" role="listbox">
          {items.map((item, i) => (
            <li key={item.key}>
              <button
                type="button"
                role="option"
                aria-selected={i === highlight}
                onClick={item.action}
                onMouseEnter={() => setHighlight(i)}
                className={`flex w-full items-center justify-between gap-2 rounded px-3 py-2 text-left text-sm transition-colors ${
                  i === highlight ? "bg-border-soft text-ink" : "text-ink-soft"
                }`}
              >
                <span className="truncate">{item.label}</span>
                {item.detail && (
                  <span className="shrink-0 font-mono text-[10px] uppercase tracking-wider text-accent">
                    {item.detail}
                  </span>
                )}
              </button>
            </li>
          ))}
        </ul>
      </div>
    </div>
  );
}
