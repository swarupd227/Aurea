"use client";

import type { ReactNode } from "react";
import { Card } from "@/components/ui";
import { titleCase } from "@/lib/format";
import { useViewNav } from "../ViewNav";
import { chip } from "../ViewHeader";

/** Beside the conversation a change is a sentence the matching tool answers, behind its Confirm card;
 * on the classic page (null here) the controls call the API themselves. */
export function useAsk(): ((text: string) => void) | null {
  const nav = useViewNav();
  return nav.inPane && nav.say ? nav.say : null;
}

/** What stays on the classic page: a link handed to the admin, sessions ended, a secret typed in. */
export function PageOnly({ label = "on the page" }: { label?: string }) {
  return <span className={`${chip} text-ink-muted`} data-testid="admin-page-only">{label}</span>;
}

export function ReasonInput({ value, onChange, className = "" }: { value: string; onChange: (v: string) => void; className?: string }) {
  return (
    <input
      className={`input py-1 text-xs ${className || "w-44"}`}
      placeholder="Reason"
      aria-label="Reason"
      value={value}
      onChange={(e) => onChange(e.target.value)}
      data-testid="admin-reason"
    />
  );
}

/** "; reason: …" for a sentence, or nothing. */
export const because = (reason: string) => (reason.trim() ? `; reason: ${reason.trim()}` : "");

export const q = (s: string | null | undefined) => `"${(s ?? "").trim()}"`;

export const TH = "px-4 py-2 text-left text-[10px] font-medium uppercase tracking-wide text-ink-muted";
export const TD = "px-4 py-2";

/** A table in a card, scrolling sideways when the pane is narrow. */
export function TableCard({ children, testid }: { children: ReactNode; testid?: string }) {
  return (
    <Card className="overflow-hidden p-0">
      <div className="overflow-x-auto">
        <table className="w-full text-sm" data-testid={testid}>{children}</table>
      </div>
    </Card>
  );
}

export function ActiveChip({ active, on = "Active", off = "Inactive" }: { active: boolean; on?: string; off?: string }) {
  return active ? <span className="chip bg-ok-bg text-ok">{on}</span> : <span className={`${chip} text-ink-muted`}>{off}</span>;
}

export function FormError({ children }: { children: ReactNode }) {
  return <div className="mb-2 chip bg-crit-bg text-crit" role="alert">{children}</div>;
}

export function Saved() {
  return <span className="chip bg-ok-bg text-ok" role="status">Saved</span>;
}

export const tierWord = (t: string) => `Tier ${String(t).replace("tier_", "")}`;

// ── Connectors: the same sentences from the admin tab and the connectors view ──

export const connectorEnabled = (c: any) => c.status !== "disabled";

export const connectorSay = {
  live: (c: any) => `Switch the connector ${q(c.display_name)} to ${c.use_mock ? "live" : "mock"} data`,
  enabled: (c: any) => `${connectorEnabled(c) ? "Disable" : "Enable"} the connector ${q(c.display_name)}`,
  sync: (c: any) => `Sync the connector ${q(c.display_name)} now`,
  cron: (c: any, cron: string) =>
    cron.trim() ? `Set the connector ${q(c.display_name)} to sync on the schedule "${cron.trim()}"` : `Clear the sync schedule of the connector ${q(c.display_name)}`,
  add: (p: any) => `Add the connector ${q(p.display_name)} (${p.key}) for ${titleCase(p.domain).toLowerCase()}`,
};

export const CONNECTOR_STATUS_TONE: Record<string, string> = {
  connected: "bg-ok-bg text-ok",
  mock: "bg-warn-bg text-warn",
  error: "bg-crit-bg text-crit",
  configured: "bg-info-bg text-info",
  disabled: "bg-border-soft text-ink-muted",
};
