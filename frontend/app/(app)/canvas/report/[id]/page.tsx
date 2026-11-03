"use client";
import { useParams } from "next/navigation";
import Link from "next/link";
import { ArrowLeft, FileText } from "lucide-react";
import { Card, ErrorState, SkeletonList } from "@/components/ui";
import { AllocationDonut } from "@/components/Charts";
import { getUser } from "@/lib/api";
import { useApi } from "@/lib/hooks";

/** One report published to the client. Its sections are the report's own published text. */
export default function CanvasReport() {
  const { id } = useParams<{ id: string }>();
  const { data: r, loading, error, refetch } = useApi<any>(`/api/canvas/reports/${id}`, [id]);
  const isClient = getUser()?.role === "client";
  const back = !isClient && r?.household_id ? `/canvas?household=${r.household_id}` : "/canvas";

  return (
    <div className="mx-auto max-w-3xl" data-testid="canvas-report">
      <Link href={back} className="mb-4 inline-flex items-center gap-1 text-sm text-ink-muted hover:text-ink">
        <ArrowLeft size={15} aria-hidden /> Back
      </Link>
      {error ? <Card><ErrorState what="the report" message={error} onRetry={refetch} /></Card>
        : loading || !r ? <SkeletonList count={3} />
        : (
          <Card className="p-6">
            <h1 className="text-2xl font-semibold tracking-tight text-ink">{r.title}</h1>
            {r.period && <div className="mt-2 flex flex-wrap gap-1.5"><span className="chip text-ink-muted">{r.period}</span></div>}
            {r.data?.allocation && (
              <div className="mt-6 border-b border-border-soft pb-6"><AllocationDonut data={r.data.allocation} size={160} /></div>
            )}
            <div className="mt-6 space-y-6">
              {r.sections?.map((s: any, i: number) => (
                <section key={i}>
                  <h2 className="mb-1.5 flex items-center gap-2 text-base font-medium text-ink"><FileText size={15} className="text-ink-muted" aria-hidden /> {s.heading}</h2>
                  <p className="whitespace-pre-line text-sm leading-relaxed text-ink-soft">{s.body}</p>
                </section>
              ))}
            </div>
          </Card>
        )}
    </div>
  );
}
