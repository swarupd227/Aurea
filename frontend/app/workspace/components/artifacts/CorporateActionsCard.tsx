const STATUS_COLOR: Record<string, string> = {
  announced: "text-ink-muted", election_open: "text-warn", election_closed: "text-warn",
  posted: "text-ok", verified: "text-ok",
};

interface ActionRow {
  id: string;
  symbol: string | null;
  action_type: string;
  status: string;
  is_voluntary: boolean;
  entitlement_count: number;
  entitlements_posted: number;
}

interface ActionsPayload {
  actions: ActionRow[];
}

export function CorporateActionsCard({ result }: { result: ActionsPayload }) {
  return (
    <div className="overflow-hidden rounded-lg border border-border bg-surface">
      <div className="flex items-center justify-between border-b border-border-soft px-3 py-2">
        <div className="text-sm font-medium text-ink">Corporate actions</div>
        <div className="font-mono text-xs tabular-nums text-ink-muted">{result.actions.length} action(s)</div>
      </div>
      <table className="w-full text-sm">
        <tbody className="divide-y divide-border-soft">
          {result.actions.map((a) => (
            <tr key={a.id}>
              <td className="px-3 py-1.5">
                <div className="text-ink">{a.symbol || "—"}</div>
                <div className="text-xs text-ink-muted">
                  {a.action_type.replace(/_/g, " ")}
                  {a.is_voluntary ? " · voluntary" : ""}
                </div>
              </td>
              <td className={`px-3 py-1.5 text-xs ${STATUS_COLOR[a.status] || "text-ink-muted"}`}>
                {a.status.replace(/_/g, " ")}
              </td>
              <td className="px-3 py-1.5 text-right font-mono text-xs tabular-nums text-ink-muted whitespace-nowrap">
                {a.entitlements_posted} / {a.entitlement_count} posted
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}
