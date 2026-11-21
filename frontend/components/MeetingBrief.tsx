"use client";

import { useState } from "react";
import { ChevronDown, ChevronRight, FileText, ShieldAlert } from "lucide-react";
import { money } from "@/lib/format";
import { api } from "@/lib/api";

const tile = "rounded-lg bg-border-soft p-2.5";
const chip = "chip bg-border-soft text-ink-soft";
const pct = (v: number | null | undefined) => (v == null ? "—" : `${(v * 100).toFixed(1)}%`);
const day = (iso: string | null | undefined) =>
  iso ? new Date(`${iso.slice(0, 10)}T00:00:00`).toLocaleDateString(undefined, { day: "numeric", month: "short", year: "numeric" }) : "—";

const DEFAULT_ORDER = ["agenda", "mandates", "goals", "history", "review", "tax", "open_proposals", "watch_items", "life_events", "house_views"];
const LABELS: Record<string, string> = {
  agenda: "Agenda", mandates: "Mandates", goals: "Goals", history: "History", review: "Review", tax: "Tax",
  open_proposals: "Open proposals", watch_items: "Watch items", life_events: "Life events", house_views: "House views",
};
// Held away, line by line, and how it is doing (B-198): after the mandates, as the brief's backend orders it.
DEFAULT_ORDER.splice(DEFAULT_ORDER.indexOf("goals"), 0, "held_away");
LABELS.held_away = "Held away";

/** A section the RM can open or close; what they do is recorded only to offer a change back (B-203). */
function Section({ id, label, open, onToggle, children }: { id: string; label: string; open: boolean; onToggle: () => void; children: React.ReactNode }) {
  return (
    <div data-testid={`meeting-brief-section-${id}`}>
      <button type="button" className="tile-label mb-1 flex items-center gap-1 hover:text-ink" aria-expanded={open} onClick={onToggle}>
        {open ? <ChevronDown size={12} /> : <ChevronRight size={12} />} {label}
      </button>
      {open && children}
    </div>
  );
}

const signedPct = (v: number | null | undefined) => (v == null ? "—" : `${v >= 0 ? "+" : ""}${(v * 100).toFixed(1)}%`);

/** What the household holds elsewhere and what of it to raise (B-198): a holding that trails its reference, with the
 * shelf's alternative over the same dates, and one also in the managed book. Estimates, for the RM only. */
function HeldAwayBrief({ held }: { held: any }) {
  const under: any[] = held.underperforming || [];
  const overlaps: any[] = held.overlaps || [];
  return (
    <div className="space-y-1.5 text-xs" data-testid="meeting-brief-held-away">
      <div className="flex flex-wrap gap-1.5">
        <span className="chip tabular-nums text-ink-muted">{held.holdings} listed · {money(held.value || 0, held.currency)}</span>
        <span className="chip tabular-nums text-ink-muted">{held.measured} measured</span>
        {held.not_measured > 0 && <span className="chip tabular-nums text-ink-muted">{held.not_measured} no performance</span>}
        {held.simulated > 0 && <span className="chip text-warn" data-testid="meeting-brief-held-away-simulated">simulated feed</span>}
        <span className="chip text-ink-muted" title={held.rule}>estimate · price return</span>
      </div>
      {under.map((f: any) => (
        <div key={f.id} className="flex flex-wrap items-center gap-1.5">
          <span className="font-medium text-ink">{f.symbol || f.name}</span>
          <span className="text-ink-muted">{f.institution} · {f.owner}</span>
          <span className="chip tabular-nums text-crit">{signedPct(f.return)}</span>
          {f.reference && <span className="chip tabular-nums text-ink-muted">{f.reference.symbol} {signedPct(f.reference.return)}</span>}
          {f.alternative
            ? <span className="chip tabular-nums text-ok">shelf: {f.alternative.symbol} {signedPct(f.alternative.return)}{f.alternative.shelf === "not_reviewed" ? " · not reviewed" : ""}</span>
            : <span className="chip text-ink-muted">no shelf alternative</span>}
        </div>
      ))}
      {overlaps.length > 0 && (
        <div className="flex flex-wrap gap-1.5">
          {overlaps.map((f: any) => <span key={f.id} className="chip text-warn">{f.symbol || f.name} · also managed</span>)}
        </div>
      )}
    </div>
  );
}

/** A meeting brief (agents/meeting_prep.py): the figures, the record, the history and the agenda, laid out for the RM
 * who holds the meeting. Figures and chips only. */
export function MeetingBrief({ brief }: { brief: any }) {
  const goals = brief.goals || [];
  const onTrack = goals.filter((g: any) => g.on_track).length;
  const mandates: any[] = brief.mandates || [];
  const review = brief.review;
  const tax = brief.tax;
  const open: any[] = brief.open_proposals || [];
  const history = brief.history;
  const shaped = brief.shaped_for;
  const heldAway = brief.held_away;

  const body: Record<string, React.ReactNode> = {
    agenda: brief.agenda?.length > 0 && (
      <ol className="list-decimal space-y-1 pl-4 text-sm marker:text-ink-faint">{brief.agenda.map((a: string, i: number) => <li key={i} className="text-ink-soft">{a}</li>)}</ol>
    ),
    mandates: mandates.length > 0 && (
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
    ),
    held_away: heldAway && <HeldAwayBrief held={heldAway} />,
    goals: goals.length > 0 && (
      <div className="flex flex-wrap gap-1.5">
        {goals.map((g: any, i: number) => (
          <span key={i} className={`chip tabular-nums ${g.on_track ? "text-ok" : "text-warn"}`} title="An estimate">
            {g.name || "Goal"} · {typeof g.probability === "number" ? `${Math.round(g.probability * 100)}%` : g.on_track ? "on track" : "off track"}
          </span>
        ))}
      </div>
    ),
    history: history && (
      <div className="space-y-1.5 text-xs" data-testid="meeting-brief-history">
        {(history.meetings || []).map((m: any, i: number) => (
          <div key={`m${i}`} className="flex flex-wrap items-center gap-1.5">
            <span className="w-20 shrink-0 tabular-nums text-ink-muted">{day(m.at)}</span>
            <span className="text-ink">{m.title}</span>
            {m.agreed > 0 && <span className="chip text-ink-muted tabular-nums">{m.agreed} agreed</span>}
            <span className="chip text-ink-muted">{m.source}</span>
          </div>
        ))}
        {(history.touches || []).map((t: any, i: number) => (
          <div key={`t${i}`} className="flex gap-1.5">
            <span className="w-20 shrink-0 tabular-nums text-ink-muted">{day(t.at)}</span>
            <span className="w-12 shrink-0 text-ink-muted">{t.type}</span>
            <span className="min-w-0 flex-1 truncate text-ink-soft" title={t.detail}>{t.detail}</span>
            <span className="shrink-0 text-ink-muted">{t.by || ""}</span>
          </div>
        ))}
        {(history.messages || []).length > 0 && (
          <div className="flex flex-wrap gap-1.5">
            {history.messages.map((m: any, i: number) => (
              <span key={i} className={`chip tabular-nums ${m.unread ? "text-warn" : "text-ink-muted"}`}>
                message · {m.from} · {day(m.at)}{m.unread ? " · unread" : ""}
              </span>
            ))}
          </div>
        )}
        {(history.decisions || []).map((d: any, i: number) => (
          <div key={`d${i}`} className="flex flex-wrap items-center gap-1.5">
            <span className="w-20 shrink-0 tabular-nums text-ink-muted">{day(d.at)}</span>
            <span className="text-ink">{d.title}</span>
            <span className={`chip ${d.decision === "dismissed" ? "text-ink-muted" : "text-ok"}`}>{d.decision}{d.by ? ` · ${d.by}` : ""}</span>
            {d.note && <span className="text-ink-muted" title={d.note}>{d.note}</span>}
          </div>
        ))}
        {(history.open_items || []).length > 0 && (
          <div className="flex flex-wrap gap-1.5">
            {history.open_items.map((t: any, i: number) => (
              <span key={i} className={`chip ${t.overdue ? "text-crit" : "text-ink-muted"}`}>{t.title}{t.due ? ` · ${day(t.due)}` : ""}</span>
            ))}
          </div>
        )}
        {(history.not_kept_here || []).map((k: string) => <span key={k} className="chip text-ink-muted">{k}: no register here</span>)}
      </div>
    ),
    review: (review || tax) && (
      <div className="flex flex-wrap gap-1.5">
        {review && (
          <span className={`chip ${review.status === "overdue" ? "text-crit" : review.status === "no_record" ? "text-warn" : ""}`}>
            Review: {review.status === "no_record" ? "none on record" : review.status.replace(/_/g, " ")}{review.last ? ` · last ${day(review.last)}` : ""}
          </span>
        )}
        {review?.unrecorded && <span className="chip text-warn">Unrecorded meeting · {day(review.unrecorded.held_on)}</span>}
      </div>
    ),
    tax: tax && tax.flags > 0 && (
      <span className="chip" title="Estimates for a qualified person to check">
        Tax {tax.jurisdiction}: {tax.flags} item{tax.flags > 1 ? "s" : ""}
        {tax.harvestable_loss ? ` · ${money(Math.abs(tax.harvestable_loss))} harvestable` : ""}
        {tax.estimated_saving ? ` · ~${money(tax.estimated_saving)} saving, est.` : ""}
      </span>
    ),
    open_proposals: open.length > 0 && (
      <ul className="space-y-0.5 text-xs">{open.slice(0, 5).map((p) => <li key={p.id} className="text-ink-soft">{p.title}</li>)}</ul>
    ),
    watch_items: brief.watch_items?.length > 0 && (
      <ul className="space-y-1">{brief.watch_items.map((w: any, i: number) => <li key={i} className="flex gap-1.5 text-xs text-ink-soft"><ShieldAlert size={12} className="mt-0.5 shrink-0 text-warn" /> {w.title}</li>)}</ul>
    ),
    life_events: brief.life_events?.length > 0 && (
      <div className="flex flex-wrap gap-1.5">{brief.life_events.map((e: string, i: number) => <span key={i} className={chip}>{e}</span>)}</div>
    ),
    house_views: brief.house_views?.length > 0 && (
      <div>{brief.house_views.map((h: any, i: number) => <div key={i} className="flex items-center gap-1 text-xs text-ink-soft"><FileText size={12} /><span className="font-medium text-ink">{h.title}</span></div>)}</div>
    ),
  };

  const present = (shaped?.order?.length ? shaped.order : DEFAULT_ORDER).filter((k: string) => body[k]);
  for (const k of DEFAULT_ORDER) if (body[k] && !present.includes(k)) present.push(k);
  // A brief made before profiles opens every section, as it always did.
  const [opened, setOpened] = useState<Set<string>>(() => new Set(shaped?.expanded?.length ? shaped.expanded : present));

  function toggle(key: string) {
    const opening = !opened.has(key);
    setOpened((prev) => {
      const next = new Set(prev);
      opening ? next.add(key) : next.delete(key);
      return next;
    });
    api("/api/briefs/events", { body: { kind: "meeting", section: key, action: opening ? "open" : "close" } }).catch(() => {});
  }

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

      {shaped && (
        <div className="flex flex-wrap gap-1.5" data-testid="meeting-brief-shaped">
          {shaped.set
            ? <span className="chip text-ink-muted">laid out for {shaped.for} · opens {shaped.length}</span>
            : <span className="chip text-ink-muted">default layout</span>}
          {(shaped.strengths || []).map((s: string) => <span key={s} className="chip text-ink-muted">leads with {s}</span>)}
        </div>
      )}

      {present.map((key: string) => (
        <Section key={key} id={key} label={LABELS[key] || key} open={opened.has(key)} onToggle={() => toggle(key)}>
          {body[key]}
        </Section>
      ))}
    </div>
  );
}
