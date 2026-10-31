"use client";
import { useParams } from "next/navigation";
import { ReportView } from "@/components/views/ReportView";

export default function ReportDetail() {
  const { id } = useParams<{ id: string }>();
  return <ReportView key={id} id={id} />;
}
