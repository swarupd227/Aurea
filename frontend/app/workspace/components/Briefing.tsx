"use client";

import { useEffect, useState } from "react";
import { ChevronRight } from "lucide-react";
import { fetchBriefing } from "../api";
import type { Briefing as BriefingData } from "../types";

export function Briefing({ onSend }: { onSend: (text: string) => void }) {
  const [data, setData] = useState<BriefingData | null>(null);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    fetchBriefing()
      .then(setData)
      .catch(() => setData(null))
      .finally(() => setLoading(false));
  }, []);

  if (loading) {
    return (
      <div className="mt-6 space-y-px overflow-hidden rounded-lg border border-border" aria-busy="true">
        {[0, 1, 2].map((i) => (
          <div key={i} className="h-12 animate-pulse bg-surface" />
        ))}
      </div>
    );
  }
  if (!data || data.rows.length === 0) return null;

  return (
    <section className="mt-6">
      <ul className="divide-y divide-border overflow-hidden rounded-lg border border-border bg-surface">
        {data.rows.map((row) => (
          <li key={row.id}>
            <button
              type="button"
              onClick={() => onSend(row.prompt)}
              className="group flex w-full items-center gap-3 px-3 py-2.5 text-left hover:bg-border-soft transition-colors"
              title={row.prompt}
            >
              <span className="w-10 shrink-0 text-right font-mono text-lg tabular-nums leading-none">
                <span className={row.tone === "attention" ? "text-accent" : "text-ink"}>{row.count}</span>
              </span>
              <span className="min-w-0 flex-1">
                <span className="block text-sm text-ink">{row.label}</span>
                {row.detail && <span className="block truncate text-xs text-ink-muted">{row.detail}</span>}
              </span>
              <ChevronRight className="h-4 w-4 shrink-0 text-ink-muted group-hover:text-ink" aria-hidden />
            </button>
          </li>
        ))}
      </ul>
      {data.not_shown.map((line) => (
        <p key={line} className="mt-2 text-xs text-ink-muted">
          {line}
        </p>
      ))}
    </section>
  );
}
