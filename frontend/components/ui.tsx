"use client";
import { ReactNode } from "react";
import Link from "next/link";
import { ChevronRight } from "lucide-react";
import { titleCase } from "@/lib/format";

export function Card({ children, className = "" }: { children: ReactNode; className?: string }) {
  return <div className={`card p-5 ${className}`}>{children}</div>;
}

export function SectionTitle({ children, sub }: { children: ReactNode; sub?: string }) {
  return (
    <div className="mb-4">
      <h2 className="text-lg font-semibold text-ink">{children}</h2>
      {sub && <p className="text-sm text-ink-muted mt-0.5">{sub}</p>}
    </div>
  );
}

export function StatTile({
  label,
  value,
  hint,
  accent,
}: {
  label: string;
  value: ReactNode;
  hint?: string;
  accent?: "gold" | "positive" | "critical";
}) {
  const color =
    accent === "positive" ? "text-positive"
    : accent === "critical" ? "text-critical"
    : accent === "gold" ? "text-gold-dark"
    : "text-ink";
  return (
    <div className="card p-4">
      <div className="tile-label">{label}</div>
      <div className={`text-2xl font-semibold mt-1 ${color}`}>{value}</div>
      {hint && <div className="text-xs text-ink-muted mt-1">{hint}</div>}
    </div>
  );
}

const TIER_STYLES: Record<string, string> = {
  tier_1: "bg-navy-100 text-navy-800",
  tier_2: "bg-gold-soft/50 text-gold-dark",
  tier_3: "bg-[#efe2f0] text-[#6b4b78]",
};
const TIER_LABELS: Record<string, string> = {
  tier_1: "Tier 1 · Assistive",
  tier_2: "Tier 2 · Supervised",
  tier_3: "Tier 3 · Bounded",
};
export function TierBadge({ tier }: { tier: string }) {
  return <span className={`chip ${TIER_STYLES[tier] || "bg-navy-100 text-navy-800"}`}>{TIER_LABELS[tier] || tier}</span>;
}

const STATUS_STYLES: Record<string, string> = {
  proposed: "bg-gold-soft/40 text-gold-dark",
  awaiting_approval: "bg-gold-soft/40 text-gold-dark",
  approved: "bg-positive/10 text-positive",
  executed: "bg-positive/10 text-positive",
  modified: "bg-navy-100 text-navy-700",
  dismissed: "bg-navy-50 text-ink-muted",
  rolled_back: "bg-navy-100 text-ink-muted",
  completed: "bg-positive/10 text-positive",
  failed: "bg-critical/10 text-critical",
  paused: "bg-critical/10 text-critical",
};
export function StatusBadge({ status }: { status: string }) {
  return <span className={`chip ${STATUS_STYLES[status] || "bg-navy-100 text-navy-700"}`}>{titleCase(status)}</span>;
}

const SEV_STYLES: Record<string, string> = {
  high: "bg-critical/10 text-critical",
  medium: "bg-caution/10 text-caution",
  low: "bg-navy-100 text-navy-700",
  info: "bg-navy-50 text-ink-muted",
};
export function SeverityBadge({ severity }: { severity: string }) {
  return <span className={`chip ${SEV_STYLES[severity] || "bg-navy-100"}`}>{titleCase(severity)}</span>;
}

export function Segment({ children }: { children: string }) {
  return <span className="chip bg-navy-50 text-ink-soft">{titleCase(children)}</span>;
}

export function Spinner({ label }: { label?: string }) {
  return (
    <div className="flex items-center gap-3 text-ink-muted text-sm py-8 justify-center">
      <span className="h-4 w-4 rounded-full border-2 border-navy-200 border-t-navy-700 animate-spin" />
      {label || "Loading…"}
    </div>
  );
}

export function Empty({ children, icon }: { children: ReactNode; icon?: ReactNode }) {
  return (
    <div className="text-center text-ink-muted text-sm py-10">
      {icon && <div className="flex justify-center mb-3 opacity-30">{icon}</div>}
      {children}
    </div>
  );
}

export function ConfidenceBar({ value }: { value: number }) {
  const pctVal = Math.round((value || 0) * 100);
  const color = pctVal >= 80 ? "bg-positive" : pctVal >= 50 ? "bg-caution" : "bg-critical";
  return (
    <div className="flex items-center gap-2">
      <div className="h-1.5 w-32 rounded-full bg-navy-100 overflow-hidden">
        <div className={`h-full ${color}`} style={{ width: `${pctVal}%` }} />
      </div>
      <span className="text-xs text-ink-muted tabular-nums">{pctVal}%</span>
    </div>
  );
}

/** Back-link breadcrumb. Pass items = [{label, href?}], last item is current page (no link). */
export function Breadcrumb({ items }: { items: { label: string; href?: string }[] }) {
  return (
    <nav aria-label="Breadcrumb" className="flex items-center gap-1 text-sm text-ink-muted mb-4">
      {items.map((item, i) => (
        <span key={i} className="flex items-center gap-1">
          {i > 0 && <ChevronRight size={13} className="shrink-0" />}
          {item.href ? (
            <Link href={item.href} className="hover:text-ink transition">{item.label}</Link>
          ) : (
            <span className="text-ink font-medium">{item.label}</span>
          )}
        </span>
      ))}
    </nav>
  );
}

/** Shimmer placeholder for card-shaped content. */
export function SkeletonCard({ rows = 3 }: { rows?: number }) {
  return (
    <div className="card p-5 space-y-3 animate-pulse">
      <div className="h-5 w-1/3 rounded-md bg-navy-100" />
      {Array.from({ length: rows }).map((_, i) => (
        <div key={i} className="h-4 rounded-md bg-navy-50" style={{ width: `${85 - i * 12}%` }} />
      ))}
    </div>
  );
}

/** Shimmer placeholder for a list of cards. */
export function SkeletonList({ count = 3 }: { count?: number }) {
  return (
    <div className="space-y-3">
      {Array.from({ length: count }).map((_, i) => <SkeletonCard key={i} rows={2} />)}
    </div>
  );
}

/** Inline confirmation widget — avoids browser confirm() dialogs. */
export function InlineConfirmButton({
  label,
  confirmLabel = "Confirm",
  onConfirm,
  className = "",
}: {
  label: ReactNode;
  confirmLabel?: string;
  onConfirm: () => void;
  className?: string;
}) {
  return (
    <span
      className={`group relative inline-flex ${className}`}
      onMouseLeave={(e) => {
        // reset confirm state when mouse leaves
        const btn = e.currentTarget.querySelector("[data-confirm]") as HTMLElement | null;
        if (btn) btn.style.display = "none";
        const main = e.currentTarget.querySelector("[data-main]") as HTMLElement | null;
        if (main) main.style.display = "";
      }}
    >
      <button
        data-main=""
        className="btn-ghost text-xs text-critical"
        onClick={(e) => {
          const parent = e.currentTarget.closest(".group") as HTMLElement | null;
          const btn = parent?.querySelector("[data-confirm]") as HTMLElement | null;
          if (btn) btn.style.display = "";
          e.currentTarget.style.display = "none";
        }}
      >
        {label}
      </button>
      <button
        data-confirm=""
        style={{ display: "none" }}
        className="btn-ghost text-xs text-critical border border-critical/40 px-2 py-0.5 rounded"
        onClick={onConfirm}
      >
        {confirmLabel}
      </button>
    </span>
  );
}
