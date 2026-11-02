const tone: Record<string, string> = { critical: "text-critical", high: "text-warn", medium: "text-ink-muted", low: "text-ink-muted" };

export function DocumentMatrixCard({ result }: { result: any }) {
  const check = result.check;
  return (
    <div className="overflow-hidden rounded-lg border border-border bg-surface" data-testid="document-matrix-card">
      <div className="flex flex-wrap items-center justify-between gap-2 border-b border-border-soft px-3 py-2">
        <div className="text-sm font-medium text-ink">{check ? check.case ?? "Registration check" : "Document matrix"}</div>
        {check
          ? <span className={`chip ${check.in_good_order ? "text-ok" : "text-critical"}`}>{check.in_good_order ? "in good order" : `${check.issues.length} issue(s)`}</span>
          : <span className="chip">{result.registrations.length} registrations</span>}
      </div>
      {check ? (
        <div className="px-3 py-2 text-xs">
          <div className="mb-2 flex flex-wrap gap-1.5">
            <span className="chip">{check.registration_type.replace(/_/g, " ")}</span>
            {check.requirements.documents.map((d: any) => <span key={d.key} className={`chip ${d.on_file ? "text-ok" : "text-critical"}`}>{d.label}</span>)}
            {check.requirements.fields.map((f: any) => <span key={f.field} className={`chip ${f.value != null && f.value !== "" ? "text-ok" : "text-warn"}`}>{f.label}</span>)}
          </div>
          {check.issues.length > 0 && (
            <table className="w-full"><tbody className="divide-y divide-border-soft">
              {check.issues.map((i: any, n: number) => (
                <tr key={n}><td className={`py-1 pr-2 ${tone[i.severity]}`}>{i.severity}</td><td className="py-1 pr-2 text-ink-muted">{String(i.root_cause).replace(/_/g, " ")}</td><td className="py-1 text-ink">{i.description}</td></tr>
              ))}
            </tbody></table>
          )}
        </div>
      ) : (
        <div className="flex flex-wrap gap-1.5 px-3 py-2">
          {Object.entries(result.custodians as Record<string, string[]>).map(([c, f]) => <span key={c} className="chip">{c}: {f.length} form(s)</span>)}
          <span className="chip text-ink-muted">signed within {result.stale_days} days</span>
        </div>
      )}
    </div>
  );
}
