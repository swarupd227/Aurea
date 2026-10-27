"use client";

import { useEffect, useMemo, useRef, useState } from "react";
import { useRouter } from "next/navigation";
import { CornerDownLeft, MessageSquarePlus, Search } from "lucide-react";
import { api, getUser } from "@/lib/api";
import { viewsForRole } from "@/lib/nav";
import { roleStarters, useSuggestionContext } from "@/lib/suggestions";
import { fetchThreads } from "@/app/workspace/api";
import { startConversation } from "@/app/workspace/lib/start";
import type { ThreadSummary } from "@/app/workspace/types";

interface Item {
  key: string;
  section: "Ask" | "Go to" | "Clients" | "Conversations";
  label: string;
  detail?: string;
  icon?: any;
  action: () => void;
}

/** Ctrl/Cmd+K from anywhere in the app. A typed sentence goes to Astra first; a word or two that
 * names a view or a client goes there first and offers Astra second; conversations, views and
 * clients are the fallbacks. Opened from the rail or the shell by the "astra:open-palette" event. */
export function CommandPalette() {
  const router = useRouter();
  const ctx = useSuggestionContext();
  const [open, setOpen] = useState(false);
  const [query, setQuery] = useState("");
  const [highlight, setHighlight] = useState(0);
  const [threads, setThreads] = useState<ThreadSummary[]>([]);
  const [households, setHouseholds] = useState<{ id: string; name: string }[]>([]);
  const [role, setRole] = useState("");
  const inputRef = useRef<HTMLInputElement>(null);

  useEffect(() => {
    const handler = (e: KeyboardEvent) => {
      const isMac = /Mac|iPhone|iPad/.test(navigator.userAgent);
      if ((isMac ? e.metaKey : e.ctrlKey) && e.key.toLowerCase() === "k") {
        e.preventDefault();
        setOpen((o) => !o);
      }
    };
    const openFromChrome = () => setOpen(true);
    window.addEventListener("keydown", handler);
    window.addEventListener("astra:open-palette", openFromChrome);
    return () => {
      window.removeEventListener("keydown", handler);
      window.removeEventListener("astra:open-palette", openFromChrome);
    };
  }, []);

  // Load what the fallbacks need only once the palette is actually opened.
  useEffect(() => {
    if (!open) return;
    setQuery("");
    setHighlight(0);
    setRole(getUser()?.role ?? "");
    requestAnimationFrame(() => inputRef.current?.focus());
    fetchThreads().then((d) => setThreads(d.threads)).catch(() => {});
    api<any[]>("/api/core/households")
      .then((rows) => setHouseholds((rows || []).map((h) => ({ id: String(h.id), name: String(h.name) }))))
      .catch(() => {});
  }, [open]);

  const q = query.trim().toLowerCase();
  const words = q ? q.split(/\s+/).length : 0;
  const isSentence = words >= 3 || q.endsWith("?");

  const items = useMemo<Item[]>(() => {
    const go = (path: string) => () => {
      router.push(path);
      setOpen(false);
    };
    const ask = (text: string) => async () => {
      setOpen(false);
      try {
        router.push(await startConversation(text));
      } catch {
        // The palette is closed; the workspace home still shows its own error state on retry.
        router.push("/workspace");
      }
    };

    const askItems: Item[] = q
      ? [{ key: "ask", section: "Ask", label: `Ask Astra “${query.trim()}”`, icon: MessageSquarePlus, action: ask(query.trim()) }]
      : roleStarters(role, ctx).slice(0, 3).map((s, i) => ({
          key: `starter-${i}`, section: "Ask" as const, label: s.prompt, icon: MessageSquarePlus, action: ask(s.prompt),
        }));

    const match = (text: string, extra: string[] = []) =>
      !q || [text, ...extra].some((t) => t.toLowerCase().includes(q));

    const views = viewsForRole(role)
      .filter((v) => match(v.label, v.keywords))
      .slice(0, q ? 8 : 6)
      .map<Item>((v) => ({ key: `view-${v.href}`, section: "Go to", label: v.label, icon: v.icon, action: go(v.href) }));

    const clients = q
      ? households.filter((h) => h.name.toLowerCase().includes(q)).slice(0, 4)
          .map<Item>((h) => ({ key: `hh-${h.id}`, section: "Clients", label: h.name, action: go(`/studio/clients/${h.id}`) }))
      : [];

    const convs = threads
      .filter((t) => !q || (t.title || "").toLowerCase().includes(q))
      .slice(0, q ? 5 : 4)
      .map<Item>((t) => ({
        key: `thread-${t.id}`, section: "Conversations", label: t.title || "Conversation",
        detail: t.status === "awaiting_confirmation" ? "Waiting on you" : undefined, action: go(`/workspace/${t.id}`),
      }));

    const nav = [...views, ...clients];
    // A word or two that names somewhere to go: go there first, Astra second.
    const navFirst = !!q && !isSentence && nav.length > 0;
    return navFirst ? [...nav, ...askItems, ...convs] : [...askItems, ...nav, ...convs];
  }, [q, query, role, ctx, households, threads, isSentence, router]);

  if (!open) return null;

  let lastSection = "";
  return (
    <div className="fixed inset-0 z-[60] flex items-start justify-center bg-black/50 px-3 pt-[12vh]" onClick={() => setOpen(false)}>
      <div
        role="dialog"
        aria-modal="true"
        aria-label="Command palette"
        data-testid="command-palette"
        className="w-full max-w-xl overflow-hidden rounded-lg border border-border bg-surface shadow-xl"
        onClick={(e) => e.stopPropagation()}
      >
        <div className="flex items-center gap-2 border-b border-border px-3">
          <Search className="h-4 w-4 shrink-0 text-ink-muted" aria-hidden />
          <input
            ref={inputRef}
            data-testid="command-palette-input"
            value={query}
            onChange={(e) => {
              setQuery(e.target.value);
              setHighlight(0);
            }}
            onKeyDown={(e) => {
              if (e.key === "ArrowDown") {
                e.preventDefault();
                setHighlight((h) => (h + 1) % Math.max(items.length, 1));
              } else if (e.key === "ArrowUp") {
                e.preventDefault();
                setHighlight((h) => (h - 1 + items.length) % Math.max(items.length, 1));
              } else if (e.key === "Enter") {
                e.preventDefault();
                items[highlight]?.action();
              } else if (e.key === "Escape") {
                setOpen(false);
              }
            }}
            placeholder="Tell Astra what to do, or jump to a view or client…"
            aria-label="Tell Astra what to do, or jump to a view or client"
            className="w-full bg-transparent py-3 text-sm text-ink outline-none placeholder:text-ink-muted"
          />
        </div>
        <ul className="max-h-[55vh] overflow-y-auto p-1" role="listbox">
          {items.map((item, i) => {
            const heading = item.section !== lastSection ? item.section : null;
            lastSection = item.section;
            const Icon = item.icon;
            return (
              <li key={item.key}>
                {heading && (
                  <div className="px-3 pb-0.5 pt-2 font-mono text-[10px] uppercase tracking-wider text-ink-faint">{heading}</div>
                )}
                <button
                  type="button"
                  role="option"
                  aria-selected={i === highlight}
                  onClick={item.action}
                  onMouseEnter={() => setHighlight(i)}
                  className={`flex w-full items-center gap-2 rounded px-3 py-2 text-left text-sm transition-colors ${
                    i === highlight ? "bg-border-soft text-ink" : "text-ink-soft"
                  }`}
                >
                  {Icon && <Icon className="h-4 w-4 shrink-0 text-ink-muted" aria-hidden />}
                  <span className="min-w-0 flex-1 truncate">{item.label}</span>
                  {item.detail && (
                    <span className="shrink-0 font-mono text-[10px] uppercase tracking-wider text-warn">{item.detail}</span>
                  )}
                  {i === highlight && <CornerDownLeft className="h-3.5 w-3.5 shrink-0 text-ink-muted" aria-hidden />}
                </button>
              </li>
            );
          })}
          {items.length === 0 && <li className="px-3 py-3 text-sm text-ink-muted">Nothing matches.</li>}
        </ul>
      </div>
    </div>
  );
}
