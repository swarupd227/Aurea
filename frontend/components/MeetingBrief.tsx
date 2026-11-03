import { FileText, ShieldAlert } from "lucide-react";
import { money } from "@/lib/format";

const tile = "rounded-lg bg-border-soft/60 p-2.5";
const chip = "chip bg-border-soft text-ink-soft";
const pct = (v: number | null | undefined) => (v == null ? "—" : `${(v * 100).toFixed(1)}%`);
const day = (iso: string | null | undefined) =>
  iso ? new Date(`${iso}T00:00:00`).toLocaleDateString(undefined, { day: "numeric", month: "short", year: "numeric" }) : "—";

/** A meeting brief (agents/meeting_prep.py): the figures, the record and the agenda. Figures and chips only. */
export function MeetingBrief({ brief }: { brief: any }) {
  const goals = brief.goals || [];
  const onTrack = goals.filter((g: any) => g.on_track).length;
  const mandates: any[] = brief.mandates || [];
  const review = brief.review;
  const tax = brief.tax;
  const open: any[] = brief.open_proposals || [];
  return (
    <div className="space-y-3" data-testid="meeting-brief">
      <div className="grid grid-cols-2 gap-2 cq-sm:grid-cols-4">
        <div className={tile}><div className="tile-label">Portfolio</div><div className="font-semibold text-ink tabular-nums">{money(brief.portfolio?.total_value || 0)}</div></div>
        <div className={tile}><div className="tile-label">Goals on track</div><div className="font-semibold text-ink tabular-nums">{onTrack}/{goals.length}</div></div>
        <div className={tile}>
          <div className="tile-label">Outside band</div>
          <div className={`font-semibold tabular-nums ${mandates.some((m) => m.outside) ? "text-warn" : "text-ink"}`}>
            {mandates.filter((m) => m.outside).length}/{mandates.length}
          </div>
        </div>
        <div className={tile}><div className="tile-label">Open proposals</div><div className="font-semibold text-ink tabular-nums">{open.length}</div></div>
      </div>

      {brief.agenda?.length > 0 && (
        <div><div className="tile-label mb-1">Agenda</div>
          <ol className="list-decimal space-y-1 pl-4 text-sm marker:text-ink-faint">{brief.agenda.map((a: string, i: number) => <li key={i} className="text-ink-soft">{a}</li>)}</ol></div>
      )}

      {mandates.length > 0 && (
        <div className="overflow-x-auto">
          <table className="w-full text-xs">
            <thead><tr className="text-left text-[10px] uppercase tracking-wide text-ink-muted">
              <th className="py-1 pr-3 font-medium">Mandate</th><th className="py-1 pr-3 font-medium">Against</th>
              <th className="py-1 pr-3 text-right font-medium">Drift</th><th className="py-1 font-medium" />
            </tr></thead>
            <tbody className="divide-y divide-border-soft">
              {mandates.map((m, i) => (
                <tr key={i}>
                  <td className="py-1 pr-3 text-ink">{m.mandate}</td>
                  <td className="py-1 pr-3 text-ink-muted">{m.measured_against}</td>
                  <td className="py-1 pr-3 text-right font-mono tabular-nums text-ink">{pct(m.max_drift)}{m.band != null ? <span className="text-ink-muted"> / {pct(m.band)}</span> : null}</td>
                  <td className="py-1"><div className="flex flex-wrap gap-1">
                    {m.outside ? <span className="chip text-warn">outside</span> : <span className="chip text-ok">within</span>}
                    {m.findings > 0 && <span className="chip text-crit">{m.findings} IPS finding{m.findings > 1 ? "s" : ""}</span>}
                    {m.prices === "synthetic" && <span className="chip text-ink-muted">prices not live</span>}
                  </div></td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}

      <div className="flex flex-wrap gap-1.5">
        {review && (
          <span className={`chip ${review.status === "overdue" ? "text-crit" : review.status === "no_record" ? "text-warn" : ""}`}>
            Review: {review.status === "no_record" ? "none on record" : review.status.replace(/_/g, " ")}{review.last ? ` · last ${day(review.last)}` : ""}
          </span>
        )}
        {review?.unrecorded && <span className="chip text-warn">Unrecorded meeting · {day(review.unrecorded.held_on)}</span>}
        {tax && tax.flags > 0 && (
          <span className="chip" title="Estimates for a qualified person to check">
            Tax {tax.jurisdiction}: {tax.flags} item{tax.flags > 1 ? "s" : ""}
            {tax.harvestable_loss ? ` · ${money(Math.abs(tax.harvestable_loss))} harvestable` : ""}
            {tax.estimated_saving ? ` · ~${money(tax.estimated_saving)} saving, est.` : ""}
          </span>
        )}
      </div>

      {open.length > 0 && (
        <div><div className="tile-label mb-1">Open proposals</div>
          <ul className="space-y-0.5 text-xs">{open.slice(0, 5).map((p) => <li key={p.id} className="text-ink-soft">{p.title}</li>)}</ul></div>
      )}
      {brief.watch_items?.length > 0 && (
        <div><div className="tile-label mb-1">Watch items</div>
          <ul className="space-y-1">{brief.watch_items.map((w: any, i: number) => <li key={i} className="flex gap-1.5 text-xs text-ink-soft"><ShieldAlert size={12} className="mt-0.5 shrink-0 text-warn" /> {w.title}</li>)}</ul></div>
      )}
      {brief.life_events?.length > 0 && (
        <div><div className="tile-label mb-1">Life events</div>
          <div className="flex flex-wrap gap-1.5">{brief.life_events.map((e: string, i: number) => <span key={i} className={chip}>{e}</span>)}</div></div>
      )}
      {brief.house_views?.length > 0 && (
        <div><div className="tile-label mb-1 flex items-center gap-1"><FileText size={12} /> House views</div>
          {brief.house_views.map((h: any, i: number) => <div key={i} className="text-xs text-ink-soft"><span className="font-medium text-ink">{h.title}</span></div>)}</div>
      )}
    </div>
  );
}
