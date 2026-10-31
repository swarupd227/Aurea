import { CHANNEL_TEXT } from "@/components/ConsentPanel";

const SHOWN = 6;

export function LicenceChips({ l }: { l: any }) {
  return (
    <>
      <span className={`chip ${l.may_execute ? "text-ok" : "text-warn"}`}>{l.may_execute ? "May execute" : "May not execute"}</span>
      <span className={`chip ${l.per_trade_consent ? "text-warn" : "text-ink-muted"}`}>
        {l.per_trade_consent ? "Client consents to every trade" : "No per-trade consent"}
      </span>
    </>
  );
}

function Answer({ result }: { result: any }) {
  const c = result.client_consent;
  return (
    <div className="flex flex-wrap items-center gap-1.5 rounded-lg border border-border bg-surface px-3 py-2" data-testid="consent-record-card">
      <span className={`chip ${c.status === "given" ? "text-ok" : "text-warn"}`}>Client {c.status === "given" ? "consented" : "declined"}</span>
      <span className="chip text-ink-muted">{CHANNEL_TEXT[c.channel] ?? c.channel}</span>
      {c.simulated && <span className="chip text-warn">Simulated</span>}
      <span className="chip text-ink-muted">{result.status}</span>
      {result.execution_result?.orders_settled != null && (
        <span className="chip text-ink-muted">{result.execution_result.orders_settled} orders settled</span>
      )}
    </div>
  );
}

/** The firm's licences, what they allow, and which proposals are waiting for a client. */
export function LicenceCard({ result }: { result: any }) {
  if (result.client_consent) return <Answer result={result} />;
  const waiting: any[] = result.awaiting_consent || [];
  const mandates: any[] = result.mandates || [];
  return (
    <div className="overflow-hidden rounded-lg border border-border bg-surface" data-testid="licence-card">
      <div className="flex flex-wrap items-center justify-between gap-2 border-b border-border-soft px-3 py-2">
        <div className="text-sm font-medium text-ink">Licences</div>
        <div className="flex flex-wrap gap-1.5">
          {result.ceiling && <span className="chip text-ink-muted">Agents capped at {result.ceiling.replace("_", " ")}</span>}
          <span className={`chip ${waiting.length ? "text-warn" : "text-ink-muted"}`}>{waiting.length} waiting on clients</span>
        </div>
      </div>
      <div className="divide-y divide-border-soft">
        {result.licences.map((l: any) => (
          <div key={l.key} className="flex flex-wrap items-center gap-1.5 px-3 py-2" title={l.citation}>
            <span className="text-sm text-ink mr-auto">{l.name}</span>
            <LicenceChips l={l} />
            <span className="chip text-ink-muted">{mandates.filter((m) => m.licence === l.key).length} mandates</span>
          </div>
        ))}
      </div>
      {waiting.length > 0 && (
        <div className="border-t border-border-soft">
          {waiting.slice(0, SHOWN).map((c) => (
            <div key={c.id} className="flex flex-wrap items-center gap-1.5 px-3 py-1.5 text-sm">
              <span className="text-ink-soft mr-auto">{c.subject}</span>
              <span className="chip text-warn">{(c.orders || []).length} orders held</span>
            </div>
          ))}
          {waiting.length > SHOWN && <div className="px-3 pb-2"><span className="chip text-ink-muted">+{waiting.length - SHOWN} more</span></div>}
        </div>
      )}
    </div>
  );
}
