"use client";
import { useEffect, ReactNode } from "react";

export default function PageHeader({
  title,
  sub,
  actions,
  back,
}: {
  title: string;
  sub?: string;
  actions?: ReactNode;
  back?: ReactNode;
}) {
  useEffect(() => {
    document.title = `${title} — Aurea`;
  }, [title]);

  return (
    <div className="mb-6">
      {back && <div className="mb-2">{back}</div>}
      <div className="flex items-start justify-between gap-4">
        <div>
          <h1 className="text-2xl font-semibold text-ink tracking-tight">{title}</h1>
          {sub && <p className="text-sm text-ink-muted mt-1 max-w-2xl">{sub}</p>}
        </div>
        {actions && <div className="flex items-center gap-2 shrink-0">{actions}</div>}
      </div>
    </div>
  );
}
