import { firmLocale } from "@/lib/format";
/** The ways in to a prospect, from people the firm already knows (aurea_core/introductions.py, B-200). Figures, chains
 * and chips only: who knows whom, how strongly, on what evidence, and who should ask. */

export interface IntroNode { type: string; id: string; name: string }
export interface IntroHop {
  from: IntroNode; to: IntroNode; kind: string; label: string; recorded: boolean; closeness: string | null;
  how: string | null; evidence: string | null; recorded_by: string | null; recorded_on: string | null; last: string | null;
}
export interface IntroPath {
  score: number; proposable: boolean; introducer: IntroNode & { standing: string[] }; ask: IntroNode; hops: IntroHop[];
  recorded_links: number;
}
export interface InTouch { user: IntroNode; label: string; kind: string; last: string | null }
export interface IntroductionsPayload {
  target: IntroNode & { contact_type?: string | null }; paths: IntroPath[]; in_touch: InTouch[];
  recorded_links_to_target: number; not_on_file: string[]; as_of?: string;
}

const day = (iso: string | null | undefined) =>
  iso ? new Date(`${iso.slice(0, 10)}T00:00:00`).toLocaleDateString(firmLocale(), { day: "numeric", month: "short", year: "numeric" }) : null;

function Score({ value }: { value: number }) {
  const tone = value >= 60 ? "bg-ok" : value >= 25 ? "bg-warn" : "bg-ink-muted";
  return (
    <div className="w-14 shrink-0" title="0-100: closeness of each link, how recent, and the introducer's standing">
      <div className="font-mono text-sm font-semibold tabular-nums text-ink">{value}</div>
      <div className="mt-1 h-1 rounded-full bg-border-soft"><div className={`h-1 rounded-full ${tone}`} style={{ width: `${value}%` }} /></div>
    </div>
  );
}

/** One way in: score, the chain hop by hop, the introducer's standing, and who asks. */
export function IntroPathRow({ path }: { path: IntroPath }) {
  return (
    <div className="flex gap-3 px-3 py-2" data-testid="intro-path">
      <Score value={path.score} />
      <div className="min-w-0 flex-1 space-y-1">
        <div className="flex flex-wrap items-center gap-1.5 text-xs">
          {path.hops.map((h, i) => (
            <span key={i} className="inline-flex items-center gap-1">
              {i === 0 && <span className="text-ink">{h.from.name}</span>}
              <span className={`chip ${h.recorded ? "text-ink" : "text-ink-muted"}`} title={h.evidence || undefined}>
                {h.label}{h.closeness ? ` · ${h.closeness}` : ""}
              </span>
              <span className={h.to.id === path.introducer.id ? "font-medium text-ink" : "text-ink"}>{h.to.name}</span>
            </span>
          ))}
        </div>
        <div className="flex flex-wrap gap-1.5">
          <span className="chip text-ink" data-testid="intro-ask">ask: {path.ask.name}</span>
          {path.introducer.standing.map((s) => <span key={s} className="chip text-ink-muted">{s}</span>)}
          {!path.proposable && <span className="chip text-ink-muted">weak</span>}
        </div>
        {path.hops.filter((h) => h.recorded && h.evidence).map((h, i) => (
          <div key={i} className="text-[11px] text-ink-muted" data-testid="intro-evidence">
            {h.from.name} {h.label} {h.to.name}: {h.evidence}{h.recorded_by ? ` · ${h.recorded_by}` : ""}{h.recorded_on ? ` · ${day(h.recorded_on)}` : ""}
          </div>
        ))}
      </div>
    </div>
  );
}

export function IntroductionsCard({ result }: { result: IntroductionsPayload }) {
  return (
    <div className="overflow-hidden rounded-lg border border-border bg-surface" data-testid="introductions-card">
      <div className="flex flex-wrap items-center gap-2 border-b border-border-soft px-3 py-2">
        <div className="text-sm font-medium text-ink">Ways in · {result.target.name}</div>
        <span className="chip text-ink-muted tabular-nums">{result.paths.length} path{result.paths.length === 1 ? "" : "s"}</span>
        <span className="chip text-ink-muted tabular-nums">{result.recorded_links_to_target} recorded link{result.recorded_links_to_target === 1 ? "" : "s"}</span>
        {result.not_on_file.map((m) => <span key={m} className="chip text-warn" data-testid="intro-missing">not on file: {m.toLowerCase()}</span>)}
      </div>
      {result.in_touch.length > 0 && (
        <div className="flex flex-wrap gap-1.5 border-b border-border-soft px-3 py-2" data-testid="intro-in-touch">
          {result.in_touch.map((t, i) => (
            <span key={i} className="chip text-ok">in touch: {t.user.name}{t.last ? ` · ${day(t.last)}` : ""}</span>
          ))}
        </div>
      )}
      {result.paths.length > 0 && (
        <div className="divide-y divide-border-soft">{result.paths.map((p, i) => <IntroPathRow key={i} path={p} />)}</div>
      )}
    </div>
  );
}

export interface LinkRecordedPayload {
  link_id: string; from: IntroNode; to: IntroNode; kind: string; label: string; closeness: string | null; how: string | null;
  evidence: string; recorded_by: string; recorded_on: string;
}

export function LinkRecordedCard({ result }: { result: LinkRecordedPayload }) {
  return (
    <div className="rounded-lg border border-border bg-surface px-3 py-2" data-testid="link-recorded-card">
      <div className="flex flex-wrap items-center gap-1.5 text-sm">
        <span className="text-ink">{result.from.name}</span>
        <span className="chip text-ink">{result.label}{result.closeness ? ` · ${result.closeness}` : ""}</span>
        <span className="text-ink">{result.to.name}</span>
        <span className="chip text-ok">recorded</span>
      </div>
      <div className="mt-1 flex flex-wrap gap-1.5">
        {result.how && <span className="chip text-ink-muted">{result.how}</span>}
        <span className="chip text-ink-muted">{result.recorded_by} · {day(result.recorded_on)}</span>
      </div>
      <div className="mt-1 text-[11px] text-ink-muted">{result.evidence}</div>
    </div>
  );
}
