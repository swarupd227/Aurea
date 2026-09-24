interface CompositeRow {
  composite_id: string;
  name: string;
  strategy: string | null;
  status: string;
}

interface CompositesPayload {
  composites: CompositeRow[];
}

export function CompositesCard({ result }: { result: CompositesPayload }) {
  return (
    <div className="overflow-hidden rounded-lg border border-border bg-surface">
      <div className="flex items-center justify-between border-b border-border-soft px-3 py-2">
        <div className="text-sm font-medium text-ink">Composites</div>
        <div className="font-mono text-xs tabular-nums text-ink-muted">{result.composites.length}</div>
      </div>
      <table className="w-full text-sm">
        <tbody className="divide-y divide-border-soft">
          {result.composites.map((c) => (
            <tr key={c.composite_id}>
              <td className="px-3 py-1.5 text-ink">{c.name}</td>
              <td className="px-3 py-1.5 text-right text-xs text-ink-muted whitespace-nowrap">{c.strategy}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}
