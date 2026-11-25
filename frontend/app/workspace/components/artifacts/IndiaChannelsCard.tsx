"use client";

// A client's language and WhatsApp (B-301), and the DigiLocker and eSign seams (B-302). Figures and chips only: the agent's message
// carries the sentences, and a template's wording or a source is a chip's title. A simulated provider always shows its chip.

const DELIVERY_TONE: Record<string, string> = { read: "text-ok", delivered: "text-ok", sent: "text-ink-muted", failed: "text-crit", "not reported": "text-warn", "in the thread": "text-ink-muted" };

export function SimulatedChip({ provider }: { provider?: any }) {
  if (!provider) return <span className="chip text-ink-muted" data-testid="channel-not-connected" title="No provider is connected: nothing can be sent or fetched">Not connected</span>;
  return provider.simulated
    ? <span className="chip text-warn" data-testid="simulated-chip" title={provider.name}>Simulated</span>
    : <span className="chip text-ink-muted" title={provider.name}>{provider.name}</span>;
}

/** Each household's language, WhatsApp opt-in and window, and the last status the channel reported. */
export function ChannelRows({ data }: { data: any }) {
  return (
    <div className="divide-y divide-border-soft" data-testid="channel-rows">
      {(data.households ?? []).map((h: any) => (
        <div key={h.household_id} className="flex flex-wrap items-center gap-1.5 px-3 py-1.5" data-testid="channel-household" title={h.evidence ?? undefined}>
          <span className="mr-auto text-sm text-ink">{h.household}</span>
          <span className={`chip ${h.recorded ? "text-ink-soft" : "text-ink-muted"}`} title={h.recorded ? `${h.native}, asked by ${h.asked_by}` : "None on file: the firm's own, English"} data-testid="channel-language">
            {h.recorded ? h.label : "English, none on file"}
          </span>
          <span className={`chip ${h.whatsapp.opted_in ? "text-ok" : h.whatsapp.withdrawn ? "text-warn" : "text-ink-muted"}`} data-testid="channel-optin"
            title={h.whatsapp.given_on ? `${h.whatsapp.method}, ${h.whatsapp.given_on}` : "No opt-in on file"}>
            {h.whatsapp.opted_in ? "WhatsApp opt-in" : h.whatsapp.withdrawn ? "Opt-in withdrawn" : "No WhatsApp opt-in"}
          </span>
          {h.whatsapp.opted_in && (
            <span className={`chip ${h.window_open ? "text-ok" : "text-ink-muted"}`} title={h.closes_at ? `The 24-hour window closes ${h.closes_at}` : "The client has not written on WhatsApp"} data-testid="channel-window">
              {h.window_open ? "Window open" : "Template only"}
            </span>
          )}
          {h.last_delivery && (
            <span className={`chip ${DELIVERY_TONE[h.last_delivery.state] ?? "text-ink-muted"}`} title={`${h.last_delivery.channel}, ${h.last_delivery.translation}`} data-testid="channel-delivery">
              {h.last_delivery.state}
            </span>
          )}
        </div>
      ))}
    </div>
  );
}

/** The approved WhatsApp templates: name, language and the provider's reference; the wording is the title. */
export function TemplateRows({ templates }: { templates: any[] }) {
  return (
    <div className="divide-y divide-border-soft" data-testid="template-rows">
      {templates.map((t) => (
        <div key={t.id} className="flex flex-wrap items-center gap-1.5 px-3 py-1.5" data-testid="template-row" title={t.body}>
          <span className="mr-auto text-sm text-ink">{t.name}</span>
          <span className="chip text-ink-muted">{t.language_label}</span>
          <span className="chip text-ink-muted" title="The provider's approval reference">{t.provider_ref}</span>
          <span className={`chip ${t.state === "approved" ? "text-ok" : "text-warn"}`}>{t.state}</span>
        </div>
      ))}
    </div>
  );
}

export function ChannelChips({ data }: { data: any }) {
  return (
    <>
      <SimulatedChip provider={data.provider} />
      <span className="chip tabular-nums text-ink-muted">{data.counts?.with_language ?? 0} with a language</span>
      <span className="chip tabular-nums text-ink-muted">{data.counts?.opted_in ?? 0} opted in</span>
      <span className="chip text-ink-muted" title="No translation service is connected: a draft in a client's language is the person's own words">
        {data.translation?.connected ? "Translation connected" : "Translation not connected"}
      </span>
    </>
  );
}

/** DigiLocker documents and eSign envelopes, and where a person's confirmation stands. */
export function StackRows({ items }: { items: any[] }) {
  return (
    <div className="divide-y divide-border-soft" data-testid="stack-rows">
      {items.map((i) => (
        <div key={i.id} className="flex flex-wrap items-center gap-1.5 px-3 py-1.5" data-testid="stack-item" title={i.reference}>
          <span className="mr-auto text-sm text-ink">{i.person ?? i.household}</span>
          {i.kind === "digilocker" ? (
            <>
              <span className="chip text-ink-soft">{i.doc_label}</span>
              {i.issuer && <span className="chip text-ink-muted">{i.issuer}</span>}
              {i.masked_number && <span className="chip tabular-nums text-ink-muted">{i.masked_number}</span>}
              {i.fetched_on && <span className="chip tabular-nums text-ink-muted" title="The day it was fetched">{i.fetched_on}</span>}
            </>
          ) : (
            <>
              <span className="chip text-ink-soft">{i.document_name}</span>
              <span className={`chip ${i.provider_state === "signed" ? "text-ok" : i.provider_state === "declined" ? "text-crit" : "text-warn"}`}>{i.provider_state}</span>
              {i.fetched_on && <span className="chip tabular-nums text-ink-muted" title="The day it was signed">{i.fetched_on}</span>}
            </>
          )}
          {i.simulated && <span className="chip text-warn" title={i.provider}>Simulated</span>}
          <span className={`chip ${i.outcome === "recorded" ? "text-ok" : i.open ? "text-warn" : "text-ink-muted"}`} data-testid="stack-state">
            {i.outcome === "recorded" ? "Recorded" : i.open ? "Waiting for a person" : "Not recordable"}
          </span>
        </div>
      ))}
    </div>
  );
}

export function StackChips({ data }: { data: any }) {
  return (
    <>
      <SimulatedChip provider={data.provider} />
      <span className="chip tabular-nums text-ink-muted">{data.counts?.documents ?? 0} documents</span>
      <span className="chip tabular-nums text-ink-muted">{data.counts?.envelopes ?? 0} envelopes</span>
      {(data.counts?.open ?? 0) > 0 && <span className="chip text-warn">{data.counts.open} to confirm</span>}
      <span className="chip text-ink-muted" title={data.kra_ckyc?.said}>KRA and CKYC not connected</span>
    </>
  );
}

/** A tool result for the conversation: the channels, or the India Stack. */
export function IndiaChannelsCard({ result }: { result: any }) {
  if (result.applies === false) {
    return <div className="rounded-lg border border-border bg-surface px-3 py-2"><span className="chip text-ink-muted">Not an Indian firm</span></div>;
  }
  const stack = Array.isArray(result.items);
  return (
    <div className="overflow-hidden rounded-lg border border-border bg-surface" data-testid="india-channels-card">
      <div className="flex flex-wrap items-center justify-between gap-2 border-b border-border-soft px-3 py-2">
        <div className="text-sm font-medium text-ink">{stack ? "DigiLocker and eSign" : "Languages and WhatsApp"}</div>
        <div className="flex flex-wrap gap-1.5">{stack ? <StackChips data={result} /> : <ChannelChips data={result} />}</div>
      </div>
      {stack ? <StackRows items={result.items} /> : (
        <>
          <ChannelRows data={result} />
          {(result.templates ?? []).length > 0 && <div className="border-t border-border-soft"><TemplateRows templates={result.templates} /></div>}
        </>
      )}
    </div>
  );
}
