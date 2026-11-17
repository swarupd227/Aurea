"use client";
import { createContext, useCallback, useContext, useState, ReactNode } from "react";
import { Zap, X, Minus } from "lucide-react";
import AgentStream from "./AgentStream";

type RunSpec = { id: string; agentKey: string; subjectType?: string; subjectId?: string; label?: string };
type Runner = { run: (s: Omit<RunSpec, "id">) => void };

const RunnerCtx = createContext<Runner | null>(null);

/** Trigger an agent run from anywhere; the global console opens and streams its steps. */
export function useAgentRunner(): Runner {
  return useContext(RunnerCtx) || { run: () => {} };
}

export function AgentConsoleProvider({ children }: { children: ReactNode }) {
  const [runs, setRuns] = useState<RunSpec[]>([]);
  const [open, setOpen] = useState(true);

  const run = useCallback((s: Omit<RunSpec, "id">) => {
    const id = `${Date.now()}-${Math.random().toString(36).slice(2, 7)}`;
    setRuns((prev) => [{ id, ...s }, ...prev].slice(0, 8));
    setOpen(true);
  }, []);

  const dismiss = (id: string) => setRuns((prev) => prev.filter((r) => r.id !== id));

  return (
    <RunnerCtx.Provider value={{ run }}>
      {children}

      {runs.length > 0 &&
        (open ? (
          <div
            className="fixed bottom-4 right-4 z-50 w-[460px] max-w-[calc(100vw-2rem)] rounded-2xl bg-surface border border-border shadow-lift overflow-hidden flex flex-col"
            style={{ maxHeight: "82vh" }}
          >
            <div className="px-4 py-2.5 bg-surface text-ink border-b border-border flex items-center gap-2 shrink-0">
              <Zap size={15} className="text-accent" aria-hidden />
              <span className="text-sm font-semibold">Workforce console</span>
              <span className="inline-flex items-center gap-1 text-[11px] text-ink-muted">
                <span className="h-1.5 w-1.5 rounded-full bg-accent agent-working" /> {runs.length} run{runs.length > 1 ? "s" : ""}
              </span>
              <div className="ml-auto flex items-center gap-1">
                <button onClick={() => setOpen(false)} className="p-1 hover:bg-border-soft rounded" title="Minimise" aria-label="Minimise"><Minus size={15} /></button>
                <button onClick={() => setRuns([])} className="p-1 hover:bg-border-soft rounded" title="Close all" aria-label="Close all"><X size={15} /></button>
              </div>
            </div>
            <div className="overflow-y-auto p-3 space-y-3 bg-paper">
              {runs.map((r) => (
                <div key={r.id} className="relative">
                  <button
                    onClick={() => dismiss(r.id)}
                    className="absolute -top-1.5 -right-1.5 z-10 h-5 w-5 rounded-full bg-surface border border-border text-ink-muted hover:text-crit flex items-center justify-center shadow-sm"
                    title="Dismiss"
                  >
                    <X size={11} />
                  </button>
                  <AgentStream agentKey={r.agentKey} subjectType={r.subjectType} subjectId={r.subjectId} label={r.label} />
                </div>
              ))}
            </div>
          </div>
        ) : (
          <button onClick={() => setOpen(true)} className="fixed bottom-4 right-4 z-50 btn-primary shadow-lift">
            <Zap size={16} aria-hidden /> Workforce console ({runs.length})
          </button>
        ))}
    </RunnerCtx.Provider>
  );
}
