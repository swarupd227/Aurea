export function ConsentsCard({ result }: { result: any }) {
  const standing = result.standing ?? {};
  return (
    <div className="overflow-hidden rounded-lg border border-border bg-surface" data-testid="consents-card">
      <div className="flex flex-wrap items-center justify-between gap-2 border-b border-border-soft px-3 py-2">
        <div className="text-sm font-medium text-ink">Consents</div>
        {result.redeliveries_due.length > 0 && <span className="chip text-warn">{result.redeliveries_due.length} due again</span>}
      </div>
      <div className="flex flex-wrap gap-1.5 px-3 py-2">
        {result.standing
          ? result.kinds.map((k: any) => <span key={k.kind} className={`chip ${standing[k.kind]?.status === "granted" ? "text-ok" : "text-ink-muted"}`}>{k.label} {standing[k.kind]?.status ?? "not on file"}</span>)
          : <span className="chip text-ink-muted">{result.records.length} recorded</span>}
      </div>
    </div>
  );
}
