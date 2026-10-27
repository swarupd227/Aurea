"use client";

import { createContext, useCallback, useContext, useEffect, useState } from "react";
import { useRouter } from "next/navigation";
import { fetchThreads } from "./api";
import { startConversation } from "./lib/start";
import type { ComposerInsert } from "./components/Composer";
import type { ThreadSummary } from "./types";

interface WorkspaceContextValue {
  threads: ThreadSummary[];
  refetchThreads: () => void;
  composerInsert: ComposerInsert | null;
  mention: (name: string) => void;
  /** Create a thread, open it, and have its page send `text` as the opening message — the same
   * flow the home composer and the command palette's "Ask Astra …" share. */
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
      // The thread page sends the first message so the turn streams in view.
      const path = await startConversation(text);
      router.push(path);
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
