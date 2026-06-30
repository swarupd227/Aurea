"use client";
import { useState } from "react";
import PageHeader from "@/components/PageHeader";
import RecommendationCard from "@/components/RecommendationCard";
import { SkeletonList, Empty } from "@/components/ui";
import { useApi } from "@/lib/hooks";
import { titleCase } from "@/lib/format";

export default function ReviewQueue() {
  const [status, setStatus] = useState("open");
  const { data, loading, refetch } = useApi<any[]>(`/api/studio/feed?status=${status}`, [status]);

  return (
    <div>
      <PageHeader
        title="Recommendations"
        sub="Approve, modify or dismiss agent recommendations. Nothing takes effect without you."
        actions={
          <div className="flex rounded-xl border border-navy-200 overflow-hidden text-sm">
            {["open", "all"].map((s) => (
              <button
                key={s}
                onClick={() => setStatus(s)}
                className={`px-3 py-1.5 ${status === s ? "bg-navy-800 text-white" : "bg-surface text-ink-soft"}`}
              >
                {titleCase(s)}
              </button>
            ))}
          </div>
        }
      />
      {loading ? (
        <SkeletonList count={3} />
      ) : !data?.length ? (
        <div className="card p-8"><Empty>No recommendations to show.</Empty></div>
      ) : (
        <div className="space-y-3">
          {data.map((r) => (
            <RecommendationCard key={r.id} rec={r} onDecided={() => refetch()} />
          ))}
        </div>
      )}
    </div>
  );
}
