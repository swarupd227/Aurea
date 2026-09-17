"use client";

import { useState } from "react";
import { Spinner } from "@/components/ui";

export interface ConfirmationCardProps {
  pendingActionId: string;
  confirmationText: string;
  toolKey: string;
  isLoading?: boolean;
  onConfirm: (id: string) => Promise<void>;
  onReject: (id: string) => Promise<void>;
}

export function ConfirmationCard({
  pendingActionId,
  confirmationText,
  toolKey,
  isLoading = false,
  onConfirm,
  onReject,
}: ConfirmationCardProps) {
  const [state, setState] = useState<"idle" | "confirming" | "rejecting">("idle");

  const handleConfirm = async () => {
    setState("confirming");
    try {
      await onConfirm(pendingActionId);
    } finally {
      setState("idle");
    }
  };

  const handleReject = async () => {
    setState("rejecting");
    try {
      await onReject(pendingActionId);
    } finally {
      setState("idle");
    }
  };

  const isActing = state !== "idle" || isLoading;

  return (
    <div className="bg-surface border border-border rounded-lg p-4 space-y-3">
      <div className="flex items-start justify-between gap-3">
        <div className="flex-1">
          <p className="text-sm font-medium text-ink">
            {confirmationText}
          </p>
          <p className="text-xs text-ink-muted mt-1">
            Tool: <code className="bg-border px-2 py-0.5 rounded text-xs text-ink">{toolKey}</code>
          </p>
        </div>
      </div>

      <div className="flex gap-2 pt-2">
        <button
          onClick={handleConfirm}
          disabled={isActing}
          className="flex-1 flex items-center justify-center gap-2 px-3 py-2 rounded bg-accent text-ink font-medium text-sm hover:bg-yellow-400 disabled:opacity-50 disabled:cursor-not-allowed transition-colors"
        >
          {state === "confirming" && <Spinner className="w-3 h-3" />}
          Confirm
        </button>
        <button
          onClick={handleReject}
          disabled={isActing}
          className="flex-1 px-3 py-2 rounded bg-border text-ink font-medium text-sm hover:bg-border-soft disabled:opacity-50 disabled:cursor-not-allowed transition-colors"
        >
          Not now
        </button>
      </div>
    </div>
  );
}
