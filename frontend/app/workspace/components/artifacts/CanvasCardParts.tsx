// Shared pieces for the client Canvas cards (read_client_canvas and the client's read_my_* tools).
// Figures and chips only - the agent's message carries the sentences.

export const obj = (v: any) => v !== null && typeof v === "object" && !Array.isArray(v);

export const Chip = ({ children, tone = "text-ink-muted", testid }: { children: React.ReactNode; tone?: string; testid?: string }) => (
  <span className={`inline-block rounded border border-border px-1.5 py-0.5 font-mono text-[11px] ${tone}`} data-testid={testid}>{children}</span>
);

export function Tile({ label, value, tone = "text-ink", sub }: { label: string; value: React.ReactNode; tone?: string; sub?: string }) {
  return (
    <div className="px-3 py-2">
      <div className="font-mono text-[11px] uppercase tracking-wider text-ink-muted">{label}</div>
      <div className={`mt-0.5 font-mono text-lg tabular-nums ${tone}`}>{value}</div>
      {sub && <div className="font-mono text-[10px] text-ink-muted">{sub}</div>}
    </div>
  );
}

/** A likelihood (0-1) as a bar, toned by whether the engine counts it on track. */
export function LikelihoodBar({ value, onTrack }: { value: number; onTrack: boolean }) {
  const tone = onTrack ? "bg-ok" : value >= 0.5 ? "bg-warn" : "bg-crit";
  return (
    <div className="h-1.5 w-full overflow-hidden rounded-full bg-border-soft">
      <div className={`h-full rounded-full ${tone}`} style={{ width: `${Math.max(0, Math.min(100, Math.round(value * 100)))}%` }} />
    </div>
  );
}

export const CardShell = ({ testid, children }: { testid: string; children: React.ReactNode }) => (
  <div className="overflow-hidden rounded-lg border border-border bg-surface" data-testid={testid}>{children}</div>
);

export const CardHead = ({ title, children }: { title: React.ReactNode; children?: React.ReactNode }) => (
  <div className="flex flex-wrap items-center justify-between gap-2 border-b border-border-soft px-3 py-2">
    <div className="min-w-0 truncate text-sm font-medium text-ink">{title}</div>
    {children && <div className="flex flex-wrap gap-1.5">{children}</div>}
  </div>
);
