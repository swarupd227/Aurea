"use client";

import type { ReactNode } from "react";
import { useViewNav, useViewTitle } from "./ViewNav";

/** A view's title row. On a page it is the page's heading; in the work pane the pane's own header
 * already carries the title (and breadcrumbs), so only the figures and actions are shown. No
 * subtitle prose either way: a view is figures, tables and chips (CLAUDE.md rule 5). */
export function ViewHeader({
  title,
  meta,
  actions,
}: {
  title: string;
  /** Chips beside the title: counts, segment, status. */
  meta?: ReactNode;
  actions?: ReactNode;
}) {
  const { inPane } = useViewNav();
  useViewTitle(title);
  if (inPane && !meta && !actions) return null;
  return (
    // Wraps rather than squeezes: at the pane's width the actions drop under the title and chips.
    <div className="mb-5 flex flex-wrap items-start justify-between gap-3">
      <div className="min-w-[12rem] flex-1">
        {!inPane && <h1 className="text-2xl font-semibold tracking-tight text-ink">{title}</h1>}
        {meta && <div className={`flex flex-wrap items-center gap-1.5 ${inPane ? "" : "mt-2"}`}>{meta}</div>}
      </div>
      {actions && <div className="flex flex-wrap items-center gap-2">{actions}</div>}
    </div>
  );
}

export const chip = "chip bg-border-soft text-ink-soft";
