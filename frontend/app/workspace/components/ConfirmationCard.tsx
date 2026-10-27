"use client";

import { useState } from "react";
import { Check, X } from "lucide-react";
import type { PendingActionRef, RunPreview } from "../types";

const ROLE_NAMES: Record<string, string> = {
  adviser: "adviser",
  portfolio_team: "portfolio team",
  operations: "operations",
  branch_leader: "branch leader",
  compliance: "compliance",
  paraplanner: "paraplanner",
  research_cio: "research",
};

function Row({ label, children, testId }: { label: string; children: React.ReactNode; testId?: string }) {
  return (
    <div className="flex items-baseline gap-3 py-1" data-testid={testId}>
      <dt className="w-28 shrink-0 font-mono text-[11px] uppercase tracking-wider text-ink-muted">{label}</dt>
      <dd className="min-w-0 flex-1 text-sm text-ink">{children}</dd>
    </div>
  );
}

function Chip({ children, tone = "text-ink-muted" }: { children: React.ReactNode; tone?: string }) {
  return <span className={`mr-1.5 inline-block rounded border border-border px-1.5 py-0.5 font-mono text-[11px] ${tone}`}>{children}</span>;
}

/** What a run's Confirm would start. Three controls, three rows, never merged: confirming only
 * starts the run; the tier is what the agent may then do on its own; decision rights are who may
 * approve what it proposes. */
function RunPreviewBlock({ preview }: { preview: RunPreview }) {
  const { autonomy, decision } = preview;
  const deciders = decision.any_staff ? "any member of staff" : decision.roles.map((r) => ROLE_NAMES[r] ?? r).join(", ");
  return (
    <div className="mt-3 rounded border border-border-soft" data-testid="run-preview">
      <div className="border-b border-border-soft px-3 py-2">
        <div className="text-sm font-medium text-ink" data-testid="run-preview-agent">{preview.agent_name}</div>
        <div className="text-xs text-ink-muted">{preview.subject_label ?? "Across the book"}</div>
        {preview.does && <div className="mt-1 text-xs text-ink-soft">{preview.does}</div>}
      </div>
      <dl className="px-3 py-1.5">
        <Row label="Confirming" testId="run-preview-confirms">
          <Chip>starts the run only</Chip>
          <Chip>approves nothing</Chip>
        </Row>
        <Row label="Autonomy tier" testId="run-preview-tier">
          <Chip tone={autonomy.acts_on_its_own ? "text-warn" : "text-ink-muted"}>{autonomy.label}</Chip>
          {autonomy.held_to_proposal ? (
            <Chip>held to a proposal</Chip>
          ) : autonomy.acts_on_its_own ? (
            <Chip tone="text-warn">may act on its own</Chip>
          ) : (
            <Chip>a person decides</Chip>
          )}
        </Row>
        <Row label="Decision rights" testId="run-preview-rights">
          <span>{deciders}</span>{" "}
          <Chip tone={decision.you_may_decide ? "text-ok" : "text-ink-muted"}>
            {decision.you_may_decide ? "you may decide" : "not yours to decide"}
          </Chip>
        </Row>
      </dl>
    </div>
  );
}

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
  onConfirm: (id: string, seenTier?: string) => Promise<void>;
  onReject: (id: string) => Promise<void>;
}) {
  const [busy, setBusy] = useState(false);
  const decided = action.decision;
  const preview = action.preview && "agent_key" in action.preview ? (action.preview as RunPreview) : null;
  const unavailable = action.preview && "unavailable" in action.preview && action.preview.unavailable ? action.preview.unavailable : null;

  const act = async (fn: () => Promise<void>) => {
    setBusy(true);
    try {
      await fn();
    } finally {
      setBusy(false);
    }
  };

  return (
    <div
      className={`rounded-lg border bg-surface p-4 ${
        !decided ? "border-accent/60" : "border-border"
      }`}
      data-testid={action.tool_key === "run_agent" ? "run-confirmation" : undefined}
    >
      <div className="mb-1 font-mono text-[11px] uppercase tracking-wider text-ink-muted">
        {preview ? "Confirm this run" : "Confirm this change"}
      </div>
      <div className="font-medium text-ink">{action.confirmation_text}</div>
      {preview && <RunPreviewBlock preview={preview} />}
      {unavailable && (
        <div role="alert" className="mt-3 rounded border border-warn/40 px-3 py-2 text-sm text-ink" data-testid="run-unavailable">
          {unavailable}
        </div>
      )}
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
              onClick={() => act(() => onConfirm(action.id, preview?.autonomy.tier))}
              disabled={busy || !!unavailable}
              data-testid="confirm-action"
              className="bg-accent text-black px-3 py-1.5 rounded font-medium text-sm hover:bg-yellow-400 disabled:opacity-50 transition-colors"
            >
              {preview ? "Start the run" : "Confirm"}
            </button>
            <button
              type="button"
              onClick={() => act(() => onReject(action.id))}
              disabled={busy}
              data-testid="reject-action"
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
