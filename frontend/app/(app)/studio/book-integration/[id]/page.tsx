"use client";
import { useParams } from "next/navigation";
import { BookBatchView } from "@/components/views/BookBatchView";

// The page and the workspace's work pane render the same view (components/views).
export default function BatchDetail() {
  const { id } = useParams<{ id: string }>();
  return <BookBatchView key={id} id={id} />;
}
