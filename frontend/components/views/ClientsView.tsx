"use client";

import { useMemo, useState } from "react";
import { ChevronDown, ChevronRight, ChevronUp, Search } from "lucide-react";
import { Segment, SkeletonList, Empty, ErrorState } from "@/components/ui";
import { useApi } from "@/lib/hooks";
import { money } from "@/lib/format";
import { ViewLink } from "./ViewNav";
import { ViewHeader, chip } from "./ViewHeader";

type SortKey = "name" | "value" | "segment";
type SortDir = "asc" | "desc";
const SORT_LABEL: Record<SortKey, string> = { name: "Name", value: "AUM", segment: "Segment" };

/** Every household, entity and trust the caller's firm holds. A row opens that client - in the
 * work pane when the view is in the pane, as its page otherwise. */
export function ClientsView() {
  const { data, loading, error, refetch } = useApi<any[]>("/api/core/households");
  const [search, setSearch] = useState("");
  const [sortKey, setSortKey] = useState<SortKey>("name");
  const [sortDir, setSortDir] = useState<SortDir>("asc");

  function toggleSort(key: SortKey) {
    if (sortKey === key) setSortDir((d) => (d === "asc" ? "desc" : "asc"));
    else {
      setSortKey(key);
      setSortDir(key === "value" ? "desc" : "asc");
    }
  }

  const rows = useMemo(() => {
    if (!data) return [];
    const q = search.trim().toLowerCase();
    const result = data.filter((h) => h.name.toLowerCase().includes(q));
    return [...result].sort((a, b) => {
      let cmp = 0;
      if (sortKey === "name") cmp = a.name.localeCompare(b.name);
      else if (sortKey === "value") cmp = (a.total_value || 0) - (b.total_value || 0);
      else cmp = (a.segment || "").localeCompare(b.segment || "");
      return sortDir === "asc" ? cmp : -cmp;
    });
  }, [data, search, sortKey, sortDir]);

  const total = (data || []).reduce((s, h) => s + (h.total_value || 0), 0);

  return (
    <div className="view-container">
      <ViewHeader
        title="Clients"
        meta={
          data ? (
            <>
              <span className={chip}>{data.length} households</span>
              <span className={`${chip} tabular-nums`}>{money(total)}</span>
            </>
          ) : null
        }
      />

      <div className="mb-3 flex flex-col gap-2 cq-sm:flex-row cq-sm:items-center">
        <div className="relative flex-1 cq-sm:max-w-xs">
          <Search size={15} className="pointer-events-none absolute left-3 top-1/2 -translate-y-1/2 text-ink-muted" aria-hidden />
          <input
            className="input py-1.5 pl-9 text-sm"
            placeholder="Search clients"
            aria-label="Search clients"
            value={search}
            onChange={(e) => setSearch(e.target.value)}
          />
        </div>
        <div className="flex items-center gap-1 text-xs" role="group" aria-label="Sort">
          {(["name", "value", "segment"] as SortKey[]).map((k) => (
            <button
              key={k}
              onClick={() => toggleSort(k)}
              aria-pressed={sortKey === k}
              className={`flex items-center gap-0.5 rounded px-2 py-1 transition-colors ${
                sortKey === k ? "bg-border-soft text-ink" : "text-ink-muted hover:text-ink"
              }`}
            >
              {SORT_LABEL[k]}
              {sortKey !== k ? (
                <ChevronDown size={13} className="opacity-30" />
              ) : sortDir === "asc" ? (
                <ChevronUp size={13} />
              ) : (
                <ChevronDown size={13} />
              )}
            </button>
          ))}
        </div>
      </div>

      {loading ? (
        <SkeletonList count={6} />
      ) : error ? (
        <div className="rounded-lg border border-border bg-surface p-8">
          <ErrorState what="your clients" message={error} onRetry={refetch} />
        </div>
      ) : !rows.length ? (
        <div className="rounded-lg border border-border bg-surface p-8">
          <Empty>{search ? `No clients matching "${search}"` : "No clients"}</Empty>
        </div>
      ) : (
        <ul className="divide-y divide-border-soft overflow-hidden rounded-lg border border-border bg-surface">
          {rows.map((h) => (
            <li key={h.id}>
              <ViewLink
                href={`/studio/clients/${h.id}`}
                data-testid="client-row"
                className="group flex items-center gap-3 px-4 py-3 transition-colors hover:bg-border-soft/60 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-inset focus-visible:ring-accent"
              >
                <span
                  aria-hidden
                  className="flex h-9 w-9 shrink-0 items-center justify-center rounded bg-border-soft font-semibold text-ink-soft"
                >
                  {h.name.replace(/^The /, "").slice(0, 1)}
                </span>
                <span className="min-w-0 flex-1">
                  <span className="block truncate font-medium text-ink">{h.name}</span>
                  <span className="mt-1 flex flex-wrap items-center gap-1.5">
                    {h.segment && <Segment>{h.segment}</Segment>}
                    {h.total_value >= 5_000_000 && <span className={`${chip} text-[10px]`}>Qualified Purchaser</span>}
                    {h.total_value >= 1_000_000 && h.total_value < 5_000_000 && (
                      <span className={`${chip} text-[10px]`}>Accredited Investor</span>
                    )}
                  </span>
                </span>
                <span className="shrink-0 text-right font-semibold tabular-nums text-ink">{money(h.total_value)}</span>
                <ChevronRight size={16} className="shrink-0 text-ink-faint transition-colors group-hover:text-ink" aria-hidden />
              </ViewLink>
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}
