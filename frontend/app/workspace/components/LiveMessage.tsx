import { Check, CircleAlert, Loader2 } from "lucide-react";
import { Markdown } from "./Markdown";
import type { LiveTurn } from "../types";

function toolLabel(key: string) {
  return key.replace(/_/g, " ");
}

function Steps({ steps }: { steps: LiveTurn["steps"] }) {
  if (steps.length === 0) return null;
  return (
    <ol className="space-y-1 font-mono text-xs" aria-label="Steps">
      {steps.map((s, i) => (
        <li key={i} className="flex min-w-0 items-center gap-2 text-ink-muted">
          {s.state === "running" ? (
            <Loader2 className="h-3 w-3 shrink-0 animate-spin text-accent" aria-label="running" />
          ) : s.state === "ok" ? (
            <Check className="h-3 w-3 shrink-0 text-ok" aria-label="done" />
          ) : (
            <CircleAlert className="h-3 w-3 shrink-0 text-crit" aria-label="failed" />
          )}
          <span className="text-ink-soft">{toolLabel(s.tool_key)}</span>
        </li>
      ))}
    </ol>
  );
}

/** Astra's turn while it's still streaming — accumulating text plus any tool steps,
 * rendered distinct from finalized messages until the turn lands and the thread reloads. */
export function LiveMessage({ live }: { live: LiveTurn }) {
  return (
    <div className="flex gap-3" aria-live="polite">
      <div
        aria-hidden
        className="mt-0.5 flex h-6 w-6 shrink-0 items-center justify-center rounded bg-accent font-mono text-[11px] font-bold text-ink agent-working"
      >
        A
      </div>
      <div className="min-w-0 flex-1 space-y-2">
        {live.text ? (
          <Markdown text={live.text} className="text-sm text-ink" />
        ) : (
          <div className="flex items-center gap-2 text-sm text-ink-muted">
            <span aria-hidden className="inline-block h-1.5 w-1.5 animate-pulse rounded-full bg-accent" />
            Working
          </div>
        )}
        <Steps steps={live.steps} />
      </div>
    </div>
  );
}
