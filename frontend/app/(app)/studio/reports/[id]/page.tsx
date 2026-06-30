"use client";
import { useParams } from "next/navigation";
import { FileText } from "lucide-react";
import PageHeader from "@/components/PageHeader";
import { Breadcrumb, Card, Spinner, StatusBadge } from "@/components/ui";
import { AllocationDonut } from "@/components/Charts";
import { useApi } from "@/lib/hooks";

export default function ReportDetail() {
  const { id } = useParams<{ id: string }>();
  const { data: r, loading } = useApi<any>(`/api/engage/reports/${id}`, [id]);
  if (loading || !r) return <Spinner />;

  return (
    <div className="max-w-3xl mx-auto">
      <Breadcrumb items={[{ label: "Reports", href: "/studio/reports" }, { label: r.title }]} />
      <PageHeader title={r.title} sub={`${r.household_name} · ${r.period}`} actions={<StatusBadge status={r.status} />} />
      <Card className="p-8">
        {r.data?.allocation && (
          <div className="mb-6 pb-6 border-b border-navy-100">
            <AllocationDonut data={r.data.allocation} size={160} />
          </div>
        )}
        <div className="space-y-6">
          {r.sections?.map((s: any, i: number) => (
            <div key={i}>
              <h3 className="font-serif text-lg text-ink mb-1.5 flex items-center gap-2"><FileText size={15} className="text-gold" /> {s.heading}</h3>
              <p className="text-sm text-ink-soft leading-relaxed whitespace-pre-line">{s.body}</p>
            </div>
          ))}
        </div>
        {r.status === "client_ready" && (
          <div className="mt-6 pt-4 border-t border-navy-100 text-xs text-positive">✓ Approved as client-ready{r.published_at ? ` · ${new Date(r.published_at).toLocaleDateString()}` : ""}.</div>
        )}
      </Card>
    </div>
  );
}
