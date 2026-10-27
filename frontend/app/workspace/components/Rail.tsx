"use client";

import { useEffect, useState } from "react";
import { useRouter } from "next/navigation";
import { ArrowUpRight, ChevronRight, House, LogOut, MessageSquarePlus, Plus, Search } from "lucide-react";
import { clearSession, getUser } from "@/lib/api";
import { moreViews, pinnedViews } from "@/lib/nav";
import { matchPaneView } from "@/lib/paneViews";
import RoleSwitcher from "@/components/RoleSwitcher";
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

const rowBase = "flex w-full items-center gap-2 rounded px-2 py-1.5 text-left text-sm transition-colors";

/** Shown until a section's data has arrived: an empty list must mean empty, not "not loaded yet". */
function Loading() {
  return <div role="status" aria-label="Loading" className="mx-2 my-1 h-3 w-28 rounded bg-border-soft motion-safe:animate-pulse" />;
}

export function Rail({
  threads,
  activeId,
  onSelect,
  onNew,
  onMention,
  onAsk,
  onOpenView,
  openRoute = null,
  view,
  threadsLoaded = true,
}: {
  threads: ThreadSummary[];
  /** False until the conversation list has been fetched once. */
  threadsLoaded?: boolean;
  activeId: string | null;
  onSelect: (id: string) => void;
  onNew: () => void;
  onMention: (name: string) => void;
  /** Start a conversation with this sentence. */
  onAsk: (text: string) => void;
  /** Open a view: in the work pane if it is a pane view, as its page otherwise. */
  onOpenView?: (route: string) => void;
  /** The route open in the work pane, if any. */
  openRoute?: string | null;
  view: "home" | "thread";
}) {
  const router = useRouter();
  const open = (route: string) => (onOpenView ? onOpenView(route) : router.push(route));
  const openPath = openRoute ? openRoute.split("?")[0] : null;
  const [needsYou, setNeedsYou] = useState<NeedsYou | null>(null);
  const [agents, setAgents] = useState<Mentionable[] | null>(null);
  const [shortcutLabel, setShortcutLabel] = useState("Ctrl K");
  const [user, setUser] = useState<any>(null);
  const [showAllThreads, setShowAllThreads] = useState(false);

  useEffect(() => {
    setUser(getUser());
    fetchMentionables().then((d) => setAgents(d.mentionables)).catch(() => setAgents([]));
    if (/Mac|iPhone|iPad/.test(navigator.userAgent)) setShortcutLabel("⌘K");
  }, []);

  // Re-read what is waiting whenever a conversation settles (a decision was made, a run finished).
  const threadSignature = threads.map((t) => `${t.id}:${t.status}:${t.updated_at}`).join("|");
  // Threads whose question was just answered, until the thread list itself catches up.
  const [answeredThreads, setAnsweredThreads] = useState<Set<string>>(new Set());
  useEffect(() => setAnsweredThreads(new Set()), [threadSignature]);
  useEffect(() => {
    // A failed first read shows the section as empty rather than loading for ever.
    fetchNeedsYou().then(setNeedsYou).catch(() =>
      setNeedsYou((n) => n ?? { needs_decision: [], needs_decision_count: 0, proposals: [], proposals_count: 0 }));
  }, [threadSignature]);
  // A decision taken in the work pane changes what is waiting without moving any thread.
  useEffect(() => {
    const again = () => fetchNeedsYou().then(setNeedsYou).catch(() => {});
    // A confirmation answered in a thread leaves Needs-you at once, not when its run finishes.
    const answered = (e: Event) => {
      const id = (e as CustomEvent<string>).detail;
      setNeedsYou((n) => {
        const item = n?.needs_decision.find((i) => i.id === id);
        if (!n || !item) return n;
        setAnsweredThreads((s) => new Set(s).add(item.thread_id));
        return {
          ...n,
          needs_decision: n.needs_decision.filter((i) => i.id !== id),
          needs_decision_count: Math.max(0, n.needs_decision_count - 1),
        };
      });
    };
    window.addEventListener("astra:changed", again);
    window.addEventListener("astra:pending-answered", answered);
    return () => {
      window.removeEventListener("astra:changed", again);
      window.removeEventListener("astra:pending-answered", answered);
    };
  }, []);

  const role: string = user?.role ?? "";
  const pinned = pinnedViews(role);
  const more = moreViews(role);

  // The newest few are enough on the rail; the rest are one palette search away.
  const RECENT = 8;
  const shownThreads = showAllThreads ? threads : threads.slice(0, RECENT);
  const items = needsYou?.needs_decision ?? [];
  // A waiting thread is listed once: by its question when Needs-you has it, by its title otherwise.
  const asked = new Set(items.map((i) => i.thread_id));
  const waitingThreads = threads.filter(
    (t) => t.status === "awaiting_confirmation" && !answeredThreads.has(t.id) && !asked.has(t.id)
  );
  const proposals = needsYou?.proposals ?? [];
  const proposalsTotal = needsYou?.proposals_count ?? proposals.length;
  const needsYouTotal = waitingThreads.length + (needsYou?.needs_decision_count ?? 0) + proposalsTotal;

  return (
    <nav className="flex h-full min-h-0 flex-col bg-surface/60" aria-label="Astra" data-testid="astra-rail">
      <div className="flex h-12 shrink-0 items-center gap-2 border-b border-border px-3">
        <div
          aria-hidden
          className="flex h-6 w-6 items-center justify-center rounded bg-accent font-mono text-[11px] font-bold text-black"
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
          className={`${rowBase} ${view === "home" ? "bg-border-soft text-ink" : "text-ink-soft hover:bg-border-soft/60"}`}
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
        <Section title="Needs you" count={needsYou ? needsYouTotal : undefined}>
          {!needsYou && waitingThreads.length === 0 ? (
            <Loading />
          ) : needsYouTotal === 0 ? (
            <p className="px-2 text-xs text-ink-muted">Nothing waiting on you.</p>
          ) : (
            <ul className="space-y-0.5" data-testid="needs-you-list">
              {waitingThreads.map((t) => (
                <li key={t.id}>
                  <button
                    type="button"
                    onClick={() => onSelect(t.id)}
                    className={`${rowBase} hover:bg-border-soft`}
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
              {proposals.slice(0, 5).map((p) => {
                const walk = () =>
                  onAsk(
                    `Walk me through the proposal "${p.title}"${p.subject_label ? ` for ${p.subject_label}` : ""}, and ${
                      p.action === "veto" ? "whether I should dismiss it" : "what it would do if I approve it"
                    }.`
                  );
                // The proposal opens beside the conversation where this role has the Recommendations
                // view; otherwise the row asks Astra about it, as it always did.
                const route = `/studio/review?rec=${p.id}`;
                const inPane = !!onOpenView && !!matchPaneView(route, role);
                return (
                  <li key={p.id} className="group/row relative">
                    <button
                      type="button"
                      data-testid="needs-you-proposal"
                      onClick={inPane ? () => open(route) : walk}
                      title={p.title}
                      aria-current={openRoute === route ? "page" : undefined}
                      className={`${rowBase} items-start pr-8 hover:bg-border-soft ${openRoute === route ? "bg-border-soft" : ""}`}
                    >
                      <span
                        aria-hidden
                        className={`mt-1.5 h-1.5 w-1.5 shrink-0 rounded-full ${p.priority <= 2 ? "bg-crit" : "bg-warn"}`}
                      />
                      <span className="min-w-0">
                        <span className="block truncate text-ink">{p.title}</span>
                        {p.subject_label && <span className="block truncate text-xs text-ink-muted">{p.subject_label}</span>}
                      </span>
                    </button>
                    {inPane && (
                      <button
                        type="button"
                        onClick={walk}
                        data-testid="needs-you-proposal-ask"
                        aria-label={`Ask Astra about "${p.title}"`}
                        title="Ask Astra about it"
                        className="absolute right-1 top-1.5 flex h-6 w-6 items-center justify-center rounded text-ink-muted opacity-0 transition-opacity hover:bg-border hover:text-ink focus-visible:opacity-100 group-hover/row:opacity-100"
                      >
                        <MessageSquarePlus className="h-3.5 w-3.5" aria-hidden />
                      </button>
                    )}
                  </li>
                );
              })}
              {proposalsTotal > Math.min(proposals.length, 5) && (
                <li>
                  <button
                    type="button"
                    data-testid="needs-you-more"
                    onClick={() =>
                      onOpenView && matchPaneView("/studio/review", role)
                        ? open("/studio/review")
                        : onAsk("What proposals are waiting for my decision, most urgent first?")
                    }
                    className={`${rowBase} text-xs text-ink-muted hover:bg-border-soft/60 hover:text-ink`}
                  >
                    {proposalsTotal - Math.min(proposals.length, 5)} more proposals
                    <ChevronRight className="ml-auto h-3.5 w-3.5" aria-hidden />
                  </button>
                </li>
              )}
            </ul>
          )}
        </Section>

        <Section title="Conversations">
          {!threadsLoaded ? (
            <Loading />
          ) : threads.length === 0 ? (
            <p className="px-2 text-xs text-ink-muted">None yet.</p>
          ) : (
            <ul className="space-y-0.5">
              {shownThreads.map((t) => (
                <li key={t.id}>
                  <button
                    type="button"
                    onClick={() => onSelect(t.id)}
                    aria-current={t.id === activeId ? "page" : undefined}
                    className={`${rowBase} ${
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
              {threads.length > RECENT && (
                <li>
                  <button
                    type="button"
                    onClick={() => setShowAllThreads((v) => !v)}
                    data-testid="rail-all-conversations"
                    className={`${rowBase} text-xs text-ink-muted hover:bg-border-soft/60 hover:text-ink`}
                  >
                    {showAllThreads ? "Show fewer" : `All ${threads.length} conversations`}
                  </button>
                </li>
              )}
            </ul>
          )}
        </Section>

        <Section title="Views">
          <ul className="space-y-0.5" data-testid="rail-views">
            {pinned.map((v) => {
              const Icon = v.icon;
              const here = openPath === v.href;
              return (
                <li key={v.href}>
                  <button
                    type="button"
                    onClick={() => open(v.href)}
                    aria-current={here ? "page" : undefined}
                    data-pane={matchPaneView(v.href) ? "true" : undefined}
                    className={`${rowBase} ${here ? "bg-border-soft text-ink" : "text-ink-soft hover:bg-border-soft/60 hover:text-ink"}`}
                  >
                    <Icon className="h-4 w-4 shrink-0 text-ink-muted" aria-hidden />
                    <span className="truncate">{v.label}</span>
                    {!matchPaneView(v.href) && <ArrowUpRight className="ml-auto h-3 w-3 shrink-0 text-ink-faint" aria-label="opens its page" />}
                  </button>
                </li>
              );
            })}
          </ul>
          {more.length > 0 && (
            <details className="group" data-testid="rail-more-views">
              <summary
                className={`${rowBase} cursor-pointer list-none text-xs text-ink-muted hover:bg-border-soft/60 hover:text-ink [&::-webkit-details-marker]:hidden`}
              >
                <ChevronRight className="h-3.5 w-3.5 transition-transform group-open:rotate-90" aria-hidden />
                More views
                <span className="ml-auto font-mono tabular-nums">{more.reduce((n, g) => n + g.items.length, 0)}</span>
              </summary>
              <div className="mt-1 space-y-3 pb-1">
                {more.map((g) => (
                  <div key={g.intent}>
                    <div className="px-2 pb-0.5 font-mono text-[10px] uppercase tracking-wider text-ink-faint">{g.title}</div>
                    <ul className="space-y-0.5">
                      {g.items.map((v) => (
                        <li key={v.href}>
                          <button
                            type="button"
                            onClick={() => open(v.href)}
                            aria-current={openPath === v.href ? "page" : undefined}
                            className={`${rowBase} ${
                              openPath === v.href ? "bg-border-soft text-ink" : "text-ink-soft hover:bg-border-soft/60 hover:text-ink"
                            }`}
                          >
                            <span className="truncate pl-1">{v.label}</span>
                            {!matchPaneView(v.href) && <ArrowUpRight className="ml-auto h-3 w-3 shrink-0 text-ink-faint" aria-label="opens its page" />}
                          </button>
                        </li>
                      ))}
                    </ul>
                  </div>
                ))}
              </div>
            </details>
          )}
        </Section>
        <Section title="Your agents">
          {agents === null ? (
            <Loading />
          ) : agents.length === 0 ? (
            <p className="px-2 text-xs text-ink-muted">No agents you can run yet.</p>
          ) : (
            <ul className="space-y-0.5">
              {agents.slice(0, 8).map((a) => (
                <li key={a.id}>
                  <button
                    type="button"
                    onClick={() => onMention(a.name)}
                    title={`Mention @${a.name} in your message`}
                    className={`${rowBase} text-ink-soft hover:bg-border-soft/60`}
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

      <div className="shrink-0 space-y-0.5 border-t border-border p-2">
        <button
          type="button"
          onClick={() => window.dispatchEvent(new Event("astra:open-palette"))}
          className={`${rowBase} text-ink-soft hover:bg-border-soft/60 hover:text-ink`}
        >
          <Search className="h-4 w-4 shrink-0 text-ink-muted" aria-hidden /> Search
          <kbd className="ml-auto font-mono text-[10px] text-ink-muted">{shortcutLabel}</kbd>
        </button>
        <RoleSwitcher />
        {user && (
          <div className="flex items-center gap-2 px-2 py-1.5">
            <div className="min-w-0 flex-1">
              <div className="truncate text-sm text-ink">{user.full_name}</div>
              <div className="truncate text-[11px] capitalize text-ink-muted">{user.title || user.role}</div>
            </div>
            <button
              type="button"
              onClick={() => {
                clearSession();
                router.replace("/login");
              }}
              className="text-ink-muted hover:text-ink"
              title="Sign out"
              aria-label="Sign out"
            >
              <LogOut className="h-4 w-4" />
            </button>
          </div>
        )}
      </div>
    </nav>
  );
}
