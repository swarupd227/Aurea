/** The mark beside "Astra for Wealth", the same on the classic sidebar and the workspace rail: the firm's
 * own mark in its branding accent when it has set one, the product's otherwise. */
export function BrandMark({ accent }: { accent?: string | null }) {
  if (accent) {
    return (
      <svg width="24" height="24" viewBox="0 0 32 32" fill="none" aria-hidden className="shrink-0">
        <circle cx="16" cy="16" r="15" stroke={accent} strokeWidth="1.5" />
        <path d="M16 7l6 11H10l6-11z" fill={accent} opacity="0.9" />
        <circle cx="16" cy="20" r="2.4" className="fill-ink" />
      </svg>
    );
  }
  return (
    <div aria-hidden className="flex h-6 w-6 shrink-0 items-center justify-center rounded bg-accent font-mono text-[11px] font-bold text-black">
      A
    </div>
  );
}
