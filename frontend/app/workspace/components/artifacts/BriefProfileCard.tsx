/** The RM's own brief profile (aurea_core/brief_profile.py, B-203): what leads, how much opens, their strengths, and
 * what they keep opening offered back. Chips only. */

export interface BriefSuggestion { section: string; label: string; kind: string; move: "up" | "down"; days: number; lead_after: string[] }
export interface BriefProfilePayload {
  user: string | null; set: boolean; lead: string[]; lead_labels: string[]; length: string; strengths: string[];
  version: number; updated: string | null; suggestions?: BriefSuggestion[];
  sections?: Record<string, { key: string; label: string }[]>;
}

export function BriefProfileCard({ result }: { result: BriefProfilePayload }) {
  const sugg = result.suggestions || [];
  return (
    <div className="overflow-hidden rounded-lg border border-border bg-surface" data-testid="brief-profile-card">
      <div className="flex flex-wrap items-center gap-2 border-b border-border-soft px-3 py-2">
        <div className="text-sm font-medium text-ink">Brief profile{result.user ? ` · ${result.user}` : ""}</div>
        {result.set
          ? <span className="chip text-ink-muted tabular-nums">version {result.version}</span>
          : <span className="chip text-ink-muted" data-testid="brief-profile-unset">default layout</span>}
        <span className="chip text-ink-muted">opens {result.length}</span>
      </div>
      <div className="space-y-2 px-3 py-2">
        <div className="flex flex-wrap items-center gap-1.5" data-testid="brief-profile-lead">
          <span className="tile-label">Leads with</span>
          {result.lead_labels.length
            ? result.lead_labels.map((l, i) => <span key={l} className="chip text-ink tabular-nums">{i + 1} · {l}</span>)
            : <span className="chip text-ink-muted">default order</span>}
        </div>
        {result.strengths.length > 0 && (
          <div className="flex flex-wrap items-center gap-1.5" data-testid="brief-profile-strengths">
            <span className="tile-label">Strengths</span>
            {result.strengths.map((s) => <span key={s} className="chip text-ink-muted">{s}</span>)}
          </div>
        )}
        {sugg.length > 0 && (
          <div className="flex flex-wrap items-center gap-1.5" data-testid="brief-profile-suggestions">
            <span className="tile-label">Suggested</span>
            {sugg.map((s) => (
              <span key={`${s.section}-${s.move}`} className="chip text-warn tabular-nums">
                {s.label} {s.move === "up" ? "to lead" : "out of the lead"} · {s.move === "up" ? "opened" : "closed"} on {s.days} days
              </span>
            ))}
          </div>
        )}
      </div>
    </div>
  );
}
