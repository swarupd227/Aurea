"use client";

import { createContext, useCallback, useContext, useEffect, useState } from "react";
import { useRouter } from "next/navigation";
import { createThread, fetchThreads, sendMessageStream } from "./api";
import type { ComposerInsert } from "./components/Composer";
import type { ThreadSummary } from "./types";

interface WorkspaceContextValue {
  threads: ThreadSummary[];
  refetchThreads: () => void;
  composerInsert: ComposerInsert | null;
  mention: (name: string) => void;
  /** Create a thread, send `text` as the opening message, and navigate to it —
   * the same flow the home page's composer uses, shared here so the command
   * palette's "Ask Astra …" can do the identical thing from anywhere. */
  askAstra: (text: string) => Promise<void>;
}

const WorkspaceContext = createContext<WorkspaceContextValue | null>(null);

export function WorkspaceProvider({ children }: { children: React.ReactNode }) {
  const router = useRouter();
  const [threads, setThreads] = useState<ThreadSummary[]>([]);
  const [composerInsert, setComposerInsert] = useState<ComposerInsert | null>(null);

  const refetchThreads = useCallback(() => {
    fetchThreads()
      .then((d) => setThreads(d.threads))
      .catch(() => {});
  }, []);

  useEffect(() => {
    refetchThreads();
  }, [refetchThreads]);

  const mention = useCallback((name: string) => {
    setComposerInsert({ text: `@${name} `, nonce: Date.now() });
  }, []);

  const askAstra = useCallback(
    async (text: string) => {
      const thread = await createThread({ kind: "ask_astra", title: text.slice(0, 60) });
      router.push(`/workspace/${thread.id}`);
      refetchThreads();
      // Streamed in the background — the thread page picks up the persisted
      // result on load. Errors here aren't surfaced (there's no composer bound
      // to this call to show them in); a failed first message just leaves the
      // thread empty, which the thread page's own empty state already handles.
      await sendMessageStream(thread.id, text, () => {}).catch(() => {});
      refetchThreads();
    },
    [router, refetchThreads]
  );

  return (
    <WorkspaceContext.Provider value={{ threads, refetchThreads, composerInsert, mention, askAstra }}>
      {children}
    </WorkspaceContext.Provider>
  );
}

export function useWorkspace() {
  const ctx = useContext(WorkspaceContext);
  if (!ctx) throw new Error("useWorkspace must be used within WorkspaceProvider");
  return ctx;
}
