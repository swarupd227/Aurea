"use client";
import { useParams } from "next/navigation";
import { ClientView } from "@/components/views/ClientView";

// The page and the workspace's work pane render the same view (components/views).
export default function ClientDetail() {
  const { id } = useParams<{ id: string }>();
  return <ClientView id={id} />;
}
