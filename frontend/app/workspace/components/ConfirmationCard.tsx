"use client";

import { useState } from "react";
import { Check, X } from "lucide-react";
import type { PendingActionRef } from "../types";

/**
 * A change waiting for the user. Confirm runs exactly what the card shows
 * (the server froze it when the turn paused); Not now changes nothing.
 */
export function ConfirmationCard({
  action,
  onConfirm,
  onReject,
}: {
  action: PendingActionRef;
  onConfirm: (id: string) => Promise<void>;
  onReject: (id: string) => Promise<void>;
}) {
  const [busy, setBusy] = useState(false);
  const decided = action.decision;

  const act = async (fn: (id: string) => Promise<void>) => {
    setBusy(true);
    try {
      await fn(action.id);
    } finally {
      setBusy(false);
    }
  };

  return (
    <div
      className={`rounded-lg border bg-surface p-4 ${
        !decided ? "border-accent/60" : "border-border"
      }`}
    >
      <div className="mb-1 font-mono text-[11px] uppercase tracking-wider text-ink-muted">
        Confirm this change
      </div>
      <div className="font-medium text-ink">{action.confirmation_text}</div>
      <div className="mt-1 font-mono text-[11px] text-ink-muted">
        <code className="bg-border-soft px-1.5 py-0.5 rounded">{action.tool_key}</code>
      </div>

      <div className="mt-4 flex items-center gap-2">
        {decided ? (
          <span
            className={`inline-flex items-center gap-1.5 font-mono text-xs ${
              decided === "confirmed" ? "text-ok" : "text-ink-muted"
            }`}
          >
            {decided === "confirmed" ? <Check className="h-3.5 w-3.5" /> : <X className="h-3.5 w-3.5" />}
            {decided === "confirmed" ? "Confirmed" : "Not now"}
          </span>
        ) : (
          <>
            <button
              type="button"
              onClick={() => act(onConfirm)}
              disabled={busy}
              className="bg-accent text-ink px-3 py-1.5 rounded font-medium text-sm hover:bg-yellow-400 disabled:opacity-50 transition-colors"
            >
              Confirm
            </button>
            <button
              type="button"
              onClick={() => act(onReject)}
              disabled={busy}
              className="text-ink-muted px-3 py-1.5 rounded font-medium text-sm hover:bg-border-soft disabled:opacity-50 transition-colors"
            >
              Not now
            </button>
          </>
        )}
      </div>
    </div>
  );
}
