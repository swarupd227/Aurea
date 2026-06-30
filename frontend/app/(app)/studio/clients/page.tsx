"use client";
import { useMemo, useState } from "react";
import Link from "next/link";
import { ChevronDown, ChevronRight, ChevronUp, Search } from "lucide-react";
import PageHeader from "@/components/PageHeader";
import { Segment, SkeletonList, Empty } from "@/components/ui";
import { useApi } from "@/lib/hooks";
import { money } from "@/lib/format";

// Stable color per initial letter — 8 distinct hues
const AVATAR_COLORS = [
  "bg-[#1d4663]", "bg-[#3f8a72]", "bg-[#7c6a9c]", "bg-[#b9852b]",
  "bg-[#4a6fa5]", "bg-[#5d8a5e]", "bg-[#9c526a]", "bg-[#557a8c]",
];
function avatarColor(name: string) {
  const c = name.replace(/^The /, "").charCodeAt(0) || 0;
  return AVATAR_COLORS[c % AVATAR_COLORS.length];
}

type SortKey = "name" | "value" | "segment";
type SortDir = "asc" | "desc";

export default function Clients() {
  const { data, loading } = useApi<any[]>("/api/core/households");
  const [search, setSearch] = useState("");
  const [sortKey, setSortKey] = useState<SortKey>("name");
  const [sortDir, setSortDir] = useState<SortDir>("asc");

  function toggleSort(key: SortKey) {
    if (sortKey === key) setSortDir((d) => (d === "asc" ? "desc" : "asc"));
    else { setSortKey(key); setSortDir("asc"); }
  }

  const rows = useMemo(() => {
    if (!data) return [];
    let result = data.filter((h) => h.name.toLowerCase().includes(search.toLowerCase()));
    result = [...result].sort((a, b) => {
      let cmp = 0;
      if (sortKey === "name") cmp = a.name.localeCompare(b.name);
      else if (sortKey === "value") cmp = (a.total_value || 0) - (b.total_value || 0);
      else if (sortKey === "segment") cmp = (a.segment || "").localeCompare(b.segment || "");
      return sortDir === "asc" ? cmp : -cmp;
    });
    return result;
  }, [data, search, sortKey, sortDir]);

  function SortIcon({ k }: { k: SortKey }) {
    if (sortKey !== k) return <ChevronDown size={13} className="opacity-30" />;
    return sortDir === "asc" ? <ChevronUp size={13} /> : <ChevronDown size={13} />;
  }

  return (
    <div>
      <PageHeader title="Clients" sub="Households, entities and trusts — one view per relationship." />

      {/* Search + sort bar */}
      <div className="flex items-center gap-3 mb-4">
        <div className="relative flex-1 max-w-xs">
          <Search size={15} className="absolute left-3 top-1/2 -translate-y-1/2 text-ink-muted pointer-events-none" />
          <input
            className="input pl-9 py-1.5 text-sm"
            placeholder="Search clients…"
            value={search}
            onChange={(e) => setSearch(e.target.value)}
          />
        </div>
        <div className="flex items-center gap-1 text-xs text-ink-muted">
          <span className="hidden sm:inline">Sort:</span>
          {(["name", "value", "segment"] as SortKey[]).map((k) => (
            <button
              key={k}
              onClick={() => toggleSort(k)}
              className={`flex items-center gap-0.5 px-2 py-1 rounded-lg transition capitalize
                ${sortKey === k ? "bg-navy-100 text-ink font-medium" : "hover:bg-navy-50 text-ink-muted"}`}
            >
              {k === "value" ? "AUM" : k} <SortIcon k={k} />
            </button>
          ))}
        </div>
      </div>

      {loading ? (
        <SkeletonList count={6} />
      ) : !rows.length ? (
        <div className="card p-8">
          <Empty>{search ? `No clients matching "${search}".` : "No clients."}</Empty>
        </div>
      ) : (
        <div className="grid gap-3">
          {rows.map((h) => (
            <Link key={h.id} href={`/studio/clients/${h.id}`} className="card p-5 flex items-center gap-4 hover:shadow-lift transition group">
              <div className={`h-11 w-11 rounded-xl ${avatarColor(h.name)} text-white flex items-center justify-center font-semibold text-base shrink-0`}>
                {h.name.replace(/^The /, "").slice(0, 1)}
              </div>
              <div className="flex-1 min-w-0">
                <div className="font-semibold text-ink">{h.name}</div>
                <div className="mt-1"><Segment>{h.segment}</Segment></div>
              </div>
              <div className="text-right shrink-0">
                <div className="text-lg font-semibold text-ink tabular-nums">{money(h.total_value)}</div>
                <div className="text-xs text-ink-muted">Total portfolio</div>
              </div>
              <ChevronRight className="text-navy-300 group-hover:text-navy-600 transition" />
            </Link>
          ))}
        </div>
      )}
    </div>
  );
}
