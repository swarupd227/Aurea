import { titleCase, timeAgo } from "@/lib/format";

// Acquired books, for `read_book_batches` (a list, or one batch) and `start_book_batch` (the batch it
// loaded). Figures, tables and chips only - the agent's message carries the sentences.

const Chip = ({ children, tone = "text-ink-muted" }: { children: React.ReactNode; tone?: string }) => (
  <span className={`inline-block rounded border border-border px-1.5 py-0.5 font-mono text-[11px] ${tone}`}>{children}</span>
);

function Tile({ label, value, tone = "text-ink" }: { label: string; value: React.ReactNode; tone?: string }) {
  return (
    <div className="px-3 py-2">
      <div className="font-mono text-[11px] uppercase tracking-wider text-ink-muted">{label}</div>
      <div className={`mt-0.5 font-mono text-lg tabular-nums ${tone}`}>{value}</div>
    </div>
  );
}

export interface BookBatchStats {
  clients?: number;
  merges?: number;
  new_clients?: number;
  securities?: number;
  unmapped_securities?: number;
  holdings?: number;
  conflicts?: number;
}

export interface BookBatchRow {
  id: string;
  source_firm: string;
  status: string;
  feed: { clients?: any[]; holdings?: any[]; [k: string]: any } | null;
  mappings: {
    client_mappings?: any[];
    security_mappings?: any[];
    holding_conflicts?: { client: string; symbol: string; inbound_quantity: number; existing_quantity: number; delta: number }[];
    capital_calls?: any[];
  } | null;
  stats: BookBatchStats | null;
  committed: { new_clients?: number; created_instruments?: number; holdings_written?: number } | null;
  created_at: string | null;
  recommendation_id?: string | null;
  recommendation_status?: string | null;
}

export interface BookBatchPayload {
  batches?: BookBatchRow[];
  batch?: BookBatchRow;
  message?: string;
}

const isBatchRow = (b: any) =>
  !!b && typeof b === "object" && typeof b.id === "string" && typeof b.source_firm === "string" && typeof b.status === "string";

export function isBookBatch(r: any): boolean {
  if (!r || typeof r !== "object") return false;
  if (Array.isArray(r.batches)) return r.batches.every(isBatchRow);
  return isBatchRow(r.batch);
}

const STATUS_TONE: Record<string, string> = {
  received: "text-ink-muted", reconciled: "text-warn", committed: "text-ok", rejected: "text-crit",
};

function BatchDetail({ b }: { b: BookBatchRow }) {
  const s = b.stats || {};
  const conflicts = b.mappings?.holding_conflicts || [];
  return (
    <>
      <div className="flex flex-wrap gap-1.5 border-b border-border-soft px-3 py-1.5">
        <Chip tone={STATUS_TONE[b.status]}>{titleCase(b.status)}</Chip>
        <Chip>Clients {b.feed?.clients?.length ?? 0}</Chip>
        <Chip>Holdings {b.feed?.holdings?.length ?? 0}</Chip>
        {b.recommendation_status && (
          <Chip tone={b.recommendation_status === "awaiting_approval" ? "text-warn" : undefined}>
            Proposal {titleCase(b.recommendation_status)}
          </Chip>
        )}
      </div>
      {b.stats && (
        <div className="flex flex-wrap border-b border-border-soft">
          <Tile label="Clients" value={s.clients ?? "—"} />
          <Tile label="Merges" value={s.merges ?? "—"} />
          <Tile label="New" value={s.new_clients ?? "—"} tone={s.new_clients ? "text-warn" : "text-ink"} />
          <Tile label="Unmapped" value={s.unmapped_securities ?? "—"} />
          <Tile label="Conflicts" value={s.conflicts ?? "—"} tone={s.conflicts ? "text-crit" : "text-ink"} />
        </div>
      )}
      {b.status === "committed" && b.committed && (
        <div className="flex flex-wrap gap-1.5 border-b border-border-soft px-3 py-1.5">
          <Chip tone="text-ok">New clients {b.committed.new_clients ?? 0}</Chip>
          <Chip tone="text-ok">Instruments {b.committed.created_instruments ?? 0}</Chip>
          <Chip tone="text-ok">Holdings {b.committed.holdings_written ?? 0}</Chip>
        </div>
      )}
      {conflicts.length > 0 && (
        <div className="overflow-x-auto">
          <table className="w-full text-sm">
            <thead className="text-[11px] uppercase tracking-wider text-ink-muted">
              <tr>
                <th className="px-3 py-1.5 text-left font-normal">Client</th>
                <th className="px-3 py-1.5 text-left font-normal">Security</th>
                <th className="px-3 py-1.5 text-right font-normal">Inbound</th>
                <th className="px-3 py-1.5 text-right font-normal">On file</th>
                <th className="px-3 py-1.5 text-right font-normal">Δ</th>
              </tr>
            </thead>
            <tbody className="divide-y divide-border-soft">
              {conflicts.map((c, i) => (
                <tr key={i}>
                  <td className="px-3 py-1.5 text-ink">{c.client}</td>
                  <td className="px-3 py-1.5 font-mono text-xs text-ink-soft">{c.symbol}</td>
                  <td className="px-3 py-1.5 text-right font-mono text-xs tabular-nums text-ink-muted">{c.inbound_quantity}</td>
                  <td className="px-3 py-1.5 text-right font-mono text-xs tabular-nums text-ink-muted">{c.existing_quantity}</td>
                  <td className={`px-3 py-1.5 text-right font-mono text-xs tabular-nums ${c.delta >= 0 ? "text-ok" : "text-crit"}`}>{c.delta}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </>
  );
}

export function BookBatchCard({ result }: { result: BookBatchPayload }) {
  const one = !result.batches && result.batch ? result.batch : null;
  const rows = result.batches ?? [];
  return (
    <div className="overflow-hidden rounded-lg border border-border bg-surface" data-testid="book-batch-card">
      <div className="flex items-center justify-between gap-2 border-b border-border-soft px-3 py-2">
        <div className="min-w-0 truncate text-sm font-medium text-ink">{one ? one.source_firm : "Book integration"}</div>
        <div className="shrink-0 font-mono text-xs tabular-nums text-ink-muted">{one ? timeAgo(one.created_at) : rows.length}</div>
      </div>
      {one ? (
        <BatchDetail b={one} />
      ) : (
        <table className="w-full text-sm">
          <tbody className="divide-y divide-border-soft">
            {rows.map((b) => (
              <tr key={b.id}>
                <td className="px-3 py-1.5">
                  <div className="text-ink">{b.source_firm}</div>
                  <div className="font-mono text-xs tabular-nums text-ink-muted">
                    {b.feed?.clients?.length ?? 0} clients · {b.feed?.holdings?.length ?? 0} holdings
                  </div>
                </td>
                <td className="px-3 py-1.5 text-right">
                  <div className="flex flex-wrap justify-end gap-1">
                    <Chip tone={STATUS_TONE[b.status]}>{titleCase(b.status)}</Chip>
                    {b.stats?.conflicts ? <Chip tone="text-crit">Conflicts {b.stats.conflicts}</Chip> : null}
                  </div>
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      )}
    </div>
  );
}
