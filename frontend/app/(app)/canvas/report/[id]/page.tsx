"use client";
import { useParams } from "next/navigation";
import Link from "next/link";
import { FileText, ArrowLeft } from "lucide-react";
import { Card, Spinner } from "@/components/ui";
import { AllocationDonut } from "@/components/Charts";
import { useApi } from "@/lib/hooks";

export default function CanvasReport() {
  const { id } = useParams<{ id: string }>();
  const { data: r, loading } = useApi<any>(`/api/canvas/reports/${id}`, [id]);
  if (loading || !r) return <Spinner />;
  return (
    <div className="max-w-3xl mx-auto">
      <Link href="/canvas" className="text-sm text-ink-muted hover:text-navy-700 flex items-center gap-1 mb-4"><ArrowLeft size={15} /> Back</Link>
      <Card className="p-8">
        <h1 className="font-serif text-2xl text-ink">{r.title}</h1>
        <div className="text-sm text-ink-muted mb-6">{r.period}</div>
        {r.data?.allocation && (
          <div className="mb-6 pb-6 border-b border-navy-100"><AllocationDonut data={r.data.allocation} size={160} /></div>
        )}
        <div className="space-y-6">
          {r.sections?.map((s: any, i: number) => (
            <div key={i}>
              <h3 className="font-serif text-lg text-ink mb-1.5 flex items-center gap-2"><FileText size={15} className="text-gold" /> {s.heading}</h3>
              <p className="text-sm text-ink-soft leading-relaxed whitespace-pre-line">{s.body}</p>
            </div>
          ))}
        </div>
      </Card>
    </div>
  );
}
