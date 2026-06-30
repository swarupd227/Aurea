"use client";
import { useState } from "react";
import Link from "next/link";
import { GitMerge, ChevronRight, Plus } from "lucide-react";
import PageHeader from "@/components/PageHeader";
import { Card, Spinner, StatusBadge } from "@/components/ui";
import { useApi } from "@/lib/hooks";
import { api } from "@/lib/api";

export default function BookIntegration() {
  const { data, loading, refetch } = useApi<any[]>("/api/onboarding/book-batches");
  const [busy, setBusy] = useState(false);

  async function newBatch() {
    setBusy(true);
    try {
      await api("/api/onboarding/book-batches", { body: { source_firm: "Northbridge Advisory" } });
      refetch();
    } finally {
      setBusy(false);
    }
  }

  return (
    <div>
      <PageHeader
        title="Book integration"
        sub="Map an acquired book into the client brain. Conflicts are flagged for review before committing."
        actions={<button className="btn-gold" disabled={busy} onClick={newBatch}><Plus size={16} /> {busy ? "Loading…" : "New acquisition"}</button>}
      />
      {loading ? (
        <Spinner />
      ) : !data?.length ? (
        <Card><div className="text-sm text-ink-muted py-6 text-center">No acquired books yet.</div></Card>
      ) : (
        <div className="grid gap-3">
          {data.map((b) => (
            <Link key={b.id} href={`/studio/book-integration/${b.id}`} className="card p-5 flex items-center gap-4 hover:shadow-lift transition group">
              <div className="h-11 w-11 rounded-xl bg-navy-800 text-white flex items-center justify-center"><GitMerge size={18} /></div>
              <div className="flex-1">
                <div className="font-semibold text-ink">{b.source_firm}</div>
                <div className="text-xs text-ink-muted mt-1">
                  {b.feed?.clients?.length || 0} clients · {b.feed?.holdings?.length || 0} holdings
                  {b.stats?.conflicts ? ` · ${b.stats.conflicts} conflict(s)` : ""}
                </div>
              </div>
              <StatusBadge status={b.status} />
              <ChevronRight className="text-navy-300 group-hover:text-navy-600" />
            </Link>
          ))}
        </div>
      )}
    </div>
  );
}
