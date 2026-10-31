"use client";
import { useParams } from "next/navigation";
import { MessageThreadView } from "@/components/views/MessageThreadView";

export default function StudioMessageThread() {
  const { id } = useParams<{ id: string }>();
  return <MessageThreadView key={id} householdId={id} />;
}
