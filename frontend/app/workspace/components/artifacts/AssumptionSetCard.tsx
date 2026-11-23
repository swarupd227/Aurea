/** The firm's planning assumptions, one versioned record (aurea_core/assumptions.py, B-174): each figure with whose it
 * is and its source, the version standing, recent versions, and the households a new version moved. Tables and chips. */
import { money } from "@/lib/format";

interface Figure { key: string; label: string; unit: "rate" | "age" | "months" | "days" | "money"; value: number | null; whose: "firm" | "market" | "platform" | "none"; source: string }
interface Moved { household: string; biggest_change: number; on_track_changed: boolean; adviser: string | null }
interface Refresh { run?: boolean; line?: number; line_assumed?: boolean; measured?: number; not_measured?: number; moved?: Moved[]; tasks?: number }
interface Version { version: number; made: string | null; by: string; reason: string; changed: string[]; refresh: Refresh }

const pct = (v: number) => `${(v * 100).toFixed(2).replace(/\.?0+$/, "")}%`;
const shown = (f: Figure, ccy?: string) => (f.value == null ? "not set" : f.unit === "rate" ? pct(f.value) : f.unit === "age" ? String(f.value)
  : f.unit === "money" ? money(f.value, ccy || "USD") : `${f.value} ${f.unit}`);
const day = (iso: string | null) => (iso ? new Date(iso).toLocaleDateString(undefined, { day: "numeric", month: "short", year: "numeric" }) : "—");
const tone = { firm: "text-ok", market: "text-info", platform: "text-ink-muted", none: "text-warn" } as const;

function Head({ children }: { children: React.ReactNode }) {
  return <th className="px-3 py-1.5 text-left font-mono text-[11px] font-normal uppercase tracking-wider text-ink-muted">{children}</th>;
}

export function AssumptionSetCard({ result }: { result: any }) {
  const figures: Figure[] = Array.isArray(result.figures) ? result.figures : [];
  const history: Version[] = Array.isArray(result.history) ? result.history : [];
  const recorded: Version | null = result.recorded ?? null;
  const changed = new Set<string>(recorded?.changed ?? []);
  const moved = recorded?.refresh?.moved ?? [];
  return (
    <div className="overflow-hidden rounded-lg border border-border bg-surface" data-testid="assumption-set-card">
      <div className="flex flex-wrap items-center gap-2 border-b border-border-soft px-3 py-2">
        <div className="text-sm font-medium text-ink">Planning assumptions</div>
        <span className="chip tabular-nums text-ink">{result.version ? `v${result.version}` : "no version recorded"}</span>
        {result.by && <span className="chip text-ink-muted">{result.by} · {day(result.made)}</span>}
        <span className="chip text-ink-muted tabular-nums">{figures.filter((f) => f.whose === "firm").length}/{figures.length} firm</span>
      </div>

      {recorded?.refresh?.run && (
        <div className="border-b border-border-soft" data-testid="assumption-refresh">
          <div className="flex flex-wrap gap-2 px-3 py-2">
            <span className="chip tabular-nums text-ink">{recorded.refresh.measured ?? 0} measured</span>
            <span className={`chip tabular-nums ${moved.length ? "text-warn" : "text-ok"}`}>{moved.length} past the line</span>
            <span className="chip tabular-nums text-ink-muted">
              line {pct(recorded.refresh.line ?? 0)}{recorded.refresh.line_assumed ? " · platform default" : ""}
            </span>
            {!!recorded.refresh.not_measured && <span className="chip tabular-nums text-ink-muted">{recorded.refresh.not_measured} with no goals</span>}
            {(recorded.refresh as any).retirement_not_measured && <span className="chip text-warn">healthcare moves retirement plans · not measured here</span>}
          </div>
          {moved.length > 0 && (
            <div className="overflow-x-auto">
              <table className="w-full text-sm">
                <thead><tr><Head>Household</Head><Head>Largest move</Head><Head>On track</Head><Head>Task for</Head></tr></thead>
                <tbody className="divide-y divide-border-soft">
                  {moved.map((m) => (
                    <tr key={m.household}>
                      <td className="px-3 py-1.5 text-ink">{m.household}</td>
                      <td className="px-3 py-1.5 tabular-nums">{pct(m.biggest_change)}</td>
                      <td className="px-3 py-1.5">{m.on_track_changed ? <span className="chip text-warn">changed</span> : <span className="text-ink-muted">—</span>}</td>
                      <td className="px-3 py-1.5 text-ink-muted">{m.adviser ?? "unassigned"}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}
        </div>
      )}

      {figures.length > 0 && (
        <div className="overflow-x-auto">
          <table className="w-full text-sm">
            <thead><tr><Head>Figure</Head><Head>Value</Head><Head>Whose</Head><Head>Source</Head></tr></thead>
            <tbody className="divide-y divide-border-soft">
              {figures.map((f) => (
                <tr key={f.key} data-testid="assumption-figure">
                  <td className="px-3 py-1.5 text-ink">
                    {f.label}
                    {changed.has(f.key) && <span className="chip ml-2 text-ink">changed</span>}
                  </td>
                  <td className="px-3 py-1.5 whitespace-nowrap tabular-nums text-ink">{shown(f, result.currency)}</td>
                  <td className="px-3 py-1.5"><span className={`chip ${tone[f.whose]}`}>{f.whose}</span></td>
                  <td className="px-3 py-1.5 text-xs text-ink-muted">{f.source}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}

      {history.length > 1 && (
        <div className="overflow-x-auto border-t border-border-soft">
          <table className="w-full text-sm">
            <thead><tr><Head>Version</Head><Head>Recorded</Head><Head>By</Head><Head>Figures</Head><Head>Moved</Head></tr></thead>
            <tbody className="divide-y divide-border-soft">
              {history.map((v) => (
                <tr key={v.version}>
                  <td className="px-3 py-1.5 tabular-nums text-ink">v{v.version}</td>
                  <td className="px-3 py-1.5 whitespace-nowrap text-xs text-ink-muted">{day(v.made)}</td>
                  <td className="px-3 py-1.5 text-ink-muted" title={v.reason}>{v.by}</td>
                  <td className="px-3 py-1.5 tabular-nums">{v.changed.length}</td>
                  <td className="px-3 py-1.5 tabular-nums">{v.refresh?.run ? v.refresh.tasks ?? 0 : "—"}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </div>
  );
}
