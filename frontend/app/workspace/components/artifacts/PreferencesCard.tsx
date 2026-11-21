/** What people asked the agents to remember (aurea_core/decision_memory.py, B-204): holds and notes, in their own
 * words, with who and until when. Table and chips only. */
export interface PreferenceRow {
  id: string; agent_key: string; subject: string | null; kind: "hold" | "note"; text: string; until: string | null;
  made_by: string; made_on: string | null; ended?: boolean;
}

const label = (k: string) => k.replace(/_/g, " ");

export function PreferencesTable({ rows }: { rows: PreferenceRow[] }) {
  return (
    <table className="w-full text-sm">
      <tbody className="divide-y divide-border-soft">
        {rows.map((p) => (
          <tr key={p.id} data-testid="preference-row">
            <td className="px-3 py-1.5 whitespace-nowrap">
              <span className={`chip ${p.kind === "hold" ? "text-warn" : "text-ink-muted"}`}>{p.kind}</span>
            </td>
            <td className="px-3 py-1.5">
              <div className="text-ink">{p.text}</div>
              <div className="text-xs text-ink-muted">{label(p.agent_key)} · {p.subject}</div>
            </td>
            <td className="px-3 py-1.5 text-right text-xs text-ink-muted whitespace-nowrap">
              {p.made_by}{p.until ? ` · until ${p.until}` : ""}{p.ended ? " · ended" : ""}
            </td>
          </tr>
        ))}
      </tbody>
    </table>
  );
}

export function PreferencesCard({ result }: { result: any }) {
  const rows: PreferenceRow[] = Array.isArray(result.preferences) ? result.preferences : [result];
  return (
    <div className="overflow-hidden rounded-lg border border-border bg-surface" data-testid="preferences-card">
      <div className="flex flex-wrap items-center gap-2 border-b border-border-soft px-3 py-2">
        <div className="text-sm font-medium text-ink">Remembered from decisions</div>
        {"holds" in result && <span className="chip text-ink-muted tabular-nums">{result.holds} hold{result.holds === 1 ? "" : "s"} · {result.notes} note{result.notes === 1 ? "" : "s"}</span>}
      </div>
      {rows.length > 0 && <PreferencesTable rows={rows} />}
    </div>
  );
}
