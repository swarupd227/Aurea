"use client";

import { createContext, useCallback, useContext, useEffect, useMemo, useRef, useState } from "react";
import { usePathname, useRouter, useSearchParams } from "next/navigation";
import { getUser } from "@/lib/api";
import { matchPaneView, withView, type PaneMatch } from "@/lib/paneViews";
import { fetchThreads } from "./api";
import { startConversation } from "./lib/start";
import type { ComposerInsert } from "./components/Composer";
import type { ThreadSummary } from "./types";

/** What "Ask about this" puts in the composer: the open view's route and its entity id - never
 * the data on it. The model still has to call a tool to say anything about it (rule 2). */
export interface ViewContextChip {
  route: string;
  label: string;
  id?: string;
}

/** The sentence a context chip adds in front of a message. */
export function contextPrefix(chip: ViewContextChip): string {
  return `[Open view: ${chip.label} ${chip.route}]`;
}

interface WorkspaceContextValue {
  threads: ThreadSummary[];
  /** False until the conversation list has been fetched once - so "None yet" is never a guess. */
  threadsLoaded: boolean;
  refetchThreads: () => void;
  composerInsert: ComposerInsert | null;
  mention: (name: string) => void;
  /** Put text in the composer without sending it. */
  insertInComposer: (text: string) => void;
  /** Create a thread, open it, and have its page send `text` as the opening message — the same
   * flow the home composer and the command palette's "Ask Astra …" share. The pane stays open. */
  askAstra: (text: string) => Promise<void>;
  /** Hand a sentence to the conversation in view: the open thread sends it (or, if it is busy,
   * puts it in the composer); with no thread open it starts one. */
  say: (text: string) => void;
  /** The open thread registers how it sends; null when it unmounts. */
  registerSender: (send: ((text: string) => void) | null) => void;
  /** Go to a thread (or home, for null), keeping the work pane as it is. */
  goThread: (id: string | null) => void;

  // ── The work pane. Its state is `?view=` on the URL; everything here derives from it.
  /** The open pane view, or null when the pane is closed. Only PANE_VIEWS routes this role may see. */
  pane: PaneMatch | null;
  /** Open `route` in the pane. `within`: navigation inside the pane (adds a breadcrumb) rather
   * than from outside it (starts a new trail). A route that is not a pane view goes to its page. */
  openView: (route: string, opts?: { within?: boolean }) => void;
  closeView: () => void;
  /** The pane's breadcrumb trail, oldest first, ending at the open view. */
  trail: string[];
  /** What each open route calls itself, once its view has loaded (a client's name). */
  titles: Record<string, string>;
  setViewTitle: (route: string, title: string | null) => void;
  focus: boolean;
  setFocus: (focus: boolean) => void;
  /** Below md the pane is a sheet over the conversation; hidden, the view stays open behind it. */
  sheetHidden: boolean;
  setSheetHidden: (hidden: boolean) => void;
  contextChip: ViewContextChip | null;
  setContextChip: (chip: ViewContextChip | null) => void;
}

const WorkspaceContext = createContext<WorkspaceContextValue | null>(null);

export function WorkspaceProvider({ children }: { children: React.ReactNode }) {
  const router = useRouter();
  const pathname = usePathname();
  const search = useSearchParams();
  const [threads, setThreads] = useState<ThreadSummary[]>([]);
  const [threadsLoaded, setThreadsLoaded] = useState(false);
  const [composerInsert, setComposerInsert] = useState<ComposerInsert | null>(null);
  const [role, setRole] = useState<string>("");

  useEffect(() => setRole(getUser()?.role ?? ""), []);

  const refetchThreads = useCallback(() => {
    fetchThreads()
      .then((d) => setThreads(d.threads))
      .catch(() => {})
      .finally(() => setThreadsLoaded(true));
  }, []);

  useEffect(() => {
    refetchThreads();
  }, [refetchThreads]);

  // Something changed - a decision in the pane, a confirmed action in the thread - so what is
  // waiting, and which threads moved, may differ now.
  useEffect(() => {
    window.addEventListener("astra:changed", refetchThreads);
    return () => window.removeEventListener("astra:changed", refetchThreads);
  }, [refetchThreads]);

  // ── Pane
  const viewRoute = search.get("view");
  const pane = useMemo(() => (role ? matchPaneView(viewRoute, role) : null), [viewRoute, role]);
  const paneRoute = pane?.route ?? null;
  const paneRef = useRef<string | null>(paneRoute);
  paneRef.current = paneRoute;

  const [trail, setTrail] = useState<string[]>([]);
  const nextWithin = useRef(false);
  useEffect(() => {
    const within = nextWithin.current;
    nextWithin.current = false;
    setTrail((prev) => {
      if (!paneRoute) return [];
      const at = prev.indexOf(paneRoute);
      if (at >= 0) return prev.slice(0, at + 1); // back, or a breadcrumb
      return within ? [...prev, paneRoute] : [paneRoute];
    });
  }, [paneRoute]);

  const [titles, setTitles] = useState<Record<string, string>>({});
  const setViewTitle = useCallback((route: string, title: string | null) => {
    setTitles((prev) => {
      if (!title) {
        if (!(route in prev)) return prev;
        const { [route]: _, ...rest } = prev;
        return rest;
      }
      return prev[route] === title ? prev : { ...prev, [route]: title };
    });
  }, []);

  const [focus, setFocus] = useState(false);
  const [sheetHidden, setSheetHidden] = useState(false);
  const [contextChip, setContextChip] = useState<ViewContextChip | null>(null);

  const openView = useCallback(
    (route: string, opts?: { within?: boolean }) => {
      if (!matchPaneView(route, role || getUser()?.role)) {
        router.push(route);
        return;
      }
      nextWithin.current = !!opts?.within;
      setSheetHidden(false);
      router.push(withView(pathname, route));
    },
    [router, pathname, role]
  );

  const closeView = useCallback(() => {
    setFocus(false);
    setSheetHidden(false);
    router.push(pathname);
  }, [router, pathname]);

  const goThread = useCallback(
    (id: string | null) => router.push(withView(id ? `/workspace/${id}` : "/workspace", paneRef.current)),
    [router]
  );

  const mention = useCallback((name: string) => {
    setComposerInsert({ text: `@${name} `, nonce: Date.now() });
  }, []);
  const insertInComposer = useCallback((text: string) => {
    setComposerInsert({ text, nonce: Date.now() });
  }, []);

  const askAstra = useCallback(
    async (text: string) => {
      // The thread page sends the first message so the turn streams in view.
      const path = await startConversation(text);
      router.push(withView(path, paneRef.current));
      refetchThreads();
    },
    [router, refetchThreads]
  );

  const sender = useRef<((text: string) => void) | null>(null);
  const registerSender = useCallback((send: ((text: string) => void) | null) => {
    sender.current = send;
  }, []);
  const say = useCallback(
    (text: string) => {
      // On a phone the conversation is under the sheet: show it, so the turn is seen streaming.
      setSheetHidden(true);
      if (sender.current) sender.current(text);
      else void askAstra(text).catch(() => insertInComposer(text));
    },
    [askAstra, insertInComposer]
  );

  return (
    <WorkspaceContext.Provider
      value={{
        threads, threadsLoaded, refetchThreads, composerInsert, mention, insertInComposer, askAstra, say, registerSender, goThread,
        pane, openView, closeView, trail, titles, setViewTitle, focus, setFocus, sheetHidden, setSheetHidden,
        contextChip, setContextChip,
      }}
    >
      {children}
    </WorkspaceContext.Provider>
  );
}

export function useWorkspace() {
  const ctx = useContext(WorkspaceContext);
  if (!ctx) throw new Error("useWorkspace must be used within WorkspaceProvider");
  return ctx;
}

/** The workspace, when the caller is inside it (the palette and message cards also render on
 * classic pages, where there is no pane to open). */
export function useWorkspaceOptional() {
  return useContext(WorkspaceContext);
}
