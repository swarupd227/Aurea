"use client";

import { useEffect, useState } from "react";
import { useRouter } from "next/navigation";
import { ArrowLeft, House, Plus } from "lucide-react";
import { fetchMentionables, fetchNeedsYou } from "../api";
import type { Mentionable, NeedsYou, ThreadSummary } from "../types";

function Section({ title, children, count }: { title: string; children: React.ReactNode; count?: number }) {
  return (
    <section className="space-y-1">
      <div className="flex h-6 items-center justify-between px-2">
        <h2 className="font-mono text-[11px] uppercase tracking-wider text-ink-muted">{title}</h2>
        {count !== undefined && (
          <span className="font-mono text-[11px] tabular-nums text-ink-muted">{count}</span>
        )}
      </div>
      {children}
    </section>
  );
}

export function Rail({
  threads,
  activeId,
  onSelect,
  onNew,
  onMention,
  view,
}: {
  threads: ThreadSummary[];
  activeId: string | null;
  onSelect: (id: string) => void;
  onNew: () => void;
  onMention: (name: string) => void;
  view: "home" | "thread";
}) {
  const router = useRouter();
  const [needsYou, setNeedsYou] = useState<NeedsYou | null>(null);
  const [agents, setAgents] = useState<Mentionable[]>([]);

  useEffect(() => {
    fetchNeedsYou().then(setNeedsYou).catch(() => setNeedsYou(null));
    fetchMentionables().then((d) => setAgents(d.mentionables)).catch(() => setAgents([]));
  }, []);

  const waitingThreads = threads.filter((t) => t.status === "awaiting_confirmation");
  const items = needsYou?.needs_decision ?? [];
  const needsYouTotal = waitingThreads.length + Math.max(0, (needsYou?.needs_decision_count ?? 0) - waitingThreads.length);

  return (
    <nav className="flex h-full min-h-0 flex-col bg-surface/60" aria-label="Astra">
      <div className="flex h-12 shrink-0 items-center gap-2 border-b border-border px-3">
        <div
          aria-hidden
          className="flex h-6 w-6 items-center justify-center rounded bg-accent font-mono text-[11px] font-bold text-ink"
        >
          A
        </div>
        <span className="text-[15px] font-semibold tracking-tight text-ink">Astra</span>
        <span className="rounded border border-border px-1 font-mono text-[10px] uppercase tracking-wider text-ink-muted">
          Preview
        </span>
      </div>

      <div className="flex items-center gap-1 p-2">
        <button
          type="button"
          onClick={onNew}
          aria-current={view === "home" ? "page" : undefined}
          className={`flex w-full items-center gap-2 rounded px-2 py-1.5 text-left text-sm transition-colors ${
            view === "home" ? "bg-border-soft text-ink" : "text-ink-soft hover:bg-border-soft/60"
          }`}
        >
          <House className="h-4 w-4 shrink-0 text-ink-muted" aria-hidden /> Home
        </button>
        <button
          type="button"
          onClick={onNew}
          aria-label="New conversation"
          title="New conversation"
          className="flex h-8 w-8 shrink-0 items-center justify-center rounded border border-border hover:bg-border-soft transition-colors"
        >
          <Plus className="h-4 w-4 text-ink" />
        </button>
      </div>

      <div className="min-h-0 flex-1 space-y-5 overflow-y-auto px-2 pb-4">
        <Section title="Needs you" count={needsYouTotal}>
          {needsYouTotal === 0 ? (
            <p className="px-2 text-xs text-ink-muted">Nothing waiting on you.</p>
          ) : (
            <ul className="space-y-0.5">
              {waitingThreads.map((t) => (
                <li key={t.id}>
                  <button
                    type="button"
                    onClick={() => onSelect(t.id)}
                    className="flex w-full items-center gap-2 rounded px-2 py-1.5 text-left text-sm hover:bg-border-soft transition-colors"
                  >
                    <span aria-hidden className="h-1.5 w-1.5 shrink-0 rounded-full bg-accent" />
                    <span className="truncate text-ink">{t.title || "Conversation"}</span>
                  </button>
                </li>
              ))}
              {items.slice(0, 6).map((item) => (
                <li key={item.id} className="rounded px-2 py-1.5 hover:bg-border-soft/60">
                  <div className="flex items-start gap-2">
                    <span aria-hidden className="mt-1.5 h-1.5 w-1.5 shrink-0 rounded-full bg-crit" />
                    <div className="min-w-0 flex-1">
                      <div className="truncate text-sm text-ink" title={item.title}>
                        {item.title}
                      </div>
                      <button
                        type="button"
                        onClick={() => onSelect(item.thread_id)}
                        className="mt-0.5 rounded text-xs font-medium text-accent hover:underline"
                      >
                        Decide here
                      </button>
                    </div>
                  </div>
                </li>
              ))}
            </ul>
          )}
        </Section>

        <Section title="Conversations">
          {threads.length === 0 ? (
            <p className="px-2 text-xs text-ink-muted">None yet.</p>
          ) : (
            <ul className="space-y-0.5">
              {threads.map((t) => (
                <li key={t.id}>
                  <button
                    type="button"
                    onClick={() => onSelect(t.id)}
                    aria-current={t.id === activeId ? "page" : undefined}
                    className={`flex w-full items-center gap-2 rounded px-2 py-1.5 text-left text-sm transition-colors ${
                      t.id === activeId ? "bg-border-soft text-ink" : "text-ink-soft hover:bg-border-soft/60"
                    }`}
                  >
                    <span className="truncate">{t.title || "Conversation"}</span>
                    {t.status === "awaiting_confirmation" && (
                      <span aria-hidden className="ml-auto h-1.5 w-1.5 shrink-0 rounded-full bg-accent" />
                    )}
                  </button>
                </li>
              ))}
            </ul>
          )}
        </Section>

        <Section title="Your agents">
          {agents.length === 0 ? (
            <p className="px-2 text-xs text-ink-muted">No agents you can run yet.</p>
          ) : (
            <ul className="space-y-0.5">
              {agents.slice(0, 8).map((a) => (
                <li key={a.id}>
                  <button
                    type="button"
                    onClick={() => onMention(a.name)}
                    title={`Mention @${a.name} in your message`}
                    className="flex w-full items-center gap-2 rounded px-2 py-1.5 text-left text-sm text-ink-soft hover:bg-border-soft/60 transition-colors"
                  >
                    <span aria-hidden className="h-1.5 w-1.5 shrink-0 rounded-full bg-ok" />
                    <span className="truncate">{a.name}</span>
                  </button>
                </li>
              ))}
            </ul>
          )}
        </Section>
      </div>

      <div className="shrink-0 border-t border-border p-2">
        <button
          type="button"
          onClick={() => router.push("/studio")}
          className="flex items-center gap-2 rounded px-2 py-1.5 text-xs text-ink-muted hover:bg-border-soft/60 hover:text-ink transition-colors"
        >
          <ArrowLeft className="h-3.5 w-3.5" aria-hidden /> Back to the classic app
        </button>
      </div>
    </nav>
  );
}
