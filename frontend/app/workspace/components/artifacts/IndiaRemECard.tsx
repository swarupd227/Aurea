"use client";

// India batch E: the mutual fund cut-off figures (assumed or recorded), what a signed agreement carried, and the client's own language / WhatsApp
// request. Figures and chips only: the agent's message carries the sentences, and every source, caveat and reason is a chip's title.

/** Each cut-off figure, and whether it is the platform's assumption or one the firm recorded from a circular. */
export function MfFigureRows({ data }: { data: any }) {
  return (
    <div className="divide-y divide-border-soft" data-testid="mf-figures">
      {(data.figures ?? []).map((f: any) => (
        <div key={f.key} className="flex flex-wrap items-center gap-1.5 px-3 py-1.5" data-testid="mf-figure"
             title={f.state === "recorded" ? `${f.source}${f.reason ? `. ${f.reason}` : ""}` : f.source ?? undefined}>
          <span className="mr-auto text-sm text-ink">{f.label}</span>
          <span className="chip tabular-nums text-ink-muted">{f.shown}</span>
          {f.state === "recorded" && f.as_of && <span className="chip tabular-nums text-ink-muted" title="The circular's date">Circular {f.as_of}</span>}
          {f.default_differs && <span className="chip tabular-nums text-ink-muted" title="The platform's assumed figure">Assumed {f.default}</span>}
          <span className={`chip ${f.state === "recorded" ? "text-ok" : "text-warn"}`} data-testid="mf-figure-state">{f.state === "recorded" ? "Recorded" : "Assumed"}</span>
        </div>
      ))}
    </div>
  );
}

/** What a household's signed agreement is recorded as carrying. */
export function AgreementContentRows({ data }: { data: any }) {
  return (
    <div className="flex flex-wrap items-center gap-1.5 px-3 py-1.5" data-testid="agreement-contents" title={data.document ?? data.said}>
      <span className="mr-auto text-sm text-ink">{data.household ?? "Signed agreement"}</span>
      {data.recorded ? (
        <>
          <span className={`chip ${data.annexure_a ? "text-ok" : "text-crit"}`}>Annexure A {data.annexure_a ? "recorded" : "not carried"}</span>
          <span className={`chip ${data.mitc ? "text-ok" : "text-crit"}`}>MITC {data.mitc ? "recorded" : "not carried"}</span>
          <span className="chip text-ink-muted" title="Who or what recorded it">{data.source === "esign" ? "eSign template" : "A person"}</span>
        </>
      ) : <span className="chip text-warn" title={data.said}>Not recorded: {(data.missing ?? []).join(" / ")}</span>}
    </div>
  );
}

/** A tool result for the conversation. */
export function IndiaRemECard({ result }: { result: any }) {
  if (result.applies === false) {
    return <div className="rounded-lg border border-border bg-surface px-3 py-2"><span className="chip text-ink-muted">Not an Indian firm</span></div>;
  }
  const figures = Array.isArray(result.figures);
  const households: any[] | null = !figures && Array.isArray(result.households) ? result.households : null;
  return (
    <div className="overflow-hidden rounded-lg border border-border bg-surface" data-testid="india-rem-e-card">
      <div className="flex flex-wrap items-center justify-between gap-2 border-b border-border-soft px-3 py-2">
        <div className="text-sm font-medium text-ink">{figures ? "Mutual fund cut-off figures" : "Advisory agreement contents"}</div>
        {figures && <span className="chip text-ink-muted">{result.recorded} recorded, {result.assumed} assumed</span>}
      </div>
      {figures ? <MfFigureRows data={result} />
        : households ? <div className="divide-y divide-border-soft">{households.map((h) => <AgreementContentRows key={h.household_id} data={h} />)}</div>
        : <AgreementContentRows data={result} />}
    </div>
  );
}

/** The client's own language and WhatsApp requests: buttons on their page that become a task, and nothing else. */
export function ChannelRequests({ channels, onAsk, busy, sent }: {
  channels: any; onAsk: (kind: string, language?: string) => void; busy: boolean; sent: string | null;
}) {
  if (!channels?.applies) return null;
  const options = (channels.languages ?? []).filter((l: any) => l.code !== channels.language);
  return (
    <div className="mt-3 flex flex-wrap items-center gap-2" data-testid="canvas-channel-requests">
      <span className="chip text-ink-muted" title="What is recorded for you">Reading in {channels.language_label}</span>
      <span className={`chip ${channels.whatsapp_on_file ? "text-ok" : "text-ink-muted"}`}>{channels.whatsapp_on_file ? "WhatsApp on file" : "WhatsApp not on file"}</span>
      {sent ? <span className="chip bg-ok-bg text-ok" data-testid="canvas-channel-sent">{sent}</span> : (
        <>
          <select className="input h-8 py-0 text-sm" defaultValue="" disabled={busy} aria-label="Ask to be written to in another language" data-testid="canvas-language-select"
                  onChange={(e) => { if (e.target.value) onAsk("language", e.target.value); e.target.value = ""; }}>
            <option value="">Ask for another language</option>
            {options.map((l: any) => <option key={l.code} value={l.code}>{l.label} · {l.native}</option>)}
          </select>
          {!channels.whatsapp_on_file && (
            <button className="btn-outline text-sm" disabled={busy || channels.open?.whatsapp} onClick={() => onAsk("whatsapp")} data-testid="canvas-whatsapp-ask">
              {channels.open?.whatsapp ? "WhatsApp asked" : "Ask for WhatsApp"}
            </button>
          )}
        </>
      )}
    </div>
  );
}
