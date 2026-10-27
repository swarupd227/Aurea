"use client";

import { Suspense, useCallback, useEffect, useRef, useState } from "react";
import { usePathname, useRouter } from "next/navigation";
import { PanelRight, Plus, Search } from "lucide-react";
import { getToken, getUser } from "@/lib/api";
import { hasWorkspace } from "@/lib/nav";
import { roleLanding } from "@/lib/roles";
import { CommandPalette } from "@/components/CommandPalette";
import { WorkspaceProvider, useWorkspace } from "./context";
import { Rail } from "./components/Rail";
import { WorkPane } from "./components/WorkPane";

// The work pane's width is the viewer's own: remembered in this browser, never shared.
const WIDTH_KEY = "astra:pane-width";
const RAIL = 260;
const MIN_PANE = 420;
const MIN_CONVERSATION = 340;

function readWidth(): number | null {
  try {
    const v = Number(localStorage.getItem(WIDTH_KEY));
    return Number.isFinite(v) && v > 0 ? v : null;
  } catch {
    return null;
  }
}
function saveWidth(w: number) {
  try {
    localStorage.setItem(WIDTH_KEY, String(Math.round(w)));
  } catch {
    // Storage blocked (private window): the width just isn't remembered.
  }
}
function clampWidth(w: number): number {
  const max = Math.max(MIN_PANE, window.innerWidth - RAIL - MIN_CONVERSATION);
  return Math.min(Math.max(w, MIN_PANE), max);
}
function defaultWidth(): number {
  return clampWidth(Math.round((window.innerWidth - RAIL) * 0.5));
}

/** Drag (or arrow keys) to size the pane against the conversation; double-click resets it. */
function ResizeHandle({ width, onChange, onCommit }: { width: number; onChange: (w: number) => void; onCommit: (w: number) => void }) {
  const dragging = useRef(false);
  const latest = useRef(width);
  latest.current = width;

  const onPointerDown = (e: React.PointerEvent) => {
    e.preventDefault();
    dragging.current = true;
    const move = (ev: PointerEvent) => {
      if (dragging.current) onChange(clampWidth(window.innerWidth - ev.clientX));
    };
    const up = () => {
      dragging.current = false;
      window.removeEventListener("pointermove", move);
      window.removeEventListener("pointerup", up);
      document.body.style.removeProperty("cursor");
      document.body.style.removeProperty("user-select");
      onCommit(latest.current);
    };
    document.body.style.cursor = "col-resize";
    document.body.style.userSelect = "none";
    window.addEventListener("pointermove", move);
    window.addEventListener("pointerup", up);
  };

  return (
    <div
      role="separator"
      aria-orientation="vertical"
      aria-label="Resize the view against the conversation"
      aria-valuenow={Math.round(width)}
      aria-valuemin={MIN_PANE}
      tabIndex={0}
      data-testid="pane-resize"
      onPointerDown={onPointerDown}
      onDoubleClick={() => {
        const w = defaultWidth();
        onChange(w);
        onCommit(w);
      }}
      onKeyDown={(e) => {
        if (e.key !== "ArrowLeft" && e.key !== "ArrowRight") return;
        e.preventDefault();
        const w = clampWidth(width + (e.key === "ArrowLeft" ? 32 : -32));
        onChange(w);
        onCommit(w);
      }}
      className="group relative hidden w-1 shrink-0 cursor-col-resize bg-border transition-colors hover:bg-accent/60 focus-visible:bg-accent focus-visible:outline-none lg:block"
    >
      <span aria-hidden className="absolute inset-y-0 -left-1.5 -right-1.5" />
    </div>
  );
}

function WorkspaceShell({ children }: { children: React.ReactNode }) {
  const pathname = usePathname();
  const { threads, threadsLoaded, mention, askAstra, goThread, pane, openView, focus, sheetHidden, setSheetHidden } = useWorkspace();
  const [width, setWidth] = useState<number | null>(null);

  useEffect(() => {
    setWidth(clampWidth(readWidth() ?? defaultWidth()));
    const onResize = () => setWidth((w) => (w ? clampWidth(w) : w));
    window.addEventListener("resize", onResize);
    return () => window.removeEventListener("resize", onResize);
  }, []);
  const commit = useCallback((w: number) => saveWidth(w), []);

  const threadId = pathname.startsWith("/workspace/") ? pathname.slice("/workspace/".length) : null;
  const activeThread = threadId ? threads.find((t) => t.id === threadId) : null;

  const paneOpen = !!pane;
  const paneStyle = { "--pane-w": `${width ?? 560}px` } as React.CSSProperties;

  return (
    <div className="flex h-screen w-full overflow-hidden bg-paper text-ink">
      <div className="hidden w-[260px] shrink-0 border-r border-border md:block">
        <Rail
          threads={threads}
          threadsLoaded={threadsLoaded}
          activeId={threadId}
          onSelect={(id) => goThread(id)}
          onNew={() => goThread(null)}
          onMention={mention}
          onAsk={(text) => void askAstra(text)}
          onOpenView={(route) => openView(route)}
          openRoute={pane?.route ?? null}
          view={threadId ? "thread" : "home"}
        />
      </div>

      <main
        className={`flex min-h-0 min-w-0 flex-1 flex-col ${paneOpen && focus ? "lg:hidden" : ""} ${
          paneOpen && !focus ? "lg:min-w-[340px]" : ""
        }`}
      >
        <header className="flex h-12 shrink-0 items-center gap-2 border-b border-border px-4">
          <h1 className="min-w-0 flex-1 truncate text-sm font-medium text-ink">
            {threadId ? activeThread?.title || "Conversation" : "New conversation"}
          </h1>
          {paneOpen && sheetHidden && (
            <button
              onClick={() => setSheetHidden(false)}
              data-testid="pane-reopen"
              className="flex items-center gap-1 rounded px-2 py-1 text-xs text-ink-muted hover:bg-border-soft lg:hidden"
            >
              <PanelRight className="h-3.5 w-3.5" /> View
            </button>
          )}
          <button
            onClick={() => window.dispatchEvent(new Event("astra:open-palette"))}
            aria-label="Search and ask"
            className="flex items-center gap-1 rounded px-2 py-1 text-xs text-ink-muted hover:bg-border-soft md:hidden"
          >
            <Search className="h-3.5 w-3.5" />
          </button>
          <button
            onClick={() => goThread(null)}
            className="flex items-center gap-1 rounded px-2 py-1 text-xs text-ink-muted hover:bg-border-soft md:hidden"
          >
            <Plus className="h-3.5 w-3.5" /> New
          </button>
        </header>
        <div className="min-h-0 flex-1">{children}</div>
      </main>

      {pane && (
        <>
          {!focus && width !== null && (
            <ResizeHandle width={width} onChange={setWidth} onCommit={commit} />
          )}
          {/* From lg: a column beside the conversation (or over it, in focus). Below lg there is no
              room for both: a sheet over the conversation (full screen below md, beside the rail
              from md), with a way back to it that leaves the view open. */}
          <aside
            style={paneStyle}
            className={`fixed inset-0 z-40 min-h-0 bg-paper md:left-[260px] md:border-l md:border-border lg:static lg:z-auto lg:border-l-0 ${
              sheetHidden ? "hidden lg:block" : "block"
            } ${focus ? "lg:min-w-0 lg:flex-1" : "lg:w-[var(--pane-w)] lg:shrink-0"}`}
          >
            <WorkPane pane={pane} />
          </aside>
        </>
      )}

      <CommandPalette />
    </div>
  );
}

export default function WorkspaceLayout({ children }: { children: React.ReactNode }) {
  const router = useRouter();
  const [ready, setReady] = useState(false);

  useEffect(() => {
    if (!getToken()) {
      router.replace("/login");
      return;
    }
    // Clients and the platform owner hold no conversation tools; send them to their own page.
    const role = getUser()?.role;
    if (role && !hasWorkspace(role)) {
      router.replace(roleLanding(role));
      return;
    }
    setReady(true);
  }, [router]);

  if (!ready) return <div className="h-screen bg-paper" />;

  // useSearchParams (the pane's ?view=) needs a Suspense boundary to build.
  return (
    <Suspense fallback={<div className="h-screen bg-paper" />}>
      <WorkspaceProvider>
        <WorkspaceShell>{children}</WorkspaceShell>
      </WorkspaceProvider>
    </Suspense>
  );
}
