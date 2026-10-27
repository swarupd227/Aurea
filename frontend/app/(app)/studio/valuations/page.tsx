"use client";
import { useState } from "react";
import { Landmark } from "lucide-react";
import PageHeader from "@/components/PageHeader";
import { Card, Empty, Spinner } from "@/components/ui";
import { api } from "@/lib/api";
import { useApi } from "@/lib/hooks";

function n(v: number, d = 2) {
  return v.toLocaleString(undefined, { minimumFractionDigits: d, maximumFractionDigits: d });
}

const BASIS: Record<string, { text: string; tone: string }> = {
  declared: { text: "stated valuation", tone: "text-ink-muted" },
  synthetic: { text: "placeholder", tone: "text-warn" },
  market: { text: "market price", tone: "text-ink-muted" },
};

export default function Valuations() {
  const { data, loading, refetch } = useApi<any>("/api/valuations/private");
  const [open, setOpen] = useState<string | null>(null);
  const [value, setValue] = useState("");
  const [asOf, setAsOf] = useState(new Date().toISOString().slice(0, 10));
  const [doc, setDoc] = useState("");
  const [notes, setNotes] = useState("");
  const [busy, setBusy] = useState(false);
  const [msg, setMsg] = useState<string | null>(null);

  if (loading || !data) return <Spinner />;
  const canRecord = data.can_record;

  async function submit(instrumentId: string) {
    setBusy(true);
    setMsg(null);
    try {
      const r = await api<any>("/api/valuations", {
        method: "POST",
        body: { instrument_id: instrumentId, unit_value: Number(value), as_of: asOf, source_document: doc, notes: notes || undefined },
      });
      setMsg(`Recorded ${r.symbol} at ${n(r.unit_value, 4)}. ${r.holdings_revalued} holding(s) revalued, ${n(r.value_before, 0)} to ${n(r.value_after, 0)}.`);
      setOpen(null);
      setValue(""); setDoc(""); setNotes("");
      await refetch();
    } catch (e: any) {
      setMsg(e?.message ?? "That did not work.");
    } finally {
      setBusy(false);
    }
  }

  return (
    <div>
      <PageHeader title="Valuations" sub="Private holdings have no market, so their value is what the manager last stated." />
      {msg && <div className="mb-4 font-mono text-xs text-ink-muted" role="status">{msg}</div>}
      {data.instruments.length === 0 ? <Empty>No private holdings.</Empty> : (
        <div className="space-y-4" data-testid="valuations-list">
          {data.instruments.map((r: any) => {
            const b = r.basis ? BASIS[r.basis] : null;
            return (
              <Card key={r.instrument_id}>
                <div className="flex flex-wrap items-start justify-between gap-3">
                  <div>
                    <div className="font-semibold text-ink flex items-center gap-2"><Landmark size={16} /> {r.symbol} <span className="font-normal text-ink-muted">{r.name}</span></div>
                    <div className="mt-1 flex flex-wrap gap-1.5">
                      {b && <span className={`chip ${b.tone}`}>{b.text}</span>}
                      <span className={`chip ${r.overdue ? "text-crit" : "text-ink-muted"}`}>
                        {r.age_days != null ? `${r.age_days} days old · limit ${r.limit_days}` : "no value"}
                      </span>
                      <span className="chip text-ink-muted">{r.holdings} holding(s) · {r.currency} {n(r.value_held, 0)}</span>
                      {r.source_document && <span className="chip text-ink-muted" title={r.source_document}>{r.source_document.slice(0, 56)}</span>}
                    </div>
                  </div>
                  <div className="text-right">
                    <div className="tile-label">Unit value{r.as_of ? ` · ${r.as_of}` : ""}</div>
                    <div className="font-mono text-xl tabular-nums text-ink">{r.unit_value != null ? n(r.unit_value, 4) : "—"}</div>
                    <button className="btn-outline mt-2 text-xs" data-testid="valuation-record" disabled={!canRecord}
                      title={canRecord ? undefined : "Only operations or the portfolio team may record a valuation"}
                      onClick={() => setOpen(open === r.instrument_id ? null : r.instrument_id)}>
                      Record valuation
                    </button>
                  </div>
                </div>
                {open === r.instrument_id && (
                  <div className="mt-4 grid gap-2 border-t border-border-soft pt-4 lg:grid-cols-4">
                    <input className="input text-sm" type="number" step="0.0001" placeholder="Unit value" value={value} onChange={(e) => setValue(e.target.value)} />
                    <input className="input text-sm" type="date" value={asOf} onChange={(e) => setAsOf(e.target.value)} />
                    <input className="input text-sm lg:col-span-2" placeholder="Source document - statement, date, manager" value={doc} onChange={(e) => setDoc(e.target.value)} />
                    <input className="input text-sm lg:col-span-3" placeholder="Note (required if the value moves by more than 50%)" value={notes} onChange={(e) => setNotes(e.target.value)} />
                    <button className="btn-primary" disabled={busy || !value || doc.trim().length < 5} onClick={() => submit(r.instrument_id)}>Record</button>
                  </div>
                )}
              </Card>
            );
          })}
        </div>
      )}
    </div>
  );
}
