"use client";
import Link from "next/link";
import { FileText, ChevronRight } from "lucide-react";
import PageHeader from "@/components/PageHeader";
import { Card, SkeletonList, StatusBadge, Empty } from "@/components/ui";
import { useApi } from "@/lib/hooks";

export default function Reports() {
  const { data, loading } = useApi<any[]>("/api/engage/reports");
  return (
    <div>
      <PageHeader
        title="Client reports"
        sub="Client-ready reports. Generate from a client page, then review and publish here."
      />
      {loading ? (
        <SkeletonList count={3} />
      ) : !data?.length ? (
        <Card><Empty>No reports yet. Open a client and run “Research note”.</Empty></Card>
      ) : (
        <div className="grid gap-3">
          {data.map((r) => (
            <Link key={r.id} href={`/studio/reports/${r.id}`} className="card p-5 flex items-center gap-4 hover:shadow-lift transition group">
              <div className="h-11 w-11 rounded-xl bg-navy-800 text-white flex items-center justify-center"><FileText size={18} /></div>
              <div className="flex-1">
                <div className="font-semibold text-ink">{r.title}</div>
                <div className="text-xs text-ink-muted mt-1">{r.household_name} · {r.period} · {r.n_sections} sections</div>
              </div>
              <StatusBadge status={r.status} />
              <ChevronRight className="text-navy-300 group-hover:text-navy-600" />
            </Link>
          ))}
        </div>
      )}
    </div>
  );
}
