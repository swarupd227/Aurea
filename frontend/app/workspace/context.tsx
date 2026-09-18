"use client";

import { createContext, useCallback, useContext, useEffect, useState } from "react";
import { fetchThreads } from "./api";
import type { ComposerInsert } from "./components/Composer";
import type { ThreadSummary } from "./types";

interface WorkspaceContextValue {
  threads: ThreadSummary[];
  refetchThreads: () => void;
  composerInsert: ComposerInsert | null;
  mention: (name: string) => void;
}

const WorkspaceContext = createContext<WorkspaceContextValue | null>(null);

export function WorkspaceProvider({ children }: { children: React.ReactNode }) {
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

  return (
    <WorkspaceContext.Provider value={{ threads, refetchThreads, composerInsert, mention }}>
      {children}
    </WorkspaceContext.Provider>
  );
}

export function useWorkspace() {
  const ctx = useContext(WorkspaceContext);
  if (!ctx) throw new Error("useWorkspace must be used within WorkspaceProvider");
  return ctx;
}
