/** Outside agents that call Astra's tools through MCP with a person's key (aurea_core/outside_agents.py, B-166): each key's
 * name, last four characters, state, use and expiry. A key's value never appears here. Table and chips only. */
export interface OutsideAgentKey {
  id: string; label: string; key_ends: string; state: "active" | "expired" | "revoked"; owner: string | null;
  made: string | null; expires: string; last_used: string | null; calls: number;
  revoked_by: string | null; revoked_reason: string | null;
}

const day = (iso: string | null) => (iso ? new Date(iso).toLocaleDateString(undefined, { day: "numeric", month: "short", year: "numeric" }) : "—");
const tone = { active: "text-ok", expired: "text-ink-muted", revoked: "text-crit" } as const;

export function OutsideAgentsTable({ rows, onRevoke }: { rows: OutsideAgentKey[]; onRevoke?: (row: OutsideAgentKey) => void }) {
  return (
    <div className="overflow-x-auto">
      <table className="w-full text-sm">
        <thead>
          <tr className="text-left font-mono text-[11px] uppercase tracking-wider text-ink-muted">
            <th className="px-3 py-1.5 font-normal">Agent</th>
            <th className="px-3 py-1.5 font-normal">State</th>
            <th className="px-3 py-1.5 text-right font-normal">Calls</th>
            <th className="px-3 py-1.5 font-normal">Last used</th>
            <th className="px-3 py-1.5 font-normal">Expires</th>
            {onRevoke && <th className="px-3 py-1.5" />}
          </tr>
        </thead>
        <tbody className="divide-y divide-border-soft">
          {rows.map((k) => (
            <tr key={k.id} data-testid="outside-agent-row">
              <td className="px-3 py-1.5">
                <div className="text-ink">{k.label}</div>
                <div className="font-mono text-xs text-ink-muted">…{k.key_ends}{k.owner ? ` · ${k.owner}` : ""}</div>
              </td>
              <td className="px-3 py-1.5 whitespace-nowrap">
                <span className={`chip ${tone[k.state]}`} title={k.revoked_reason ? `${k.revoked_by}: ${k.revoked_reason}` : undefined}>
                  {k.state}
                </span>
              </td>
              <td className="px-3 py-1.5 text-right tabular-nums">{k.calls}</td>
              <td className="px-3 py-1.5 whitespace-nowrap text-xs text-ink-muted">{day(k.last_used)}</td>
              <td className="px-3 py-1.5 whitespace-nowrap text-xs text-ink-muted">{day(k.expires)}</td>
              {onRevoke && (
                <td className="px-3 py-1.5 text-right">
                  {k.state === "active" && (
                    <button type="button" className="btn-outline px-2 py-0.5 text-xs" onClick={() => onRevoke(k)}
                      data-testid="outside-agent-revoke">
                      Revoke
                    </button>
                  )}
                </td>
              )}
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

export function OutsideAgentsCard({ result }: { result: any }) {
  const rows: OutsideAgentKey[] = Array.isArray(result.keys) ? result.keys : [result];
  return (
    <div className="overflow-hidden rounded-lg border border-border bg-surface" data-testid="outside-agents-card">
      <div className="flex flex-wrap items-center gap-2 border-b border-border-soft px-3 py-2">
        <div className="text-sm font-medium text-ink">Outside agents</div>
        {"on" in result && (
          <span className={`chip ${result.on ? "text-ok" : "text-ink-muted"}`}>{result.on ? "on for the firm" : "off for the firm"}</span>
        )}
        {"active" in result && <span className="chip text-ink-muted tabular-nums">{result.active} active</span>}
      </div>
      {rows.length > 0 && <OutsideAgentsTable rows={rows} />}
    </div>
  );
}
