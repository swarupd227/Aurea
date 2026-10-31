"use client";
import { Fragment, useState } from "react";
import PageHeader from "@/components/PageHeader";
import { Card, Empty, Spinner } from "@/components/ui";
import { api } from "@/lib/api";
import { useApi } from "@/lib/hooks";
import {
  ReviewStatusChip, reviewDay, reviewFees, topicText, type ReviewHousehold,
} from "@/app/workspace/components/artifacts/ReviewsCard";

type Panel =
  | { kind: "record"; h: ReviewHousehold; held_on: string; covered: string[]; notes: string; meeting_id: string | null }
  | { kind: "book"; h: ReviewHousehold; date: string; time: string }
  | { kind: "cadence"; h: ReviewHousehold; months: number; reason: string };

export default function ClientReviews() {
  const { data, loading, refetch } = useApi<any>("/api/reviews");
  const [msg, setMsg] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [panel, setPanel] = useState<Panel | null>(null);

  if (loading || !data) return <Spinner />;
  const s = data.summary;
  const homes: ReviewHousehold[] = data.households;
  const topics: string[] = data.topics;
  const cadences: number[] = data.cadences;
  const canRecord: boolean = data.can_record;
  const canSetCadence: boolean = data.can_set_cadence;

  const ready = panel && (
    panel.kind === "record" ? !!panel.held_on && panel.covered.length > 0
    : panel.kind === "book" ? !!panel.date
    : panel.reason.trim().length >= 3 && panel.months !== panel.h.cadence_months
  );

  async function send() {
    if (!panel) return;
    setBusy(true);
    setMsg(null);
    const base = `/api/reviews/households/${panel.h.household_id}`;
    try {
      if (panel.kind === "record") {
        const r = await api<any>(base, {
          method: "POST",
          body: { held_on: panel.held_on, covered: panel.covered, notes: panel.notes || null, meeting_id: panel.meeting_id },
        });
        setMsg(`${r.household}: next review ${reviewDay(r.next_due)}`);
      } else if (panel.kind === "book") {
        const r = await api<any>(`${base}/book`, { method: "POST", body: { date: panel.date, time: panel.time || null } });
        setMsg(`${r.household}: booked ${reviewDay(r.booked?.on)}`);
      } else {
        const r = await api<any>(`${base}/cadence`, { method: "POST", body: { months: panel.months, reason: panel.reason } });
        setMsg(`${r.household}: ${r.cadence}`);
      }
      setPanel(null);
      await refetch();
    } catch (e: any) {
      setMsg(e?.message ?? "That did not work.");
    } finally {
      setBusy(false);
    }
  }

  const toRecord = (h: ReviewHousehold): Panel => ({
    kind: "record", h, held_on: h.unrecorded_meeting?.held_on ?? data.as_of, covered: [], notes: "",
    meeting_id: h.unrecorded_meeting?.meeting_id ?? null,
  });

  return (
    <div>
      <PageHeader title="Client reviews" sub="When each client was last reviewed, and when the next is due." />
      {msg && <div className="mb-4 font-mono text-xs text-ink-muted" role="status" data-testid="review-page-status">{msg}</div>}

      <div className="mb-4 grid grid-cols-2 gap-3 lg:grid-cols-4" data-testid="review-summary">
        {[
          ["Overdue", s.overdue, s.overdue ? "text-crit" : "text-ink-muted"],
          [`Due in ${data.due_soon_days} days`, s.due_soon, s.due_soon ? "text-warn" : "text-ink-muted"],
          ["On track", s.on_track, "text-ok"],
          ["No review on record", s.no_record, s.no_record ? "text-warn" : "text-ink-muted"],
        ].map(([label, v, tone]: any) => (
          <Card key={label}>
            <div className="tile-label">{label}</div>
            <div className={`font-mono text-2xl tabular-nums ${tone}`}>{v}</div>
          </Card>
        ))}
      </div>

      <Card className="mb-4">
        <div className="flex flex-wrap gap-1.5" data-testid="review-basis">
          <span className="chip text-ink-muted">next review: last review + cadence</span>
          <span className="chip text-ink-muted">default every {data.default_cadence_months} months</span>
          <span className="chip text-ink-muted">no record does not mean no review</span>
          {s.booked > 0 && <span className="chip text-ink">{s.booked} booked</span>}
          {s.unrecorded_meetings > 0 && <span className="chip text-warn">{s.unrecorded_meetings} with a meeting not recorded</span>}
        </div>
      </Card>

      {homes.length === 0 ? <Empty>No households.</Empty> : (
        <Card className="overflow-hidden p-0">
          <div className="overflow-x-auto">
            <table className="w-full text-sm" data-testid="review-rows">
              <thead>
                <tr className="text-left text-[10px] uppercase tracking-wide text-ink-muted">
                  <th className="px-4 py-2 font-medium">Client</th>
                  <th className="px-4 py-2 font-medium">Last review</th>
                  <th className="px-4 py-2 font-medium">Covered</th>
                  <th className="px-4 py-2 font-medium">Next due</th>
                  <th className="px-4 py-2 font-medium">Booked</th>
                  <th className="px-4 py-2 text-right font-medium">Fees since</th>
                  <th className="px-4 py-2 font-medium">Status</th>
                  <th className="px-4 py-2 text-right font-medium">Act</th>
                </tr>
              </thead>
              <tbody className="divide-y divide-border-soft">
                {homes.map((h) => {
                  const last = h.last_review;
                  const open = panel?.h.household_id === h.household_id ? panel : null;
                  return (
                    <Fragment key={h.household_id}>
                      <tr>
                        <td className="px-4 py-2">
                          <div className="text-ink">{h.household}</div>
                          <div className="text-xs text-ink-muted">{h.cadence}{h.cadence_set ? "" : " · default"}</div>
                        </td>
                        <td className="px-4 py-2 font-mono text-xs text-ink-muted" title={last?.notes ?? undefined}>{reviewDay(last?.held_on)}</td>
                        <td className="px-4 py-2 font-mono text-xs text-ink-muted">
                          {!last ? "—" : last.covered.length ? `${last.covered.length} of ${topics.length}` : <span className="text-warn">not recorded</span>}
                        </td>
                        <td className="px-4 py-2 font-mono text-xs text-ink">{reviewDay(h.next_due)}</td>
                        <td className="px-4 py-2 font-mono text-xs text-ink-muted" title={h.booked?.title}>{reviewDay(h.booked?.on)}</td>
                        <td className="px-4 py-2 text-right font-mono text-xs tabular-nums text-ink-muted">{reviewFees(h.fees_since_review)}</td>
                        <td className="px-4 py-2">
                          <div className="flex flex-wrap gap-1.5">
                            <ReviewStatusChip status={h.status} />
                            {h.unrecorded_meeting && <span className="chip text-warn" title={h.unrecorded_meeting.title}>meeting {reviewDay(h.unrecorded_meeting.held_on)}</span>}
                          </div>
                        </td>
                        <td className="px-4 py-2 text-right">
                          {!open && (
                            <div className="flex justify-end gap-1.5">
                              <button className="btn-outline text-xs" data-testid="review-record" disabled={!canRecord || busy}
                                title={!canRecord ? "Only an adviser or paraplanner records a review" : undefined}
                                onClick={() => setPanel(toRecord(h))}>Record</button>
                              <button className="btn-outline text-xs" data-testid="review-book" disabled={!canRecord || busy || !!h.booked}
                                title={!canRecord ? "Only an adviser or paraplanner books a review" : h.booked ? "Already booked" : undefined}
                                onClick={() => setPanel({ kind: "book", h, date: "", time: "10:00" })}>Book</button>
                              <button className="btn-outline text-xs" data-testid="review-cadence" disabled={!canSetCadence || busy}
                                title={!canSetCadence ? "Only an adviser changes a cadence" : undefined}
                                onClick={() => setPanel({ kind: "cadence", h, months: h.cadence_months, reason: "" })}>Cadence</button>
                            </div>
                          )}
                        </td>
                      </tr>
                      {open && (
                        <tr>
                          <td colSpan={8} className="bg-border-soft/30 px-4 py-3">
                            <div className="flex flex-wrap items-center gap-2" data-testid="review-confirm">
                              {open.kind === "record" && (
                                <>
                                  <input type="date" className="input text-xs" aria-label="Held on" value={open.held_on} max={data.as_of}
                                    onChange={(e) => setPanel({ ...open, held_on: e.target.value })} />
                                  {topics.map((t) => (
                                    <label key={t} className="chip flex cursor-pointer items-center gap-1 text-ink">
                                      <input type="checkbox" checked={open.covered.includes(t)}
                                        onChange={(e) => setPanel({ ...open, covered: e.target.checked ? [...open.covered, t] : open.covered.filter((c) => c !== t) })} />
                                      {topicText(t)}
                                    </label>
                                  ))}
                                  <input className="input min-w-48 flex-1 text-xs" aria-label="Notes" placeholder="Notes" value={open.notes}
                                    onChange={(e) => setPanel({ ...open, notes: e.target.value })} />
                                </>
                              )}
                              {open.kind === "book" && (
                                <>
                                  <input type="date" className="input text-xs" aria-label="Date" value={open.date} min={data.as_of}
                                    onChange={(e) => setPanel({ ...open, date: e.target.value })} />
                                  <input type="time" className="input text-xs" aria-label="Time" value={open.time}
                                    onChange={(e) => setPanel({ ...open, time: e.target.value })} />
                                </>
                              )}
                              {open.kind === "cadence" && (
                                <>
                                  <select className="input text-xs" aria-label="Every" value={open.months}
                                    onChange={(e) => setPanel({ ...open, months: Number(e.target.value) })}>
                                    {cadences.map((c) => <option key={c} value={c}>Every {c} months</option>)}
                                  </select>
                                  <input className="input min-w-48 flex-1 text-xs" aria-label="Reason" placeholder="Reason" value={open.reason}
                                    onChange={(e) => setPanel({ ...open, reason: e.target.value })} />
                                </>
                              )}
                              <button className="btn-primary text-xs" data-testid="review-confirm-yes" disabled={busy || !ready} onClick={send}>Confirm</button>
                              <button className="btn-outline text-xs" disabled={busy} onClick={() => setPanel(null)}>Not now</button>
                            </div>
                          </td>
                        </tr>
                      )}
                    </Fragment>
                  );
                })}
              </tbody>
            </table>
          </div>
        </Card>
      )}
    </div>
  );
}
