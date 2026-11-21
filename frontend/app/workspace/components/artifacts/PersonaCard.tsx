/** Talking points from public sources (aurea_core/persona.py, B-202). Rows and chips only: each point, the article it
 * cites, its date and when it expires, and the article's own words it rests on. Never shown to a client. */

export interface PersonaArticle { title: string; publisher: string; published_on: string; url: string; source: string; simulated: boolean }
export interface PersonaPoint {
  point: string; domain: "work" | "beyond" | string; quote: string; article: PersonaArticle; dated: string; expires_on: string;
  simulated: boolean;
}
export interface PersonaLeftOut {
  special_category: number; by_category: Record<string, number>; adverse_media: number; unchecked: number; expired: number;
}
export interface PersonaPayload {
  subject: { type: string; id: string; name: string };
  points: PersonaPoint[];
  left_out?: PersonaLeftOut;
  control?: { basis: string | null; simulated?: boolean };
  sources?: { key: string; name: string; simulated: boolean }[];
  simulated?: boolean;
  expiry_days?: number;
  articles_read?: number;
  not_on_file?: string[];
  as_of?: string;
}

const CATEGORY: Record<string, string> = {
  health: "health", religion: "religion", politics: "politics", sex_life: "sex life", ethnicity: "ethnicity",
  union: "union", criminal: "criminal",
};

const day = (iso: string | null | undefined) =>
  iso ? new Date(`${iso.slice(0, 10)}T00:00:00`).toLocaleDateString(undefined, { day: "numeric", month: "short", year: "numeric" }) : "";

/** The counts of what was left out, as chips. */
export function LeftOutChips({ left }: { left?: PersonaLeftOut }) {
  if (!left) return null;
  const cats = Object.entries(left.by_category || {}).map(([k, n]) => `${CATEGORY[k] || k} ${n}`).join(" · ");
  return (
    <>
      {left.special_category > 0 && <span className="chip text-ink-muted" data-testid="persona-left-special" title={cats}>left out: {left.special_category} special category</span>}
      {left.adverse_media > 0 && <span className="chip text-warn" data-testid="persona-left-adverse">{left.adverse_media} adverse · screening's</span>}
      {left.unchecked > 0 && <span className="chip text-ink-muted">{left.unchecked} unchecked</span>}
      {left.expired > 0 && <span className="chip text-ink-muted">{left.expired} expired</span>}
    </>
  );
}

/** One point per row: the interest, where it comes from, when, and when it expires. */
export function PersonaRows({ points }: { points: PersonaPoint[] }) {
  return (
    <table className="w-full text-xs" data-testid="persona-points">
      <tbody className="divide-y divide-border-soft">
        {points.map((p, i) => (
          <tr key={i} data-testid="persona-point">
            <td className="px-3 py-1.5 align-top">
              <div className="flex flex-wrap items-center gap-1.5">
                <span className="text-sm text-ink">{p.point}</span>
                <span className="chip text-ink-muted">{p.domain === "work" ? "at work" : "beyond work"}</span>
                {p.simulated && <span className="chip text-warn" data-testid="persona-simulated">simulated</span>}
              </div>
              <div className="mt-0.5 text-[11px] text-ink-muted" data-testid="persona-quote">&ldquo;{p.quote}&rdquo;</div>
            </td>
            <td className="hidden px-3 py-1.5 align-top cq-sm:table-cell">
              <a className="text-ink hover:underline" href={p.article.url} target="_blank" rel="noopener noreferrer">{p.article.title}</a>
              <div className="text-[11px] text-ink-muted">{p.article.publisher}</div>
            </td>
            <td className="px-3 py-1.5 text-right align-top whitespace-nowrap">
              <div className="tabular-nums text-ink-muted">{day(p.dated)}</div>
              <span className="chip mt-0.5 text-ink-muted tabular-nums" data-testid="persona-expiry">until {day(p.expires_on)}</span>
            </td>
          </tr>
        ))}
      </tbody>
    </table>
  );
}

export function PersonaCard({ result }: { result: PersonaPayload }) {
  return (
    <div className="overflow-hidden rounded-lg border border-border bg-surface" data-testid="persona-card">
      <div className="flex flex-wrap items-center gap-2 border-b border-border-soft px-3 py-2">
        <div className="text-sm font-medium text-ink">Talking points · {result.subject.name}</div>
        <span className="chip text-ink-muted tabular-nums">{result.points.length} point{result.points.length === 1 ? "" : "s"}</span>
        {result.articles_read !== undefined && <span className="chip text-ink-muted tabular-nums">{result.articles_read} articles</span>}
        {result.expiry_days && <span className="chip text-ink-muted">good for {result.expiry_days} days</span>}
        {result.simulated && <span className="chip text-warn" data-testid="persona-source-simulated">simulated source</span>}
        {(result.not_on_file || []).map((m) => <span key={m} className="chip text-warn">not on file: {m.toLowerCase()}</span>)}
      </div>
      <div className="flex flex-wrap gap-1.5 border-b border-border-soft px-3 py-2">
        {result.control?.basis && <span className="chip max-w-full truncate text-ink-muted" data-testid="persona-basis" title={result.control.basis}>basis: {result.control.basis}</span>}
        <LeftOutChips left={result.left_out} />
      </div>
      {result.points.length > 0 && <PersonaRows points={result.points} />}
    </div>
  );
}
