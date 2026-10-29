"use client";
import { useParams } from "next/navigation";
import { MeetingView } from "@/components/views/MeetingView";

// The page and the workspace's work pane render the same view (components/views).
export default function MeetingDetail() {
  const { id } = useParams<{ id: string }>();
  return <MeetingView key={id} id={id} />;
}
